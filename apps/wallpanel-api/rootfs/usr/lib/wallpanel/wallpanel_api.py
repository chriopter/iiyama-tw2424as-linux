#!/usr/bin/env python3
"""wallpanel-api: Home Assistant integration for the iiyama TW2424AS wallpanel.

- MQTT Discovery: entities appear automatically in Home Assistant
  (display standby + lock, brightness, night shift, volume, buttons, URL, auto reboot, sensors)
- Hardware keys: volume up/down with an on-screen overlay, power key toggles the display

Configuration: /etc/wallpanel/wallpanel.conf (KEY=value, see wallpanel.conf.example)
No listening ports. Diagnosis on the device: wallpanel_api.py --state
"""
import glob
import json
import os
import re
import socket
import subprocess
import threading
import time

CONF_FILE = os.environ.get('WALLPANEL_CONF', '/etc/wallpanel/wallpanel.conf')
BACKLIGHT = '/sys/class/backlight/backlight'
CDP = 'http://127.0.0.1:9222'


def load_conf():
    conf = {}
    if os.path.exists(CONF_FILE):
        for line in open(CONF_FILE):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                conf[k.strip()] = v.strip().strip('"\'')
    return conf


CONF = load_conf()
STATE_FILE = os.environ.get('WALLPANEL_STATE', '/var/lib/wallpanel/api-state.json')


def load_settings():
    try:
        return json.load(open(STATE_FILE))
    except (OSError, ValueError):
        return {}


def save_settings():
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(SETTINGS, f)
    os.replace(tmp, STATE_FILE)


SETTINGS = load_settings()
NAME = CONF.get('DEVICE_NAME', 'Wallpanel')
NODE = re.sub(r'[^a-z0-9_]', '_', CONF.get('DEVICE_ID', socket.gethostname()).lower())
BASE = f'wallpanel/{NODE}'


def sh(*cmd, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=env)


def read(path, default=''):
    try:
        return open(path).read().strip()
    except OSError:
        return default


# --- display -------------------------------------------------------------

def wayland_env():
    env = dict(os.environ, XDG_RUNTIME_DIR='/run/wallpanel')
    socks = sorted(glob.glob('/run/wallpanel/wayland-[0-9]*'))
    socks = [s for s in socks if not s.endswith('.lock')]
    if socks:
        env['WAYLAND_DISPLAY'] = os.path.basename(socks[0])
    return env


