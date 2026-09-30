#!/usr/bin/env python3
"""wallpanel-api: Home Assistant integration for the iiyama TW2424AS wallpanel.

- MQTT Discovery: entities appear automatically in Home Assistant
  (display standby + lock, brightness, night shift, volume, buttons, URL, auto reboot, sensors)
- Hardware keys: volume up/down with an on-screen overlay, power key toggles the display
- On-screen update page (HA switch): Alpine packages and kernel (A/B); a kernel install needs a touch

Configuration: /etc/wallpanel/wallpanel.conf (KEY=value, see wallpanel.conf.example)
Listens only on 127.0.0.1:8099 (on-screen update page, UpdatePage). Diagnosis on the device: wallpanel_api.py --state
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
        self.last_touch = 0.0  # last touch on the lit screen (only the touchscreen, never HA)
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

    CT_SCALE = (50, 150, 83)  # %, min/max/default of the colour calibration

    def ct_scale(self):
        return int(SETTINGS.get('ct_scale', self.CT_SCALE[2]))

    def set_ct_scale(self, value):
        SETTINGS['ct_scale'] = max(self.CT_SCALE[0], min(self.CT_SCALE[1], int(float(value))))
        save_settings()
        self.ensure_gamma()  # re-applied right away

    def effective_kelvin(self):
        """Colour calibration (HA number "Farbton-Abgleich"): the panel's tint looked warmer than a bulb of
        the same nominal temperature (3000 K like 2700 K), so the distance from neutral is scaled in the mired
        domain - 6500 K stays neutral. Default 83 %: 3000 K -> ~3300 K. HA keeps seeing the requested value."""
        neutral = 1e6 / self.KELVIN[1]
        mired = neutral + (1e6 / self.kelvin - neutral) * self.ct_scale() / 100
        return max(self.KELVIN[0], min(self.KELVIN[1], round(1e6 / mired)))

    def ensure_gamma(self):
        """Keep the night-shift tint applied. wallpanel-gamma holds the compositor's gamma control for
        the whole session and takes new temperatures on stdin, so changes (e.g. every few minutes from
        Adaptive Lighting) glide without releasing the ramp - no neutral flash. It exits with the
        compositor, so this runs periodically (gamma_loop) and restarts it. 6500 K = neutral: no helper."""
        with self.lock:
            running = self.gamma and self.gamma.poll() is None
            k = self.effective_kelvin()
            if k >= self.KELVIN[1]:
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
            if self.gamma_k != k:
                try:
                    self.gamma.stdin.write(f'{k}\n')
                    self.gamma.stdin.flush()
                    self.gamma_k = k
                    print(f'night shift: {self.kelvin} K (effective {k} K, calibration {self.ct_scale()} %)', flush=True)
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
                'color_temp': self.kelvin, 'color_temp_effective': self.effective_kelvin(), 'ct_scale': self.ct_scale(),
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


def cpu_usage(prev=_cpu_last):
    """CPU busy in % since the previous call with the same prev holder (i.e. averaged over the state interval)."""
    v = [int(x) for x in read('/proc/stat').split('\n', 1)[0].split()[1:]]
    idle, total = v[3] + v[4], sum(v)  # idle + iowait
    last, prev[0] = prev[0], (idle, total)
    if not last or total == last[1]:
        return None
    return round(100 * (1 - (idle - last[0]) / (total - last[1])), 1)


def soc_temperature():
    temps = [int(read(p, '0')) / 1000 for p in glob.glob('/sys/class/thermal/thermal_zone*/temp')]
    return round(max(temps), 1) if temps else None


def wifi_rssi():
    # nl80211 only (no wireless extensions, so no /proc/net/wireless)
    m = re.search(r'signal:\s*(-?\d+)', sh('iw', 'dev', 'wlan0', 'link').stdout)
    return int(m.group(1)) if m else None


def memory():
    """(available kB, used %)"""
    mem = dict(re.findall(r'(\w+):\s+(\d+)', read('/proc/meminfo')))
    avail, total = int(mem.get('MemAvailable', 0)), max(1, int(mem.get('MemTotal', 1)))
    return avail, round(100 * (1 - avail / total), 1)


def sensors():
    avail, used = memory()
    fs = os.statvfs('/')
    return {
        'temperature': soc_temperature(),
        'wifi_rssi': wifi_rssi(),
        'uptime': int(uptime()),
        'cpu_usage': cpu_usage(),
        'load': float(read('/proc/loadavg', '0').split()[0]),
        'memory_used_pct': used,
        'memory_free': round(avail / 1024),
        'disk_free': round(fs.f_bavail * fs.f_frsize / 2**30, 2),
        'disk_used_pct': round(100 * (1 - fs.f_bavail / max(1, fs.f_blocks)), 1),
    }


class History:
    """System values for the graphs on the update page: one sample every STEP s, SPAN s kept in memory as a
    ring of compact arrays (~0.3 MB); nothing is written to the eMMC."""
    STEP, SPAN, POINTS = 10, 86400, 240
    KEYS = ('cpu', 'temp', 'mem', 'rssi', 'backlight')  # int16, x10 for cpu/temp/mem; NONE = no value
    NONE = -32768

    def __init__(self):
        from array import array
        self.n, self.i, self.count = self.SPAN // self.STEP, 0, 0
        self.v = {k: array('h', [self.NONE]) * self.n for k in self.KEYS}
        self.cpu_prev = [None]
        self.now = {}

    def sample(self):
        backlight = int(read(f'{BACKLIGHT}/brightness', '0') or 0)
        now = {'cpu': cpu_usage(self.cpu_prev), 'temp': soc_temperature(), 'mem': memory()[1],
               'rssi': wifi_rssi(), 'backlight': round(100 * backlight / max(1, display.max))}
        for k, x in now.items():
            self.v[k][self.i] = self.NONE if x is None else round(x * (10 if k in ('cpu', 'temp', 'mem') else 1))
        self.now = now  # current values for the page (the graphs show averages)
        self.i, self.count = (self.i + 1) % self.n, min(self.n, self.count + 1)

    def series(self, span):
        """Last span seconds as POINTS averages per key (None = no data yet, e.g. after an api restart).
        Cached until the next sample, so a page polling every 5 s costs nothing in between."""
        key = (span, self.i, self.count)
        if getattr(self, '_cache', (None,))[0] == key:
            return self._cache[1]
        m = max(1, min(self.n, span // self.STEP))
        per = max(1, m // self.POINTS)
        out = {}
        for k in self.KEYS:
            a, scale, pts = self.v[k], 10 if k in ('cpu', 'temp', 'mem') else 1, []
            for b in range(m - m // per * per, m, per):  # oldest bucket first
                vals = [a[(self.i - m + j) % self.n] for j in range(b, b + per) if m - j <= self.count]
                vals = [x for x in vals if x != self.NONE]
                pts.append(round(sum(vals) / len(vals) / scale, 1) if vals else None)
            out[k] = pts
        self._cache = (key, out)
        return out

    def loop(self):
        while True:
            try:
                self.sample()
            except Exception as e:
                print('history:', e, flush=True)
            time.sleep(self.STEP)


history = History()


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
        if kernel_installing():  # it reboots by itself
            print('maintenance skipped: kernel update running', flush=True)
            time.sleep(60)
            continue
        # maintenance time: updates first (if enabled), then the reboot (if enabled)
        if auto_update():
            print('scheduled update (' + ', '.join(AUTO_PACKAGES) + ') at', t, flush=True)
            install_updates(then_reboot=reboot_enabled())
        elif reboot_enabled():
            print('scheduled reboot at', t, flush=True)
            reboot()
        time.sleep(60)  # never twice in the same minute


# --- system updates (Alpine packages: Chromium, Mesa, ...) ------------------------

# Automatic updates (HA switch, at the maintenance time) and the HA button only touch these packages (plus
# the dependencies apk needs for them); everything else is upgraded by hand from the on-screen update page.
AUTO_PACKAGES = ('chromium', 'shairport-sync')
UPDATE = {'pending': None, 'packages': [], 'checked': None, 'last': SETTINGS.get('last_update'),
          'result': SETTINGS.get('last_update_result'), 'running': False, 'mode': None, 'log': []}


def auto_update():
    return bool(SETTINGS.get('auto_update', False))


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def check_updates():
    """Number of upgradable packages (apk update + simulated upgrade); the list goes to the update page."""
    if sh('apk', 'update', '-q').returncode != 0:
        return None
    out = subprocess.run(['apk', 'upgrade', '--simulate', '--no-interactive'], capture_output=True, text=True,
                         timeout=300).stdout
    # "(1/3) Upgrading chromium (140.0-r0 -> 141.0-r0)"
    UPDATE['packages'] = [{'name': m.group(1), 'old': m.group(2), 'new': m.group(3)}
                          for m in re.finditer(r'Upgrading (\S+) \((\S+) -> (\S+)\)', out)]
    UPDATE['pending'] = sum(1 for line in out.splitlines() if 'Upgrading ' in line)
    UPDATE['checked'] = now_iso()
    return UPDATE['pending']


def auto_packages():
    """AUTO_PACKAGES that are installed (shairport-sync is optional)."""
    return sh('apk', 'info', '-e', *AUTO_PACKAGES).stdout.split()


def install_updates(then_reboot=False, full=False):
    """full: apk upgrade of everything (update page); otherwise only AUTO_PACKAGES (apk add -u). Pinned
    packages such as our cage stay as they are. Then restart the kiosk or reboot."""
    if UPDATE['running'] or kernel_installing():
        print('update: already running', flush=True)
        return
    UPDATE.update(running=True, mode='full' if full else 'auto', log=[])
    upgraded = []
    try:
        pkgs = [] if full else auto_packages()
        cmd = ['apk', 'upgrade', '--no-interactive'] if full else ['apk', 'add', '-u', '--no-interactive', *pkgs]
        if not full and not pkgs:
            cmd = ['true']
        if sh('apk', 'update', '-q').returncode != 0:
            UPDATE['log'].append('apk update fehlgeschlagen (Netzwerk?)')
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 1800
        for line in p.stdout:  # progress for the update page
            line = line.rstrip()
            m = re.search(r'Upgrading (\S+) ', line)
            if m:
                upgraded.append(m.group(1))
            UPDATE['log'] = (UPDATE['log'] + [line])[-12:]
            if time.time() > deadline:
                p.kill()
        rc = p.wait()
        UPDATE['last'] = now_iso()
        what = '' if full else f" ({', '.join(pkgs)})"
        UPDATE['result'] = f'ok, {len(upgraded)} packages{what}' if rc == 0 else f'failed ({rc})'
        print('update:', UPDATE['result'], '' if rc == 0 else '\n'.join(UPDATE['log'])[-300:], flush=True)
        SETTINGS['last_update'], SETTINGS['last_update_result'] = UPDATE['last'], UPDATE['result']
        save_settings()
        check_updates()
    except Exception as e:
        UPDATE['result'] = f'failed ({e})'
        print('update:', e, flush=True)
    finally:
        UPDATE['running'] = False
    if MQ:
        MQ.publish_state()
    if then_reboot:
        reboot()
    elif upgraded:
        if 'shairport-sync' in upgraded and sh('rc-service', '--exists', 'wallpanel-airplay').returncode == 0:
            sh('rc-service', 'wallpanel-airplay', 'restart')
        restart_kiosk()  # new Chromium/Mesa take effect


# --- kernel updates (wallpanel-update, A/B slots) ------------------------------
# Only ever started by a touch on the on-screen update page (UpdatePage), never from MQTT/Home Assistant.

WALLPANEL_UPDATE = os.environ.get('WALLPANEL_UPDATE', '/usr/sbin/wallpanel-update')
KERNEL_LOG = '/var/log/wallpanel-kernel-update.log'
KERNEL = {'info': None, 'checked': None, 'checking': False, 'error': None}


def running_slot():
    m = re.search(r'wallpanel\.slot=([AB])', read('/proc/cmdline'))
    return m.group(1) if m else None


def check_kernel():
    """wallpanel-update check --json: running slot/kernel, available release, last result."""
    KERNEL['checking'] = True
    try:
        r = subprocess.run([WALLPANEL_UPDATE, 'check', '--json'], capture_output=True, text=True, timeout=120)
        KERNEL['info'] = json.loads(r.stdout)
        KERNEL['error'] = None
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        KERNEL['info'] = None
        KERNEL['error'] = 'Kernel-Updates werden von diesem System noch nicht unterstützt' \
            if isinstance(e, ValueError) else f'Prüfung fehlgeschlagen ({e.__class__.__name__})'
        print('kernel check:', e, flush=True)
    finally:
        KERNEL['checked'] = now_iso()
        KERNEL['checking'] = False


def kernel_installing():
    return sh('pgrep', '-f', f'{os.path.basename(WALLPANEL_UPDATE)} install-release').returncode == 0


def install_kernel():
    """Start wallpanel-update install-release detached (own session, output to KERNEL_LOG): it downloads,
    verifies, writes slot B, reboots once into it and promotes or falls back to A - it must survive an api
    restart, so it does not hang on our pipes."""
    if kernel_installing() or UPDATE['running']:
        return False
    log = open(KERNEL_LOG, 'w')
    log.write(f'{now_iso()} Kernel-Update gestartet\n')
    log.flush()
    subprocess.Popen([WALLPANEL_UPDATE, 'install-release'], stdin=subprocess.DEVNULL, stdout=log,
                     stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    print('kernel update started from the update page', flush=True)
    return True


def kernel_status():
    info = KERNEL['info'] or {}
    try:
        log = open(KERNEL_LOG).read().splitlines()[-12:]
    except OSError:
        log = []
    return {'running_slot': info.get('running_slot') or running_slot(),
            'running_kernel': info.get('running_kernel') or os.uname().release,
            'available': info.get('available'), 'update_available': bool(info.get('update_available')),
            'last_result': info.get('last_result'), 'checked': KERNEL['checked'], 'checking': KERNEL['checking'],
            'error': KERNEL['error'], 'running': kernel_installing(), 'log': log}


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
            'last_update_result': UPDATE['result'], 'update_page': update_page.on,
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
            ('number', 'ct_scale'): {'name': 'Farbton-Abgleich', 'min': Display.CT_SCALE[0], 'max': Display.CT_SCALE[1],
                                     'step': 1, 'unit_of_measurement': '%', 'icon': 'mdi:palette-swatch',
                                     'command_topic': f'{BASE}/ct_scale/set', 'state_topic': f'{BASE}/state',
                                     'value_template': '{{ value_json.display.ct_scale }}', 'entity_category': 'config'},
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
            ('switch', 'update_page'): {'name': 'Update-Seite anzeigen', 'icon': 'mdi:update',
                                        'command_topic': f'{BASE}/update_page/set', 'state_topic': f'{BASE}/state',
                                        'value_template': "{{ 'ON' if value_json.update_page else 'OFF' }}"},
            ('switch', 'auto_update'): {'name': 'Auto-Update Apps', 'icon': 'mdi:update',
                                        'command_topic': f'{BASE}/auto_update/set', 'state_topic': f'{BASE}/state',
                                        'value_template': "{{ 'ON' if value_json.auto_update else 'OFF' }}",
                                        'entity_category': 'config'},
            ('button', 'install_updates'): {'name': 'Apps aktualisieren', 'command_topic': f'{BASE}/install_updates',
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
        elif topic.endswith('/ct_scale/set'):
            display.set_ct_scale(payload)
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
        elif topic.endswith('/install_updates'):  # AUTO_PACKAGES only; the full upgrade is on the update page
            threading.Thread(target=install_updates, daemon=True).start()
        elif topic.endswith('/update_page/set'):  # shows the page only; kernel installs need a touch there
            update_page.show() if payload.strip().upper() == 'ON' else update_page.close()
        elif topic.endswith('/reboot_enabled/set'):
            set_reboot_enabled(payload.strip().upper() == 'ON')
        elif topic.endswith('/volume/set'):
            volume.set(float(payload))
        elif topic.endswith('/url/set'):
            if UpdatePage.is_local(payload.strip()):  # only via the switch
                print('url: the update page is only shown via its switch', flush=True)
            elif re.match(r'https?://', payload.strip()):  # never file:, chrome:, javascript: (config holds secrets)
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
    if not re.match(r'https?://', value) or UpdatePage.is_local(value):
        raise ValueError('expected an http(s) URL (not the update page)')
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
    if update_page.on:  # it closes itself after UpdatePage.IDLE
        return
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


# --- on-screen update page (127.0.0.1 only) -------------------------------------

class UpdatePage:
    """HA switch "Update-Seite anzeigen": the kiosk shows a local page with the pending Alpine updates and
    the kernel (A/B) release; off (or IDLE without touch) returns to the page shown before.
    Served on 127.0.0.1 only (not reachable from the network). POSTs need the random token that is only
    embedded in the page the kiosk loads (other local processes cannot start anything), and the Host header
    must be ours (no DNS rebinding). A kernel install additionally needs a real touch on the lit screen in
    the last seconds (display.last_touch comes from the touchscreen only), so neither MQTT/HA nor anything
    remote can start one."""
    PORT = 8099
    URL = f'http://127.0.0.1:{PORT}/'
    IDLE = 600  # s without touch -> close
    TOUCH_WINDOW = 20  # s between the touch and the kernel install request

    def __init__(self):
        import secrets
        self.on = False
        self.since = 0.0
        self.back = None  # page shown before
        self.token = secrets.token_urlsafe(24)
        self.checking = False

    @classmethod
    def is_local(cls, url):
        from urllib.parse import urlsplit
        try:
            return urlsplit(url).port == cls.PORT
        except ValueError:
            return False

    def show(self):
        if not self.on:
            url = browser.url()
            self.back = url if re.match(r'https?://', url or '') and not self.is_local(url) else None
            self.on, self.since = True, time.time()
            print('update page: shown', flush=True)
        if not display.locked:
            display.set_standby(False)
        browser.navigate(self.URL)
        self.check()

    def open_by_touch(self):
        """TapGesture: same state as the HA switch (HA shows it on)."""
        self.show()
        if MQ:
            MQ.publish_state()

    def close(self):
        if not self.on:
            return
        self.on = False
        print('update page: closed', flush=True)
        browser.navigate(self.back or home_url())
        self.back = None

    def check(self):
        """Refresh both columns in the background (apk update needs the network, the kernel check too)."""
        if self.checking:
            return

        def run():
            self.checking = True
            try:
                if not UPDATE['running']:
                    check_updates()
            except Exception as e:
                print('update check:', e, flush=True)
            finally:
                check_kernel()
                self.checking = False
        threading.Thread(target=run, daemon=True).start()

    def status(self):
        return {'apps': {'pending': UPDATE['pending'], 'packages': UPDATE['packages'], 'checked': UPDATE['checked'],
                         'running': UPDATE['running'], 'mode': UPDATE['mode'], 'log': UPDATE['log'],
                         'last': UPDATE['last'], 'result': UPDATE['result'], 'auto_update': auto_update(),
                         'auto_packages': list(AUTO_PACKAGES), 'maintenance_time': reboot_time()},
                'kernel': kernel_status(), 'checking': self.checking, 'name': NAME,
                'closes_in': max(0, int(self.IDLE - (time.time() - max(self.since, display.last_touch)))) if self.on else 0}

    def post(self, path):
        """-> (http code, message)"""
        if not self.on:
            return 409, 'Die Update-Seite ist nicht aktiv.'
        if path == '/api/close':
            threading.Thread(target=self._close_and_publish, daemon=True).start()
            return 200, 'ok'
        if path == '/api/check':
            self.check()
            return 202, 'Wird geprüft …'
        if path == '/api/apps/update':
            if UPDATE['running'] or kernel_installing():
                return 409, 'Es läuft bereits ein Update.'
            threading.Thread(target=install_updates, kwargs={'full': True}, daemon=True).start()
            return 202, 'Update gestartet.'
        if path == '/api/kernel/install':
            if not display.lit or time.time() - display.last_touch > self.TOUCH_WINDOW:
                print('update page: kernel install refused (no touch on the screen)', flush=True)
                return 403, 'Nur per Berührung am Bildschirm möglich.'
            if not (KERNEL['info'] or {}).get('update_available'):
                return 409, 'Kein neuer Kernel verfügbar.'
            if not install_kernel():
                return 409, 'Es läuft bereits ein Update.'
            return 202, 'Kernel-Update gestartet.'
        return 404, 'not found'

    def _close_and_publish(self):
        self.close()
        if MQ:
            MQ.publish_state()

    def loop(self):
        """Close after IDLE without touch; keep the kiosk on the page while the switch is on (e.g. after the
        browser restart that follows an update) and off it while the switch is off (e.g. after an api restart)."""
        n = 0
        while True:
            time.sleep(5)
            n += 1
            if self.on and time.time() - max(self.since, display.last_touch) > self.IDLE:
                print(f'update page: no touch for {self.IDLE // 60} min', flush=True)
                self._close_and_publish()
            elif self.on or n % 3 == 0:
                url = browser.url()
                if url and self.on and not self.is_local(url):
                    browser.navigate(self.URL)
                elif url and not self.on and self.is_local(url):
                    browser.navigate(home_url())

    def start(self):
        import http.server
        import hmac
        page = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, code, body, ctype='application/json'):
                body = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header('Content-Type', ctype + '; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; "
                                 "style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def host_ok(self):
                return self.headers.get('Host') == f'127.0.0.1:{page.PORT}'

            def do_GET(self):
                path = self.path.split('?')[0]
                if not self.host_ok():
                    self.send(403, '{}')
                elif path == '/':
                    self.send(200, PAGE_HTML.replace('__TOKEN__', page.token), 'text/html')
                elif path == '/api/status':
                    self.send(200, json.dumps(page.status()))
                elif path == '/api/stats':  # ?span=3600|86400
                    span = 86400 if 'span=86400' in self.path else 3600
                    self.send(200, json.dumps({'span': span, 'uptime': int(uptime()), 'kernel': os.uname().release,
                                               'now': history.now, 'series': history.series(span)}))
                else:
                    self.send(404, '{}')

            def do_POST(self):
                self.rfile.read(min(4096, int(self.headers.get('Content-Length') or 0)))
                origin = self.headers.get('Origin')
                if not (self.host_ok() and hmac.compare_digest(self.headers.get('X-Wallpanel-Token', ''), page.token)
                        and origin in (None, page.URL.rstrip('/'))):
                    self.send(403, json.dumps({'message': 'forbidden'}))
                    return
                code, msg = page.post(self.path.split('?')[0])
                self.send(code, json.dumps({'message': msg}))

        server = http.server.ThreadingHTTPServer(('127.0.0.1', self.PORT), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=self.loop, daemon=True).start()
        threading.Thread(target=history.loop, daemon=True).start()


update_page = UpdatePage()

PAGE_HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>Updates</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { --bg:#111; --card:#1c1c1c; --line:#2e2e2e; --text:#e1e1e1; --dim:#9b9b9b; --primary:#03a9f4;
  --ok:#4caf50; --warn:#ff9800; --err:#ef5350; }
* { box-sizing:border-box; }
html, body { margin:0; height:100%; background:var(--bg); color:var(--text);
  font:20px/1.4 Roboto, "Noto Sans", system-ui, sans-serif; -webkit-user-select:none; user-select:none; }
body { display:flex; flex-direction:column; padding:24px 36px 28px; gap:18px; overflow:hidden; }
header { display:flex; align-items:center; gap:20px; }
header h1 { margin:0; font-size:34px; font-weight:500; flex:1; }
header h1 small { color:var(--dim); font-size:20px; font-weight:400; margin-left:14px; }
#stats { display:grid; grid-template-columns:repeat(5, 1fr) 1.25fr; gap:16px; }
.tile { background:var(--card); border-radius:16px; padding:14px 18px 10px; display:flex; flex-direction:column; gap:2px;
  box-shadow:0 2px 6px rgba(0,0,0,.35); min-width:0; }
.tile .dim { font-size:16px; }
.tile b { font-size:25px; font-weight:500; }
.tile svg { width:100%; height:48px; margin-top:4px; }
.tile polyline { fill:none; stroke:var(--primary); stroke-width:2; vector-effect:non-scaling-stroke; stroke-linejoin:round; }
.tile polygon { fill:rgba(3,169,244,.14); stroke:none; }
.tile.info { justify-content:space-between; gap:6px; }
.tile.info b { font-size:19px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.seg { display:flex; gap:8px; }
.seg button { flex:1; min-height:48px; font-size:19px; padding:0; background:#2a2a2a; border-radius:10px; }
.seg button.sel { background:var(--primary); }
main { flex:1; display:grid; grid-template-columns:1fr 1fr; gap:24px; min-height:0; }
section { background:var(--card); border-radius:16px; padding:26px 30px; display:flex; flex-direction:column;
  gap:18px; min-height:0; box-shadow:0 2px 6px rgba(0,0,0,.35); }
h2 { margin:0; font-size:26px; font-weight:500; display:flex; align-items:center; gap:12px; }
h2 svg { width:32px; height:32px; fill:var(--primary); }
.big { font-size:30px; font-weight:500; }
.dim { color:var(--dim); }
.rows { display:grid; grid-template-columns:max-content 1fr; gap:8px 24px; }
.rows .dim { white-space:nowrap; }
.list { flex:1; min-height:80px; overflow:auto; border-top:1px solid var(--line); border-bottom:1px solid var(--line);
  padding:6px 0; }
.list div { display:flex; justify-content:space-between; gap:16px; padding:7px 4px; border-bottom:1px solid #242424; }
.list div:last-child { border-bottom:0; }
.list .v { color:var(--dim); font-size:17px; font-family:"Roboto Mono", monospace; text-align:right; }
.list ul { margin:4px 0; padding-left:26px; } .list li { padding:4px 0; }
.tag { display:inline-block; font-size:14px; padding:2px 10px; border-radius:10px; background:#0b3a52; color:#8fd6fa;
  margin-left:10px; vertical-align:2px; }
.log { background:#0d0d0d; border-radius:10px; padding:12px 16px; font:15px/1.45 "Roboto Mono", monospace;
  color:#bdbdbd; white-space:pre-wrap; max-height:190px; overflow:hidden; display:none; }
.ok { color:var(--ok); } .err { color:var(--err); } .warn { color:var(--warn); }
button { font:inherit; font-size:24px; font-weight:500; border:0; border-radius:14px; min-height:76px; padding:0 34px;
  color:#fff; background:var(--primary); touch-action:manipulation; }
button:active { filter:brightness(.8); }
button:disabled { background:#333; color:#777; }
button.flat { background:#2a2a2a; min-height:64px; font-size:21px; }
button#close { background:#3a3a3a; min-height:84px; min-width:240px; font-size:26px; }
button.kernel { background:var(--warn); color:#1a1a1a; }
button.wide { width:100%; }
button:disabled, button.kernel:disabled { background:#333; color:#777; }
#closes { color:var(--dim); font-size:17px; }
#modal { position:fixed; inset:0; background:rgba(0,0,0,.7); display:none; align-items:center; justify-content:center; }
#modal .box { background:#232323; border-radius:20px; padding:40px 44px; width:880px; display:flex; flex-direction:column;
  gap:22px; box-shadow:0 20px 60px rgba(0,0,0,.6); }
#modal h3 { margin:0; font-size:30px; font-weight:500; }
#modal .btns { display:flex; gap:20px; justify-content:flex-end; margin-top:10px; }
#toast { position:fixed; left:50%; bottom:40px; transform:translateX(-50%); background:#323232; padding:18px 30px;
  border-radius:12px; font-size:21px; display:none; box-shadow:0 8px 30px rgba(0,0,0,.5); }
</style></head><body>
<header>
  <h1>Updates <small id="name"></small></h1>
  <span id="closes"></span>
  <button class="flat" id="check">Erneut prüfen</button>
  <button id="close">✕&nbsp; Schließen</button>
</header>
<div id="stats">
  <div class="tile"><span class="dim">Prozessor</span><b id="v-cpu">–</b><svg id="g-cpu" viewBox="0 0 240 50" preserveAspectRatio="none"></svg></div>
  <div class="tile"><span class="dim">Temperatur</span><b id="v-temp">–</b><svg id="g-temp" viewBox="0 0 240 50" preserveAspectRatio="none"></svg></div>
  <div class="tile"><span class="dim">Arbeitsspeicher</span><b id="v-mem">–</b><svg id="g-mem" viewBox="0 0 240 50" preserveAspectRatio="none"></svg></div>
  <div class="tile"><span class="dim">WLAN-Signal</span><b id="v-rssi">–</b><svg id="g-rssi" viewBox="0 0 240 50" preserveAspectRatio="none"></svg></div>
  <div class="tile"><span class="dim">Bildschirm</span><b id="v-backlight">–</b><svg id="g-backlight" viewBox="0 0 240 50" preserveAspectRatio="none"></svg></div>
  <div class="tile info">
    <span class="dim">Betriebszeit <b id="v-up" style="display:block">–</b></span>
    <span class="dim">Kernel <b id="v-kern" style="display:block">–</b></span>
    <div class="seg"><button data-s="3600" class="sel">1 h</button><button data-s="86400">24 h</button></div>
  </div>
</div>
<main>
  <section>
    <h2><svg viewBox="0 0 24 24"><path d="M21 16.5c0 .38-.21.71-.53.88l-7.9 4.44c-.16.12-.36.18-.57.18s-.41-.06-.57-.18l-7.9-4.44A1 1 0 0 1 3 16.5v-9c0-.38.21-.71.53-.88l7.9-4.44c.16-.12.36-.18.57-.18s.41.06.57.18l7.9 4.44c.32.17.53.5.53.88v9z"/></svg>Apps &amp; System</h2>
    <div><div class="big" id="a-count">…</div><div class="dim" id="a-checked"></div></div>
    <div class="list" id="a-list"></div>
    <div class="rows">
      <span class="dim">Auto-Update Apps</span><span id="a-auto"></span>
      <span class="dim">Letztes Update</span><span id="a-last"></span>
    </div>
    <div class="log" id="a-log"></div>
    <button class="wide" id="a-go">Jetzt aktualisieren</button>
  </section>
  <section>
    <h2><svg viewBox="0 0 24 24"><path d="M17 17H7V7h10m4 4V9h-2V7a2 2 0 0 0-2-2h-2V3h-2v2h-2V3H9v2H7a2 2 0 0 0-2 2v2H3v2h2v2H3v2h2v2a2 2 0 0 0 2 2h2v2h2v-2h2v2h2v-2h2a2 2 0 0 0 2-2v-2h2v-2h-2v-2m-6 2h-2v-2h2m2-2H9v6h6V9z"/></svg>Kernel</h2>
    <div class="rows">
      <span class="dim">Läuft</span><span id="k-run"></span>
      <span class="dim">Letztes Kernel-Update</span><span id="k-last"></span>
    </div>
    <div><div class="big" id="k-avail">…</div><div class="dim" id="k-checked"></div></div>
    <div class="list" id="k-list"></div>
    <div class="log" id="k-log"></div>
    <button class="wide kernel" id="k-go" disabled>Kernel installieren</button>
  </section>
</main>
<div id="modal"><div class="box">
  <h3 id="m-title">Kernel installieren?</h3>
  <div>Das Panel startet zum Test neu und ist dabei einige Minuten nicht bedienbar. Läuft der neue Kernel
    einwandfrei, wird er übernommen – sonst startet das Panel automatisch wieder mit dem bisherigen Kernel.</div>
  <div class="dim">Während des Updates bitte nicht vom Strom trennen.</div>
  <div class="btns"><button class="flat" id="m-no">Abbrechen</button>
    <button class="kernel" id="m-yes">Installieren und neu starten</button></div>
</div></div>
<div id="toast"></div>
<script>
const TOKEN = '__TOKEN__';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const when = (s) => {
  if (!s) return '–';
  const d = new Date(String(s).replace(/([+-]\\d\\d)(\\d\\d)$/, '$1:$2'));
  return isNaN(d) ? esc(s) : d.toLocaleString('de-DE', {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'});
};
let S = null;
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.style.display = 'block';
  clearTimeout(toast.t); toast.t = setTimeout(() => t.style.display = 'none', 4000);
}
async function post(path) {
  try {
    const r = await fetch(path, {method: 'POST', headers: {'X-Wallpanel-Token': TOKEN}});
    const j = await r.json().catch(() => ({}));
    if (r.status === 403 && j.message === 'forbidden') { location.reload(); return; }  // api restarted: new token
    toast(j.message || r.status);
  } catch (e) { toast('Keine Verbindung'); }
  refresh();
}
function log(el, lines, show) {
  el.style.display = show && lines.length ? 'block' : 'none';
  el.textContent = lines.join('\\n');
}
function render() {
  const a = S.apps, k = S.kernel, busy = a.running || k.running;
  $('name').textContent = S.name;
  $('closes').textContent = S.closes_in ? `schließt in ${Math.ceil(S.closes_in / 60)} min` : '';
  $('check').disabled = S.checking;
  $('check').textContent = S.checking ? 'Prüfe …' : 'Erneut prüfen';
  // apps & system
  $('a-count').innerHTML = a.running ? '<span class="warn">Wird aktualisiert …</span>'
    : a.pending == null ? (S.checking ? 'Wird geprüft …' : 'Unbekannt')
    : a.pending ? `${a.pending} Update${a.pending == 1 ? '' : 's'} verfügbar` : '<span class="ok">Alles aktuell</span>';
  $('a-checked').textContent = a.checked ? `geprüft ${when(a.checked)}` : '';
  $('a-list').innerHTML = a.packages.map((p) => `<div><span>${esc(p.name)}${a.auto_packages.includes(p.name)
    ? '<span class="tag">Auto-Update</span>' : ''}</span><span class="v">${esc(p.old)} → ${esc(p.new)}</span></div>`).join('')
    || '<div class="dim">Keine ausstehenden Pakete</div>';
  $('a-auto').innerHTML = a.auto_update ? `<span class="ok">an</span> – Chrome &amp; AirPlay täglich um ${esc(a.maintenance_time)}`
    : '<span class="dim">aus</span> <span class="dim">(Chrome &amp; AirPlay, täglich zur Wartungszeit)</span>';
  $('a-last').innerHTML = a.last ? `${when(a.last)} – <span class="${/^ok/.test(a.result) ? 'ok' : 'err'}">${esc(a.result)}</span>` : '–';
  log($('a-log'), a.log, a.running || a.log.length);
  $('a-go').disabled = busy || a.pending === 0;
  $('a-go').textContent = a.running ? 'Wird aktualisiert …' : 'Jetzt aktualisieren';
  // kernel
  $('k-run').textContent = `${k.running_kernel} (Slot ${k.running_slot || '?'})`;
  const lr = k.last_result;
  $('k-last').innerHTML = lr ? `${when(lr.time)} – ${esc(lr.from)} → ${esc(lr.to)}: ${lr.ok ? '<span class="ok">erfolgreich</span>'
    : `<span class="err">zurück auf den bisherigen Kernel${lr.reason ? ' (' + esc(lr.reason) + ')' : ''}</span>`}` : '–';
  const av = k.available;
  $('k-avail').innerHTML = k.running ? '<span class="warn">Wird installiert …</span>'
    : k.error ? `<span class="dim">${esc(k.error)}</span>`
    : k.checking && !k.checked ? 'Wird geprüft …'
    : k.update_available && av ? `Neuer Kernel ${esc(av.kernel)}` : '<span class="ok">Kernel ist aktuell</span>';
  $('k-checked').textContent = av ? `Release ${av.tag}${av.published ? ', veröffentlicht ' + when(av.published) : ''}`
    : k.checked ? `geprüft ${when(k.checked)}` : '';
  $('k-list').innerHTML = av && (av.changelog || []).length
    ? '<ul>' + av.changelog.map((c) => `<li>${esc(c)}</li>`).join('') + '</ul>' : '<div class="dim">Keine Änderungen</div>';
  log($('k-log'), k.log, k.running || k.log.length);
  $('k-go').disabled = busy || !k.update_available;
  $('k-go').textContent = k.running ? 'Wird installiert …' : 'Kernel installieren';
}
async function refresh() {
  try { S = await (await fetch('/api/status')).json(); render(); } catch (e) {}
}
$('close').onclick = () => post('/api/close');
$('check').onclick = () => post('/api/check');
$('a-go').onclick = () => post('/api/apps/update');
$('k-go').onclick = () => {
  $('m-title').textContent = `Kernel ${S.kernel.available.kernel} installieren?`;
  $('modal').style.display = 'flex';
};
$('m-no').onclick = () => $('modal').style.display = 'none';
$('m-yes').onclick = () => { $('modal').style.display = 'none'; post('/api/kernel/install'); };
// system values: sparklines from the api's in-memory history (1 h / 24 h), every 5 s
let span = 3600;
const RANGE = {cpu: [0, 100], mem: [0, 100], backlight: [0, 100]};
const num = (x, d = 0) => x.toLocaleString('de-DE', {maximumFractionDigits: d, minimumFractionDigits: d});
const FMT = {cpu: (x) => `${num(x)} %`, temp: (x) => `${num(x, 1)} °C`, mem: (x) => `${num(x)} %`,
  rssi: (x) => `${num(x)} dBm`, backlight: (x) => x > 0 ? `an · ${num(x)} %` : 'aus'};
function spark(k, arr, cur) {
  const v = arr.filter((x) => x != null), svg = $('g-' + k), W = 240, H = 50;
  $('v-' + k).textContent = cur == null ? '–' : FMT[k](cur);
  if (!v.length) { svg.innerHTML = ''; return; }
  const [lo, hi] = RANGE[k] || [Math.min(...v) - 2, Math.max(...v) + 2];
  const pts = [];
  arr.forEach((x, i) => { if (x != null) pts.push([i / Math.max(1, arr.length - 1) * W, H - 1 - (x - lo) / (hi - lo || 1) * (H - 2)]); });
  if (pts.length == 1) pts.unshift([pts[0][0] - 3, pts[0][1]]);
  const p = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  svg.innerHTML = `<polygon points="${pts[0][0].toFixed(1)},${H} ${p} ${pts[pts.length - 1][0].toFixed(1)},${H}"/><polyline points="${p}"/>`;
}
async function stats() {
  try {
    const s = await (await fetch('/api/stats?span=' + span)).json();
    for (const k in s.series) spark(k, s.series[k], s.now[k]);
    const d = Math.floor(s.uptime / 86400), h = Math.floor(s.uptime % 86400 / 3600), m = Math.floor(s.uptime % 3600 / 60);
    $('v-up').textContent = d ? `${d} ${d == 1 ? 'Tag' : 'Tage'} ${h} h` : `${h} h ${m} min`;
    $('v-kern').textContent = s.kernel;
  } catch (e) {}
}
document.querySelectorAll('.seg button').forEach((b) => b.onclick = () => {
  span = +b.dataset.s;
  document.querySelectorAll('.seg button').forEach((x) => x.classList.toggle('sel', x === b));
  stats();
});
refresh(); setInterval(refresh, 2000);
stats(); setInterval(stats, 5000);
</script></body></html>
"""


# --- hardware keys ---------------------------------------------------------

class TapGesture:
    """Hidden gesture: TAPS touches within WINDOW seconds on the lit screen open the update page (no HA
    needed). Only finger-down events count; the taps also reach the page below, like any tap would."""
    TAPS, WINDOW = 10, 4.0

    def __init__(self):
        self.times = []

    def tap(self, t):
        """Register a touch at time t; True once TAPS touches fall within WINDOW (then starts over)."""
        self.times = [x for x in self.times if t - x < self.WINDOW] + [t]
        if len(self.times) >= self.TAPS:
            self.times = []
            return True
        return False


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
    taps = TapGesture()
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
                    if ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH and ev.value == 1 and display.lit:
                        display.last_touch = time.time()  # a real finger on the visible screen (update page)
                        if taps.tap(display.last_touch) and not update_page.on:
                            print(f'{taps.TAPS} taps -> update page', flush=True)
                            threading.Thread(target=update_page.open_by_touch, daemon=True).start()
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
    update_page.start()
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
