#!/usr/bin/env python3
"""Colour check: a bulb and the panel at the same colour temperature, step by step - to see by eye whether
the panel's calibration (Farbton-Kalibrierung, Weißabgleich Rot/Blau) matches the bulb.

  tools/debug/ct-sync.py [--steps 10] [--hold 8] [--from 2200] [--to 4000] [--white] [--brightness 60]
  tools/debug/ct-sync.py --random --step 300 --hold 2 --white     # random colours every 2 s until Ctrl+C

  --white   the panel shows a plain white page meanwhile (DevTools over tools/tssh), else the dashboard

tools/local.env: HA_URL, HA_TOKEN (long-lived access token; or HA_TOKEN_FILE), CT_LAMP (the bulb's light
entity), optional CT_PAUSE (space-separated automation./switch. entities turned off meanwhile, e.g. Adaptive
Lighting switches and motion automations) and CT_PANEL (default light.wallpanel_display). Everything paused
is turned on again at the end, also on Ctrl+C.
"""
import argparse, json, os, re, signal, ssl, subprocess, sys, time, urllib.request

P = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV = {}
for line in open(os.path.join(P, 'tools', 'local.env')):
    m = re.match(r"\s*([A-Z_][A-Z0-9_]*)=(.*)$", line)
    if m:
        ENV[m.group(1)] = m.group(2).strip().strip("'\"")
HA = ENV.get('HA_URL', '').rstrip('/')
TOKEN = ENV.get('HA_TOKEN') or (open(os.path.expanduser(ENV['HA_TOKEN_FILE'])).read().strip() if ENV.get('HA_TOKEN_FILE') else '')
LAMP, PANEL = ENV.get('CT_LAMP'), ENV.get('CT_PANEL', 'light.wallpanel_display')
PAUSE = ENV.get('CT_PAUSE', '').split()
CTX = ssl._create_unverified_context()  # HA with a self-signed certificate on the LAN


def api(method, path, data=None):
    req = urllib.request.Request(f'{HA}/api/{path}', method=method, data=json.dumps(data).encode() if data else None,
                                 headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, context=CTX, timeout=20))


def call(service, **data):
    d, s = service.split('.')
    api('POST', f'services/{d}/{s}', data)


class White:
    """Plain white page over the dashboard via the kiosk's DevTools (ssh tunnel with tools/tssh)."""
    def __init__(self):
        env = dict(os.environ)
        env.setdefault('WALLPANEL_HOST', ENV.get('DEVICE_WLAN_IP', '10.42.0.1'))
        self.tunnel = subprocess.Popen([os.path.join(P, 'tools', 'tssh'), '-N', '-L', '19223:127.0.0.1:9222'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        for _ in range(20):  # until the tunnel answers
            try:
                urllib.request.urlopen('http://127.0.0.1:19223/json/version', timeout=1)
                return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError('no DevTools tunnel to the panel (DEVICE_WLAN_IP / WALLPANEL_HOST?)')

    def js(self, expr):
        import websocket
        t = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:19223/json', timeout=5))
                 if t['type'] == 'page' and not t['url'].startswith('chrome'))
        ws = websocket.create_connection(t['webSocketDebuggerUrl'], timeout=10, suppress_origin=True)
        ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': expr}}))
        ws.recv()
        ws.close()

    def on(self):
        self.js("(() => { let d = document.getElementById('wp-ct'); if (!d) { d = document.createElement('div');"
                " d.id = 'wp-ct'; d.style.cssText = 'position:fixed;inset:0;background:#fff;z-index:2147483647';"
                " document.documentElement.appendChild(d); } })()")

    def off(self):
        try:
            self.js("document.getElementById('wp-ct')?.remove()")
        finally:
            self.tunnel.terminate()


def main():
    a = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    a.add_argument('--steps', type=int, default=10)
    a.add_argument('--hold', type=float, default=8, help='seconds per colour')
    a.add_argument('--from', dest='k0', type=int, default=2200)
    a.add_argument('--to', dest='k1', type=int, default=4000)
    a.add_argument('--brightness', type=int, default=60, help='bulb brightness 1-255')
    a.add_argument('--random', action='store_true', help='random colours from --from..--to in --step K, until Ctrl+C')
    a.add_argument('--step', type=int, default=300, help='Kelvin step for --random')
    a.add_argument('--white', action='store_true')
    o = a.parse_args()
    if not (HA and TOKEN and LAMP):
        sys.exit('set HA_URL, HA_TOKEN (or HA_TOKEN_FILE) and CT_LAMP in tools/local.env')
    was = {e: api('GET', f'states/{e}')['state'] for e in PAUSE}
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # SIGTERM: through finally like Ctrl+C
    white = None
    try:
        for e in PAUSE:
            call(f'{e.split(".")[0]}.turn_off', entity_id=e)
        if o.white:
            white = White()
            white.on()
        print(f'{LAMP} and {PANEL} (Farbton-Kalibrierung / Weißabgleich set on the panel), {o.hold:g} s each')
        import itertools, random
        levels = list(range(o.k0, o.k1 + 1, o.step))
        last = None
        for i in itertools.count() if o.random else range(o.steps):
            if o.random:
                k = random.choice([x for x in levels if x != last])
            else:
                k = round(o.k0 + (o.k1 - o.k0) * i / max(1, o.steps - 1))
            last = k
            call('light.turn_on', entity_id=LAMP, color_temp_kelvin=k, brightness=o.brightness, transition=0)
            call('light.turn_on', entity_id=PANEL, color_temp_kelvin=k, brightness=255)
            print(f'  {i + 1:3}{"" if o.random else "/" + str(o.steps)}  {k} K', flush=True)
            time.sleep(o.hold)
    except KeyboardInterrupt:
        print('stopped')
    finally:
        for e, st in was.items():
            if st == 'on':
                call(f'{e.split(".")[0]}.turn_on', entity_id=e)
        if white:
            white.off()
        print('restored:', ', '.join(e for e, st in was.items() if st == 'on') or '-')


if __name__ == '__main__':
    main()