class Display:
    """Display control without any modeset (a full output off/on re-initialises the TC358775 bridge
    and shows artefacts), so switching is instant:
    - HA light "Display light" (the screen as a bulb, e.g. for Adaptive Lighting): on/off = standby,
      brightness = backlight, colour temperature = night shift.
    - standby (HA switch "Display"): backlight off + all animations paused, ~6.4 W instead of ~21 W.
    - auto-off timeout (HA number, default 5 min, 0 = never): standby after that long without touch, keys or
      a "Display on" from HA - a safety net if no automation switches it off.
    - power lock (HA switch "Display power lock"): switches the display off; while locked, Display
      on/off from HA and touch wake are ignored (e.g. at night, so a motion automation cannot wake
      it); the power key still works.
    While the screen is dark the touchscreen is grabbed, so a touch only wakes it instead of tapping
    the invisible page.
    - colour temperature (HA number "Night shift"): the compositor's gamma ramp
      (wlr-gamma-control), applied by the VOP's hardware LUT, so it costs no rendering."""
    lock = threading.Lock()
    KELVIN = (1000, 6500)  # 6500 K = neutral (no gamma client, identity ramp); 1000 K as in Adaptive Lighting

    def __init__(self):
        self.max = int(read(f'{BACKLIGHT}/max_brightness', '255') or 255)
        self.brightness = int(SETTINGS.get('brightness', 200))  # last brightness set from HA
        self.kelvin = int(SETTINGS.get('color_temp', self.KELVIN[1]))
        self.backlight_on = True
        self.locked = bool(SETTINGS.get('display_lock', False))
        # stay dark after an api restart or reboot at night (kiosk_loop pauses the page once it is up)
        self.standby = bool(SETTINGS.get('standby', False)) or self.locked
        self.dark_since = None  # time the screen went dark (for the periodic reload)
        self.last_activity = time.time()  # touch, keys, "Display on" from HA (auto-off timer)
        self.touch = None  # evdev device, set by keys_loop
        self.gamma = None  # wallpanel-gamma process holding the gamma ramp
        self.gamma_k = None  # temperature last sent to it

    @property
    def lit(self):
        return self.backlight_on and not self.standby

    def _apply(self):
        self._fade_to(self.brightness if self.lit else 0)
        if self.lit:
            self.dark_since = None
        elif self.dark_since is None:
            self.dark_since = time.time()
        if self.touch:
            try:
                self.touch.ungrab() if self.lit else self.touch.grab()
            except OSError:
                pass

    def set_backlight(self, on):
        with self.lock:
            self.backlight_on = on
            self._apply()

    def set_brightness(self, value):
        """1..max; 0 switches the backlight off."""
        value = max(0, min(self.max, int(value)))
        with self.lock:
            if value:
                self.brightness = value
                SETTINGS['brightness'] = value
                save_settings()
            self.backlight_on = value > 0
            self._apply()

    def activity(self):
        self.last_activity = time.time()

    def set_standby(self, standby):
        if not standby:
            self.activity()
        with self.lock:
            if standby == self.standby:
                return
            self.standby = standby
            SETTINGS['standby'] = standby  # survives api restarts and reboots (e.g. the nightly one)
            save_settings()
            self._apply()
        browser.set_paused(standby)

    def set_locked(self, locked):
        """Display power lock: switches the display off and keeps it off until unlocked."""
        if locked:
            self.set_standby(True)
        with self.lock:
            self.locked = locked
            SETTINGS['display_lock'] = locked
            save_settings()

    def wake(self):
        """Touch / keys: make the screen visible - quickly (own fade time, default 100 ms)."""
        self.activity()
        self._next_fade_ms = int(SETTINGS.get('touch_fade_ms', 100))
        with self.lock:
            self.backlight_on = True
        self.set_standby(False)
        with self.lock:
            self._next_fade_ms = int(SETTINGS.get('touch_fade_ms', 100))
            self._apply()

    _fade_gen = 0

    def _fade_to(self, target):
        """Move the backlight to target, gliding over SETTINGS['fade_ms'] (0 = instantly) in a
        background thread; a newer request cancels a running fade."""
        Display._fade_gen += 1
        gen, ms = Display._fade_gen, int(SETTINGS.get('fade_ms', 400))
        if getattr(self, '_next_fade_ms', None) is not None:  # one-off (wake by touch/keys)
            ms, self._next_fade_ms = self._next_fade_ms, None
        try:
            start = int(read(f'{BACKLIGHT}/brightness', '0') or 0)
        except ValueError:
            start = 0
        if ms <= 0 or start == target:
            self._write_brightness(target)
            return

        def run():
            steps = max(1, ms // 16)  # ~60 updates per second
            for i in range(1, steps + 1):
                if gen != Display._fade_gen:
                    return
                self._write_brightness(round(start + (target - start) * i / steps))
                time.sleep(ms / steps / 1000)
        threading.Thread(target=run, daemon=True).start()

    def _write_brightness(self, value):
        try:
            open(f'{BACKLIGHT}/brightness', 'w').write(str(value))
        except OSError:
            pass

    def set_kelvin(self, value):
        value = max(self.KELVIN[0], min(self.KELVIN[1], int(value)))
        with self.lock:
            if value == self.kelvin:
                return
            self.kelvin = value
            SETTINGS['color_temp'] = value
            save_settings()
        self.ensure_gamma()

    def ensure_gamma(self):
        """Keep the night-shift tint applied. wallpanel-gamma holds the compositor's gamma control for
        the whole session and takes new temperatures on stdin, so changes (e.g. every few minutes from
        Adaptive Lighting) glide without releasing the ramp - no neutral flash. It exits with the
        compositor, so this runs periodically (gamma_loop) and restarts it. 6500 K = neutral: no helper."""
        with self.lock:
            running = self.gamma and self.gamma.poll() is None
            if self.kelvin >= self.KELVIN[1]:
                if running:
                    self._stop_gamma()  # also switches the hardware LUT off
                return
            if not running:
                env = wayland_env()
                if 'WAYLAND_DISPLAY' not in env:  # compositor (re)starting
                    return
                self.gamma = subprocess.Popen(['wallpanel-gamma', '-o', CONF.get('OUTPUT', 'DSI-1'), '-f', '1500'],
                                              stdin=subprocess.PIPE, text=True, env=env, user='wallpanel',
                                              stdout=subprocess.DEVNULL)  # errors to our log
                self.gamma_k = None
            if self.gamma_k != self.kelvin:
                try:
                    self.gamma.stdin.write(f'{self.kelvin}\n')
                    self.gamma.stdin.flush()
                    self.gamma_k = self.kelvin
                    print(f'night shift: {self.kelvin} K', flush=True)
                except OSError:
                    self._stop_gamma()

    def _stop_gamma(self):
        if self.gamma and self.gamma.poll() is None:
            try:
                self.gamma.stdin.close()  # EOF: the helper releases the ramp and exits
                self.gamma.wait(3)
            except (OSError, subprocess.TimeoutExpired):
                self.gamma.kill()
        self.gamma = None

    def state(self):
        return {'on': self.backlight_on, 'standby': self.standby, 'lit': self.lit, 'locked': self.locked,
                'brightness': self.brightness, 'max_brightness': self.max,
                'brightness_pct': round(100 * self.brightness / self.max) if self.backlight_on else 0,
                'color_temp': self.kelvin,
                'night_shift': bool(self.gamma and self.gamma.poll() is None)}


# --- audio ---------------------------------------------------------------

class Volume:
    """Volume 0-100 % on an ALSA mixer control (ES8316: 'DAC', -96..0 dB in 0.5 dB steps; the speaker
    amplifier hangs off the headphone outputs, whose coarse 'Headphone' gain is set once at boot).
    Perceptual curve: linear in dB, 100 % = 0 dB, each % = 0.5 dB (1 % = -49.5 dB), 0 % = control
    minimum (DAC: mute). On the DAC that is exactly one raw step per %. Controls without dB info
    fall back to amixer's percentage."""
    RANGE_DB = 50

    def __init__(self):
        self.control = CONF.get('MIXER_CONTROL', 'DAC')
        self.card = CONF.get('MIXER_CARD', '0')

    def get(self):
        out = sh('amixer', '-c', self.card, 'sget', self.control).stdout
        raw, lim = re.search(r'Playback (-?\d+) \[', out), re.search(r'Limits: Playback (-?\d+)', out)
        db, pct = re.search(r'\[(-?[\d.]+)dB\]', out), re.search(r'\[(\d+)%\]', out)
        if not db:
            return int(pct.group(1)) if pct else 0
        if raw and lim and raw.group(1) == lim.group(1):
            return 0
        return max(1, min(100, round(100 * (1 + float(db.group(1)) / self.RANGE_DB))))

    def set(self, pct, save=True):
        pct = max(0, min(100, int(round(pct))))
        db = -self.RANGE_DB * (1 - pct / 100)
        # 0 % -> minimum ("0" = raw 0); else the dB value, amixer clamps it to the control's range
        if sh('amixer', '-q', '-c', self.card, 'sset', self.control, '--',
              f'{db:.2f}dB' if pct else '0').returncode:
            sh('amixer', '-q', '-c', self.card, 'sset', self.control, f'{pct}%')  # control without dB
        if save:
            SETTINGS['volume'] = pct
            save_settings()
        return self.get()

    def restore(self):
        """Last volume on api start (fresh install: 30 %)."""
        self.set(SETTINGS.get('volume', 30), save=False)


# --- browser (Chrome DevTools Protocol, local only) ----------------------

class Browser:
    def _page(self):
        import urllib.request
        import websocket
        targets = json.load(urllib.request.urlopen(f'{CDP}/json', timeout=3))
        page = next(t for t in targets if t['type'] == 'page')
        return websocket.create_connection(page['webSocketDebuggerUrl'], timeout=5,
                                           suppress_origin=True)

    lock = threading.Lock()

    def call(self, method, **params):
        # One call at a time, and always close the socket: leaked DevTools sessions pile up in
        # Chromium and slow it down until it stops answering.
        with self.lock:
            ws = None
            try:
                ws = self._page()
                ws.send(json.dumps({'id': 1, 'method': method, 'params': params}))
                while True:
                    msg = json.loads(ws.recv())
                    if msg.get('id') == 1:
                        return msg.get('result')
            except Exception as e:  # browser restarting, not reachable
                print('cdp:', e, flush=True)
                return None
            finally:
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def ensure_fullscreen(self):
        """--kiosk only reaches "maximized" under cage (browser UI stays visible): force fullscreen."""
        import urllib.request
        import websocket
        try:
            v = json.load(urllib.request.urlopen(f'{CDP}/json/version', timeout=3))
            page = next(t for t in json.load(urllib.request.urlopen(f'{CDP}/json', timeout=3)) if t['type'] == 'page')
            ws = websocket.create_connection(v['webSocketDebuggerUrl'], timeout=5, suppress_origin=True)
            try:
                ws.send(json.dumps({'id': 1, 'method': 'Browser.getWindowForTarget', 'params': {'targetId': page['id']}}))
                r = json.loads(ws.recv()).get('result', {})
                if r.get('bounds', {}).get('windowState') != 'fullscreen':
                    ws.send(json.dumps({'id': 2, 'method': 'Browser.setWindowBounds',
                                        'params': {'windowId': r['windowId'], 'bounds': {'windowState': 'fullscreen'}}}))
                    ws.recv()
            finally:
                ws.close()
        except Exception:
            pass

    def set_paused(self, paused):
        """Pause all animations while the display is off (SVG/SMIL and CSS/Web animations, shadow DOM
        included), resume on wake. Freezing the page (Page.setWebLifecycleState) is not an option: after
        "frozen" -> "active" Chromium keeps the page hidden and stops rendering."""
        return self.js("""(() => {
          const paused = %s; let n = 0;
          const walk = (root) => {
            root.querySelectorAll('svg').forEach(s => { paused ? s.pauseAnimations() : s.unpauseAnimations(); n++; });
            root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) walk(e.shadowRoot); });
          };
          walk(document);
          document.getAnimations().forEach(a => { paused ? a.pause() : a.play(); n++; });
          window.__wallpanelPaused = paused;
          return n;
        })()""" % ('true' if paused else 'false'))

    def js(self, expr):
        return self.call('Runtime.evaluate', expression=expr, returnByValue=True)

    def reload(self):
        return self.call('Page.reload', ignoreCache=False)

    def navigate(self, url):
        return self.call('Page.navigate', url=url)

    def url(self):
        r = self.js('location.href')
        return (r or {}).get('result', {}).get('value', '')

    def osd_volume(self, pct):
        side = 'left' if CONF.get('OSD_SIDE', 'right') == 'left' else 'right'
        self.js(OSD_JS.replace('__PCT__', str(int(pct))).replace('__SIDE__', side))


OSD_JS = """
(() => {
  // Volume overlay: a slim vertical bar at the screen edge (OSD_SIDE, default right), so the
  // dashboard stays visible. Injected on demand (rebuilt after reloads/navigation). A manual popover
  // sits in the top layer: above HA's dialogs, outside the page layout (no shift).
  let o = document.getElementById('__wallpanel_osd');
  if (!o) {
    o = document.createElement('div'); o.id = '__wallpanel_osd'; o.popover = 'manual';
    o.style.cssText = 'inset:0 auto;__SIDE__:28px;margin:auto 0;width:92px;height:fit-content;' +
      'box-sizing:border-box;border:0;padding:22px 0;border-radius:46px;background:rgba(18,18,18,.9);' +
      'color:#fff;pointer-events:none;backdrop-filter:blur(12px);font:600 26px/1 system-ui,sans-serif;' +
      'box-shadow:0 12px 48px rgba(0,0,0,.5);transition:opacity .25s';  // no display: [popover] hides via display:none
    o.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:18px">' +
      '<span class="val" style="font-variant-numeric:tabular-nums"></span>' +
      '<div style="position:relative;width:18px;height:360px;background:rgba(255,255,255,.22);' +
      'border-radius:9px;overflow:hidden">' +
      '<div class="bar" style="position:absolute;left:0;right:0;bottom:0;background:#03a9f4;border-radius:9px"></div></div>' +
      '<svg width="40" height="40" viewBox="0 0 24 24" fill="#fff">' +
      '<path d="M3 9v6h4l5 5V4L7 9H3z"/><path class="w" d="M16.5 12A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4z"/>' +
      '<path class="w" d="M14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z"/></svg></div>';
    (document.body || document.documentElement).appendChild(o);
  }
  const pct = __PCT__;
  o.querySelector('.bar').style.height = pct + '%';
  o.querySelector('.val').textContent = pct;
  o.querySelectorAll('.w').forEach(w => w.style.opacity = pct ? 1 : .25);
  clearTimeout(window.__wallpanel_osd_t);
  if (o.matches(':popover-open')) o.hidePopover();  // re-show: back on top of anything opened since
  o.style.opacity = 1; o.showPopover();
  window.__wallpanel_osd_t = setTimeout(() => {
    o.style.opacity = 0;
    window.__wallpanel_osd_t = setTimeout(() => o.hidePopover(), 300);
  }, 1500);
  return pct;
})()
"""


# --- system sensors --------------------------------------------------------

_cpu_last = [None]


def cpu_usage():
    """CPU busy in % since the previous call (i.e. averaged over the state interval)."""
    v = [int(x) for x in read('/proc/stat').split('\n', 1)[0].split()[1:]]
    idle, total = v[3] + v[4], sum(v)  # idle + iowait
    last, _cpu_last[0] = _cpu_last[0], (idle, total)
    if not last or total == last[1]:
        return None
    return round(100 * (1 - (idle - last[0]) / (total - last[1])), 1)


def sensors():
    temps = [int(read(p, '0')) / 1000 for p in glob.glob('/sys/class/thermal/thermal_zone*/temp')]
    # nl80211 only (no wireless extensions, so no /proc/net/wireless)
    m = re.search(r'signal:\s*(-?\d+)', sh('iw', 'dev', 'wlan0', 'link').stdout)
    rssi = int(m.group(1)) if m else None
    mem = dict(re.findall(r'(\w+):\s+(\d+)', read('/proc/meminfo')))
    avail, total = int(mem.get('MemAvailable', 0)), max(1, int(mem.get('MemTotal', 1)))
    fs = os.statvfs('/')
    return {
        'temperature': round(max(temps), 1) if temps else None,
        'wifi_rssi': rssi,
        'uptime': int(uptime()),
        'cpu_usage': cpu_usage(),
        'load': float(read('/proc/loadavg', '0').split()[0]),
        'memory_used_pct': round(100 * (1 - avail / total), 1),
        'memory_free': round(avail / 1024),
        'disk_free': round(fs.f_bavail * fs.f_frsize / 2**30, 2),
        'disk_used_pct': round(100 * (1 - fs.f_bavail / max(1, fs.f_blocks)), 1),
    }


# --- actions -------------------------------------------------------------

display, volume, browser = Display(), Volume(), Browser()


def restart_kiosk():
    sh('rc-service', 'wallpanel-kiosk', 'restart')


def reboot():
    sh('reboot')


REBOOT_TIMES = [f'{h:02d}:{m:02d}' for h in range(24) for m in (0, 30)]


def reboot_time():
    """Daily reboot time 'HH:MM' (only used while auto reboot is enabled)."""
    return SETTINGS.get('reboot_time') or CONF.get('REBOOT_TIME') or '04:00'


def reboot_enabled():
    return bool(SETTINGS.get('reboot_enabled', bool(SETTINGS.get('reboot_time') or CONF.get('REBOOT_TIME'))))


def set_reboot_time(value):
    value = (value or '').strip()
    if value not in REBOOT_TIMES:
        raise ValueError('expected one of 00:00, 00:30, ... 23:30')
    SETTINGS['reboot_time'] = value
    save_settings()


def set_reboot_enabled(on):
    SETTINGS['reboot_enabled'] = on
    SETTINGS.setdefault('reboot_time', reboot_time())
    save_settings()


def next_reboot():
    if not reboot_enabled():
        return None
    t = reboot_time()
    h, m = map(int, t.split(':'))
    now = time.localtime()
    ts = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, h, m, 0, 0, 0, -1))
    if ts <= time.time() + 60:
        ts += 86400
    return time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(ts))


