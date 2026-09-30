#!/usr/bin/env python3
"""Minimal Chrome DevTools Protocol helper for the kiosk (via ssh -L 9222).

  cdp.py login                 log in to Home Assistant (HA_USER/HA_PASS from tools/local.env)
  cdp.py eval '<js>'           evaluate JavaScript in the page
  cdp.py fps [seconds]         measure frame rate / long frames while the page runs
  cdp.py scroll [seconds]      scroll the dashboard up/down and measure frames meanwhile
"""
import json
import os
import sys
import time
import urllib.request

import websocket

ENV = dict(l.strip().split('=', 1) for l in open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'local.env'))
    if '=' in l and not l.startswith('#'))
ENV = {k: v.strip("'\"") for k, v in ENV.items()}


class Page:
    def __init__(self):
        targets = json.load(urllib.request.urlopen('http://127.0.0.1:9222/json'))
        page = next(t for t in targets if t['type'] == 'page')
        self.ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=60,
                                              suppress_origin=True)
        self.n = 0

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get('id') == self.n:
                if 'error' in msg:
                    raise RuntimeError(msg['error'])
                return msg.get('result', {})

    def js(self, expr, await_promise=True):
        r = self.call('Runtime.evaluate', expression=expr, awaitPromise=await_promise,
                      returnByValue=True)
        return r.get('result', {}).get('value')


DEEP = """
function deepAll(sel, root = document, out = []) {
  root.querySelectorAll(sel).forEach(e => out.push(e));
  root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) deepAll(sel, e.shadowRoot, out); });
  return out;
}
"""

FRAMES = """
(async (ms) => {
  const t = []; let last = performance.now(); const end = last + ms;
  await new Promise(res => { function f(now) { t.push(now - last); last = now;
    if (now < end) requestAnimationFrame(f); else res(); } requestAnimationFrame(f); });
  t.shift(); t.sort((a, b) => a - b);
  const q = p => t[Math.min(t.length - 1, Math.floor(p * t.length))];
  return {frames: t.length, fps: +(t.length / (ms / 1000)).toFixed(1),
          p50_ms: +q(0.5).toFixed(1), p95_ms: +q(0.95).toFixed(1), p99_ms: +q(0.99).toFixed(1),
          max_ms: +t[t.length - 1].toFixed(1), over_33ms: t.filter(x => x > 33.4).length};
})(%d)
"""


def login(p):
    user, pw = ENV['HA_USER'], ENV['HA_PASS']
    for _ in range(30):
        n = p.js(DEEP + "deepAll('input').length")
        if n and n >= 2:
            break
        time.sleep(1)
    res = p.js(DEEP + """
    (() => {
      const ins = deepAll('input').filter(i => ['text','password','email'].includes(i.type) || !i.type);
      const set = (el, v) => { el.focus(); el.value = v;
        el.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
        el.dispatchEvent(new Event('change', {bubbles: true, composed: true})); };
      const u = ins.find(i => i.name === 'username' || i.autocomplete === 'username') || ins[0];
      const pw = ins.find(i => i.type === 'password');
      set(u, %s); set(pw, %s);
      const btn = deepAll('ha-button, mwc-button, button').find(b => /log ?in|anmelden/i.test(b.textContent));
      if (btn) btn.click();
      return {inputs: ins.length, button: !!btn};
    })()""" % (json.dumps(user), json.dumps(pw)))
    print('login form:', res)
    for _ in range(30):
        time.sleep(1)
        url = p.js('location.href')
        if '/auth/authorize' not in url:
            print('logged in, now at', url)
            return
    print('still on login page:', p.js('location.href'))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    p = Page()
    if cmd == 'login':
        login(p)
    elif cmd == 'eval':
        print(json.dumps(p.js(sys.argv[2]), indent=1))
    elif cmd == 'fps':
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 10
        print(json.dumps(p.js(FRAMES % int(secs * 1000))))
    elif cmd == 'scroll':
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 10
        p.js(DEEP + """
        (() => { const sc = deepAll('*').filter(e => e.scrollHeight > e.clientHeight + 50 &&
                   getComputedStyle(e).overflowY.match(/auto|scroll/));
          window.__sc = sc.sort((a,b) => b.scrollHeight - a.scrollHeight)[0] || document.scrollingElement;
          let dir = 1; window.__t = setInterval(() => { const s = window.__sc;
            s.scrollBy(0, 12 * dir); if (s.scrollTop + s.clientHeight >= s.scrollHeight - 2) dir = -1;
            if (s.scrollTop <= 0) dir = 1; }, 16); return true; })()""")
        print(json.dumps(p.js(FRAMES % int(secs * 1000))))
        p.js('clearInterval(window.__t)')
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
