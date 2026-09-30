#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Where does the page spend time? Diff of CDP Performance metrics over N seconds + GPU feature status."""
import json, sys, time, urllib.request
sys.path.insert(0, __import__('os').path.dirname(__file__))
from cdp import Page
import websocket

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 5
p = Page()
p.call('Performance.enable')
m = lambda: {x['name']: x['value'] for x in p.call('Performance.getMetrics')['metrics']}
a = m(); time.sleep(secs); b = m()
keys = ['TaskDuration', 'ScriptDuration', 'RecalcStyleDuration', 'LayoutDuration', 'Frames', 'LayoutCount', 'RecalcStyleCount', 'JSHeapUsedSize', 'Nodes']
for k in keys:
    d = b[k] - a[k] if k not in ('JSHeapUsedSize', 'Nodes') else b[k]
    unit = 's' if 'Duration' in k else ''
    print(f"{k:22s} {d:10.3f}{unit}  {'(%.0f%% of wall)' % (100*d/secs) if 'Duration' in k else ''}")
ver = json.load(urllib.request.urlopen('http://127.0.0.1:9222/json/version'))
bw = websocket.create_connection(ver['webSocketDebuggerUrl'], suppress_origin=True)
bw.send(json.dumps({'id': 1, 'method': 'SystemInfo.getInfo'}))
info = json.loads(bw.recv())['result']['gpu']
print('featureStatus:', {k: v for k, v in info['featureStatus'].items() if k in ('gpu_compositing', 'rasterization', 'webgl', 'video_decode', 'canvas_oop_rasterization', 'opengl')})
print('driverBugWorkarounds:', len(info.get('driverBugWorkarounds', [])))