def reboot_scheduler():
    """Reboot once a day at the configured time (needs a synced clock)."""
    while True:
        time.sleep(20)
        t = reboot_time()
        if time.time() < 1.7e9 or uptime() < 600 or time.strftime('%H:%M') != t:  # no clock / just booted
            continue
        # maintenance time: updates first (if enabled), then the reboot (if enabled)
        if auto_update():
            print('scheduled update at', t, flush=True)
            install_updates(then_reboot=reboot_enabled())
        elif reboot_enabled():
            print('scheduled reboot at', t, flush=True)
            reboot()
        time.sleep(60)  # never twice in the same minute


# --- system updates (Alpine packages: Chromium, Mesa, ...) ------------------------

UPDATE = {'pending': None, 'last': SETTINGS.get('last_update'), 'result': SETTINGS.get('last_update_result'),
          'running': False}


def auto_update():
    return bool(SETTINGS.get('auto_update', False))


def check_updates():
    """Number of upgradable packages (apk update + simulated upgrade)."""
    if sh('apk', 'update', '-q').returncode != 0:
        return None
    out = subprocess.run(['apk', 'upgrade', '--simulate', '--no-interactive'], capture_output=True, text=True,
                         timeout=300).stdout
    UPDATE['pending'] = sum(1 for line in out.splitlines() if 'Upgrading ' in line)
    return UPDATE['pending']


def install_updates(then_reboot=False):
    """apk upgrade (pinned packages such as our cage stay as they are), then restart the kiosk or reboot."""
    if UPDATE['running']:
        return
    UPDATE['running'] = True
    try:
        before = check_updates()
        r = subprocess.run(['apk', 'upgrade', '--no-interactive'], capture_output=True, text=True, timeout=1800)
        UPDATE['last'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        UPDATE['result'] = f'ok, {before or 0} packages' if r.returncode == 0 else f'failed ({r.returncode})'
        print('update:', UPDATE['result'], (r.stderr or '')[-300:], flush=True)
        SETTINGS['last_update'], SETTINGS['last_update_result'] = UPDATE['last'], UPDATE['result']
        save_settings()
        check_updates()
    finally:
        UPDATE['running'] = False
    if then_reboot:
        reboot()
    elif before:
        restart_kiosk()  # new Chromium/Mesa take effect


def update_checker():
    """Check for pending updates every 6 hours (sensor in HA)."""
    time.sleep(300)
    while True:
        try:
            check_updates()
        except Exception as e:
            print('update check:', e, flush=True)
        time.sleep(6 * 3600)


def uptime():
    return float(read('/proc/uptime', '0').split()[0])


def state():
    return {'display': display.state(), 'volume': volume.get(), 'url': browser.url(),
            'home_url': home_url(), 'home_after': home_after(), 'auto_update': auto_update(),
            'auto_off': int(SETTINGS.get('auto_off', 5)), 'fade_ms': int(SETTINGS.get('fade_ms', 400)),
            'touch_fade_ms': int(SETTINGS.get('touch_fade_ms', 100)),
            'updates_pending': UPDATE['pending'], 'last_update': UPDATE['last'],
            'last_update_result': UPDATE['result'],
            'reboot_time': reboot_time(), 'reboot_enabled': reboot_enabled(), 'next_reboot': next_reboot(),
            **sensors()}


# --- MQTT (Home Assistant discovery) -------------------------------------

# Entity ID suffixes where they differ from the discovery key (the ids automations use; HA ignores
# object_id and derives ids from names otherwise, so fresh installs would get other ids)
ENTITY_IDS = {'screen': 'display', 'auto_off': 'display_auto_off_timeout', 'fade_ms': 'display_fade_time',
              'home_after': 'return_to_home_page_after', 'home_url': 'home_page', 'reboot_enabled': 'auto_reboot',
              'reload': 'reload_page', 'disk_used_pct': 'disk_used'}


class Mqtt:
    def __init__(self):
        import paho.mqtt.client as mqtt
        if hasattr(mqtt, 'CallbackAPIVersion'):  # paho-mqtt >= 2.0
            self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'wallpanel-{NODE}')
        else:  # paho-mqtt 1.x (Alpine v3.24); callbacks below accept both signatures
            self.c = mqtt.Client(client_id=f'wallpanel-{NODE}')
        if CONF.get('MQTT_USER'):
            self.c.username_pw_set(CONF['MQTT_USER'], CONF.get('MQTT_PASSWORD', ''))
        self.c.will_set(f'{BASE}/availability', 'offline', retain=True)
        self.c.on_connect = self.on_connect
        self.c.on_message = self.on_message
        self.c.reconnect_delay_set(1, 60)
        self.c.connect_async(CONF['MQTT_HOST'], int(CONF.get('MQTT_PORT', '1883')))
        self.c.loop_start()

    def device(self):
        return {'identifiers': [f'wallpanel_{NODE}'], 'name': NAME,
                'manufacturer': 'iiyama', 'model': 'ProLite TW2424AS', 'sw_version': os.uname().release}

    def discovery(self):
        dev, avail = self.device(), f'{BASE}/availability'
        common = {'device': dev, 'availability_topic': avail}
        cfg = {
            ('switch', 'screen'): {'name': 'Bildschirm an/aus', 'icon': 'mdi:monitor',
                                   'command_topic': f'{BASE}/screen/set', 'state_topic': f'{BASE}/state',
                                   'value_template': "{{ 'OFF' if value_json.display.standby else 'ON' }}"},
            ('switch', 'display_lock'): {'name': 'Bildschirm gesperrt', 'icon': 'mdi:monitor-lock',
                                         'command_topic': f'{BASE}/display_lock/set', 'state_topic': f'{BASE}/state',
                                         'value_template': "{{ 'ON' if value_json.display.locked else 'OFF' }}"},
            ('number', 'fade_ms'): {'name': 'Bildschirm-Überblendung', 'min': 0, 'max': 3000, 'step': 50,
                                    'unit_of_measurement': 'ms', 'icon': 'mdi:transition',
                                    'command_topic': f'{BASE}/fade_ms/set', 'state_topic': f'{BASE}/state',
                                    'value_template': '{{ value_json.fade_ms }}', 'entity_category': 'config'},
            ('number', 'touch_fade_ms'): {'name': 'Bildschirm-Überblendung bei Berührung', 'min': 0, 'max': 3000,
                                          'step': 10, 'unit_of_measurement': 'ms', 'icon': 'mdi:gesture-tap',
                                          'command_topic': f'{BASE}/touch_fade_ms/set', 'state_topic': f'{BASE}/state',
                                          'value_template': '{{ value_json.touch_fade_ms }}', 'entity_category': 'config'},
            ('number', 'auto_off'): {'name': 'Bildschirm aus nach', 'min': 0, 'max': 240, 'step': 1,
                                     'unit_of_measurement': 'min', 'mode': 'box', 'icon': 'mdi:timer-outline',
                                     'command_topic': f'{BASE}/auto_off/set', 'state_topic': f'{BASE}/state',
                                     'value_template': '{{ value_json.auto_off }}', 'entity_category': 'config'},
            ('light', 'display'): {'name': 'Bildschirm-Beleuchtung', 'schema': 'json', 'icon': 'mdi:monitor-shimmer',
                                   'brightness': True, 'brightness_scale': display.max,
                                   'supported_color_modes': ['color_temp'], 'color_temp_kelvin': True,
                                   'min_kelvin': Display.KELVIN[0], 'max_kelvin': Display.KELVIN[1],
                                   'command_topic': f'{BASE}/display/set', 'state_topic': f'{BASE}/display/state'},
            ('number', 'volume'): {'name': 'Lautstärke', 'min': 0, 'max': 100, 'unit_of_measurement': '%',
                                   'command_topic': f'{BASE}/volume/set', 'state_topic': f'{BASE}/state',
                                   'value_template': '{{ value_json.volume }}', 'icon': 'mdi:volume-high'},
            ('text', 'url'): {'name': 'Seitenadresse', 'command_topic': f'{BASE}/url/set', 'state_topic': f'{BASE}/state',
                              'value_template': '{{ value_json.url }}', 'max': 255, 'icon': 'mdi:web'},
            ('switch', 'reboot_enabled'): {'name': 'Neustart täglich', 'icon': 'mdi:autorenew',
                                           'command_topic': f'{BASE}/reboot_enabled/set', 'state_topic': f'{BASE}/state',
                                           'value_template': "{{ 'ON' if value_json.reboot_enabled else 'OFF' }}",
                                           'entity_category': 'config'},
            ('switch', 'auto_update'): {'name': 'Updates automatisch', 'icon': 'mdi:update',
                                        'command_topic': f'{BASE}/auto_update/set', 'state_topic': f'{BASE}/state',
                                        'value_template': "{{ 'ON' if value_json.auto_update else 'OFF' }}",
                                        'entity_category': 'config'},
            ('button', 'install_updates'): {'name': 'Updates installieren', 'command_topic': f'{BASE}/install_updates',
                                            'icon': 'mdi:download', 'entity_category': 'config'},
            ('sensor', 'updates_pending'): {'name': 'Updates verfügbar', 'state_topic': f'{BASE}/state',
                                            'value_template': '{{ value_json.updates_pending }}',
                                            'icon': 'mdi:package-up', 'entity_category': 'diagnostic'},
            ('sensor', 'last_update'): {'name': 'Letztes Update', 'state_topic': f'{BASE}/state',
                                        'value_template': '{{ value_json.last_update }}', 'device_class': 'timestamp',
                                        'json_attributes_topic': f'{BASE}/state',
                                        'json_attributes_template': '{{ {"result": value_json.last_update_result} | tojson }}',
                                        'icon': 'mdi:package-variant-closed-check', 'entity_category': 'diagnostic'},
            ('select', 'reboot_time'): {'name': 'Wartungszeit', 'options': REBOOT_TIMES,
                                        'command_topic': f'{BASE}/reboot_time/set', 'state_topic': f'{BASE}/state',
                                        'value_template': '{{ value_json.reboot_time }}',
                                        'icon': 'mdi:wrench-clock', 'entity_category': 'config'},
            ('text', 'home_url'): {'name': 'Startseite', 'command_topic': f'{BASE}/home_url/set',
                                   'state_topic': f'{BASE}/state', 'value_template': '{{ value_json.home_url }}',
                                   'max': 255, 'icon': 'mdi:home-outline', 'entity_category': 'config'},
            ('number', 'home_after'): {'name': 'Startseite laden nach', 'min': 0, 'max': 1440, 'step': 5,
                                       'unit_of_measurement': 'min', 'mode': 'box', 'icon': 'mdi:home-clock-outline',
                                       'command_topic': f'{BASE}/home_after/set', 'state_topic': f'{BASE}/state',
                                       'value_template': '{{ value_json.home_after }}', 'entity_category': 'config'},
            ('sensor', 'next_reboot'): {'name': 'Nächster Neustart', 'state_topic': f'{BASE}/state',
                                        'value_template': '{{ value_json.next_reboot }}', 'device_class': 'timestamp',
                                        'icon': 'mdi:calendar-clock', 'entity_category': 'diagnostic'},
            ('button', 'reload'): {'name': 'Seite neu laden', 'command_topic': f'{BASE}/reload', 'icon': 'mdi:refresh'},
            ('button', 'restart_kiosk'): {'name': 'Browser neu starten', 'command_topic': f'{BASE}/restart_kiosk',
                                          'icon': 'mdi:web-refresh', 'entity_category': 'config'},
            ('button', 'reboot'): {'name': 'Neu starten', 'command_topic': f'{BASE}/reboot', 'device_class': 'restart',
                                   'icon': 'mdi:restart', 'entity_category': 'config'},
        }
        for key, name, unit, dc, icon, enabled in (  # enabled: False = disabled by default (noise for most users)
                ('temperature', 'Prozessortemperatur', '°C', 'temperature', None, True),
                ('wifi_rssi', 'WLAN-Signal', 'dBm', 'signal_strength', None, True),
                ('uptime', 'Betriebszeit', 's', 'duration', 'mdi:clock-start', True),
                ('cpu_usage', 'Prozessorauslastung', '%', None, 'mdi:cpu-64-bit', True),
                ('load', 'Systemlast', None, None, 'mdi:gauge', False),
                ('memory_used_pct', 'Arbeitsspeicher belegt', '%', None, 'mdi:memory', True),
                ('memory_free', 'Arbeitsspeicher frei', 'MiB', 'data_size', 'mdi:memory', False),
                ('disk_free', 'Speicherplatz frei', 'GiB', 'data_size', 'mdi:harddisk', True),
                ('disk_used_pct', 'Speicherplatz belegt', '%', None, 'mdi:harddisk', False)):
            c = {'name': name, 'state_topic': f'{BASE}/state',
                 'value_template': '{{ value_json.%s }}' % key, 'entity_category': 'diagnostic',
                 'state_class': 'measurement'}
            if not enabled:
                c['enabled_by_default'] = False
            if unit:
                c['unit_of_measurement'] = unit
            if dc:
                c['device_class'] = dc
            if icon:
                c['icon'] = icon
            cfg[('sensor', key)] = c
        cfg[('sensor', 'uptime')]['suggested_unit_of_measurement'] = 'h'  # shown as 6.2 h instead of 22145 s
        for (comp, obj), c in cfg.items():
            c.update(common, unique_id=f'wallpanel_{NODE}_{obj}',
                     default_entity_id=f'{comp}.{NODE}_{ENTITY_IDS.get(obj, obj)}')
            self.c.publish(f'homeassistant/{comp}/{NODE}/{obj}/config', json.dumps(c), retain=True)
        for comp, obj in (('text', 'reboot_time'), ('number', 'brightness'), ('number', 'color_temp')):  # earlier versions
            self.c.publish(f'homeassistant/{comp}/{NODE}/{obj}/config', '', retain=True)

    def on_connect(self, c, userdata, flags, rc, props=None):
        code = getattr(rc, 'value', rc)
        print(f'mqtt connect to {CONF["MQTT_HOST"]}: {"ok" if code == 0 else f"refused ({rc})"}', flush=True)
        if code != 0:
            return
        self.discovery()
        c.subscribe(f'{BASE}/+/set')
        for t in ('reload', 'restart_kiosk', 'reboot', 'install_updates'):
            c.subscribe(f'{BASE}/{t}')
        c.subscribe('homeassistant/status')
        c.publish(f'{BASE}/availability', 'online', retain=True)
        self.publish_state()

    def on_message(self, c, userdata, msg):
        topic, payload = msg.topic, msg.payload.decode(errors='replace')
        if topic == 'homeassistant/status' and payload == 'online':
            self.discovery()
            c.publish(f'{BASE}/availability', 'online', retain=True)
        elif topic.endswith('/display/set'):
            # the screen as a light bulb (HA light, e.g. driven by Adaptive Lighting): on/off = standby,
            # brightness = backlight, colour temperature = night shift
            cmd = json.loads(payload)
            if 'color_temp' in cmd:  # Kelvin (color_temp_kelvin)
                display.set_kelvin(cmd['color_temp'])
            if cmd.get('brightness'):
                display.set_brightness(cmd['brightness'])
            if cmd.get('state') in ('ON', 'OFF'):
                if display.locked:
                    print('display locked: ignoring light', cmd['state'], flush=True)
                else:
                    display.set_standby(cmd['state'] == 'OFF')
        elif topic.endswith('/brightness/set'):
            display.set_brightness(round(float(payload) * display.max / 100))
        elif topic.endswith('/color_temp/set'):
            display.set_kelvin(float(payload))
        elif topic.endswith('/screen/set'):
            if display.locked:
                print('display locked: ignoring', payload, flush=True)
            else:
                display.set_standby(payload.strip().upper() == 'OFF')  # ON also resets the auto-off timer
        elif topic.endswith('/touch_fade_ms/set'):
            SETTINGS['touch_fade_ms'] = max(0, min(3000, int(float(payload))))
            save_settings()
        elif topic.endswith('/fade_ms/set'):
            SETTINGS['fade_ms'] = max(0, min(3000, int(float(payload))))
            save_settings()
        elif topic.endswith('/auto_off/set'):
            SETTINGS['auto_off'] = max(0, int(float(payload)))
            save_settings()
        elif topic.endswith('/display_lock/set'):
            display.set_locked(payload.strip().upper() == 'ON')
        elif topic.endswith('/home_url/set'):
            try:
                set_home_url(payload)
            except ValueError as e:
                print('home_url:', e, flush=True)
        elif topic.endswith('/home_after/set'):
            SETTINGS['home_after'] = max(0, int(float(payload)))
            save_settings()
        elif topic.endswith('/auto_update/set'):
            SETTINGS['auto_update'] = payload.strip().upper() == 'ON'
            save_settings()
        elif topic.endswith('/install_updates'):
            threading.Thread(target=install_updates, daemon=True).start()
        elif topic.endswith('/reboot_enabled/set'):
            set_reboot_enabled(payload.strip().upper() == 'ON')
        elif topic.endswith('/volume/set'):
            volume.set(float(payload))
        elif topic.endswith('/url/set'):
            if re.match(r'https?://', payload.strip()):  # never file:, chrome:, javascript: (config holds secrets)
                browser.navigate(payload.strip())
            else:
                print('url: only http(s) URLs are allowed', flush=True)
        elif topic.endswith('/reboot_time/set'):
            try:
                set_reboot_time(payload)
            except ValueError as e:
                print('reboot_time:', e, flush=True)
        elif topic.endswith('/reload'):
            browser.reload()
        elif topic.endswith('/restart_kiosk'):
            restart_kiosk()
        elif topic.endswith('/reboot'):
            reboot()
        self.publish_state()

    def publish_state(self):
        d = display.state()
        self.c.publish(f'{BASE}/display/state', json.dumps(
            {'state': 'OFF' if d['standby'] else 'ON', 'brightness': d['brightness'],
             'color_mode': 'color_temp', 'color_temp': d['color_temp']}), retain=True)
        self.c.publish(f'{BASE}/state', json.dumps(state()), retain=True)


# --- kiosk: fullscreen + auto-login -------------------------------------------------------

LOGIN_JS = """
(() => {
  const deepAll = (sel, root = document, out = []) => {
    root.querySelectorAll(sel).forEach(e => out.push(e));
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) deepAll(sel, e.shadowRoot, out); });
    return out;
  };
  const ins = deepAll('input');
  const user = ins.find(i => i.name === 'username' || i.autocomplete === 'username');
  const pw = ins.find(i => i.type === 'password');
  if (!user || !pw) return 'no form';
  const set = (el, v) => { el.focus(); el.value = v;
    el.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
    el.dispatchEvent(new Event('change', {bubbles: true, composed: true})); };
  set(user, %s); set(pw, %s);
  // "Keep me logged in": HA then stores a refresh token and the kiosk stays logged in
  deepAll('ha-checkbox, input[type=checkbox]').forEach(c => { if (!c.checked) c.click(); });
  const btn = deepAll('ha-button, mwc-button, button').find(b => /log ?in|anmelden/i.test(b.textContent));
  if (!btn) return 'no button';
  btn.click();
  return 'submitted';
})()"""


_last_reload = [0.0]


def memory_watch():
    """While the screen is dark: reload the page if the JS heap or the system memory runs full, so
    long uptimes never exhaust RAM (at most once per hour, never while someone looks at it)."""
    heap = ((browser.js('performance.memory.usedJSHeapSize') or {}).get('result', {}).get('value') or 0) / 2**20
    mem = dict(re.findall(r'(\w+):\s+(\d+)', read('/proc/meminfo')))
    avail = 100 * int(mem.get('MemAvailable', 1)) / max(1, int(mem.get('MemTotal', 1)))
    if (heap > float(CONF.get('RELOAD_HEAP_MB', '350')) or avail < float(CONF.get('RELOAD_MEM_PCT', '15'))) \
            and time.time() - _last_reload[0] > 3600:
        print(f'memory watch: heap {heap:.0f} MiB, {avail:.0f}% RAM free -> reload', flush=True)
        _last_reload[0] = time.time()
        browser.reload()


def home_url():
    """Start page of the kiosk (HA text "Home page"; default: URL from wallpanel.conf)."""
    return SETTINGS.get('home_url') or CONF.get('URL', '')


def set_home_url(value):
    value = (value or '').strip()
    if not re.match(r'https?://', value):
        raise ValueError('expected an http(s) URL')
    if value != home_url():
        SETTINGS['home_url'] = value
        save_settings()
        restart_kiosk()  # the kiosk starts with it and locks navigation to its origin


def home_after():
    """Minutes the screen has to be dark before the kiosk returns to the start page (0 = never)."""
    return int(SETTINGS.get('home_after', 60))


def dark_reload():
    """Once the screen has been dark for home_after() minutes, load the start page in the background:
    back on the main page with a fresh JS heap when it is switched on again, nobody sees the reload."""
    since, after = display.dark_since, home_after() * 60
    if after and since and time.time() - since > after and _last_reload[0] < since + after:
        print('dark for', home_after(), 'min -> start page', flush=True)
        _last_reload[0] = time.time()
        browser.navigate(home_url())


def kiosk_loop():
    """Keep the kiosk fullscreen and log it in to Home Assistant whenever it shows the login page
    (KIOSK_USER/KIOSK_PASSWORD). Backs off after failed logins so a wrong password never triggers
    HA's IP ban."""
    user, pw = CONF.get('KIOSK_USER'), CONF.get('KIOSK_PASSWORD')
    failures = 0
    while True:
        time.sleep(15)
        browser.ensure_fullscreen()
        if display.standby:
            browser.set_paused(True)  # again: new cards/SVGs or a reload start their animations
        if not display.lit:
            memory_watch()
            dark_reload()
        if not (user and pw):
            continue
        if '/auth/authorize' not in (browser.url() or ''):
            failures = 0
            continue
        r = browser.js(LOGIN_JS % (json.dumps(user), json.dumps(pw)))
        result = (r or {}).get('result', {}).get('value')
        time.sleep(15)
        if '/auth/authorize' in (browser.url() or ''):
            failures += 1
            print(f'kiosk auto-login: {result}, still on the login page (attempt {failures})', flush=True)
            time.sleep(min(3600, 60 * 2 ** failures))
        else:
            print('kiosk auto-login: ok', flush=True)
            failures = 0


def auto_off_loop():
    """Safety net: standby after the auto-off timeout without touch, keys or "Display on" from HA."""
    while True:
        time.sleep(15)
        auto_off = int(SETTINGS.get('auto_off', 5))
        if auto_off and display.lit and time.time() - display.last_activity > auto_off * 60:
            print(f'no activity for {auto_off} min -> display off', flush=True)
            display.set_standby(True)
            if MQ:
                MQ.publish_state()


def gamma_loop():
    """Keep the night shift applied across kiosk/compositor restarts."""
    while True:
        display.ensure_gamma()
        time.sleep(5)


# --- hardware keys ---------------------------------------------------------

def keys_loop():
    import evdev
    from evdev import ecodes as e
    devs = [evdev.InputDevice(p) for p in evdev.list_devices()]
    touch = next((d for d in devs if 'Touch' in d.name), None)
    devs = [d for d in devs if d.name in ('adc-keys', 'gpio-keys')]
    if touch:
        display.touch = touch
        devs.append(touch)
    step = int(CONF.get('VOLUME_STEP', '5'))
    import selectors
    sel = selectors.DefaultSelector()
    for d in devs:
        sel.register(d, selectors.EVENT_READ)
    while True:
        for key, _ in sel.select():
            steps = 0  # Vol± events read in one go (autorepeat): applied once, no lag behind the key
            for ev in key.fileobj.read():
                if key.fileobj is touch:
                    display.activity()
                    # grabbed while off: the first touch only wakes the display
                    if ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH and ev.value == 1 and not display.lit:
                        print(f'touch on dark screen (locked={display.locked})', flush=True)
                        if not display.locked:
                            display.wake()
                            if MQ:
                                MQ.publish_state()
                    continue
                if ev.type != e.EV_KEY or ev.value == 0:  # press and autorepeat
                    continue
                display.activity()
                if ev.code in (e.KEY_VOLUMEUP, e.KEY_VOLUMEDOWN):
                    steps += 1 if ev.code == e.KEY_VOLUMEUP else -1
                elif ev.code == e.KEY_POWER and ev.value == 1:
                    display.wake() if not display.lit else display.set_standby(True)
                    if MQ:
                        MQ.publish_state()
            if steps:
                if not display.lit:  # first press on a dark screen: wake and show the level only
                    display.wake()
                    v = volume.get()
                else:
                    v = volume.set(volume.get() + steps * step)
                browser.osd_volume(v)
                if MQ:
                    MQ.publish_state()


MQ = None


def main():
    global MQ
    if CONF.get('MQTT_HOST'):
        MQ = Mqtt()
    display.set_backlight(True)  # saved brightness (stays 0 while in standby)
    volume.restore()
    sh('pkill', '-x', 'wlsunset')  # older versions: would hold the gamma control
    sh('pkill', '-x', 'wallpanel-gamma')  # left over from a previous run
    threading.Thread(target=gamma_loop, daemon=True).start()
    threading.Thread(target=keys_loop, daemon=True).start()
    threading.Thread(target=reboot_scheduler, daemon=True).start()
    threading.Thread(target=update_checker, daemon=True).start()
    threading.Thread(target=kiosk_loop, daemon=True).start()
    threading.Thread(target=auto_off_loop, daemon=True).start()
    if MQ:
        def periodic():
            while True:
                time.sleep(int(CONF.get('STATE_INTERVAL', '60')))
                MQ.publish_state()
        threading.Thread(target=periodic, daemon=True).start()
    threading.Event().wait()


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--state']:
        print(json.dumps(state(), indent=1))
    else:
        main()
