#!/usr/bin/env python3
"""Print vendor DT nodes with phandles resolved to labels/paths and GPIO specs decoded."""
import sys, fdt
d = fdt.parse_dtb(open(sys.argv[1], 'rb').read())
ph = {}
def idx(n, path):
    p = n.get_property('phandle')
    if p: ph[p.value] = path or '/'
    for c in n.nodes: idx(c, f"{path}/{c.name}")
idx(d.root, '')
gpiobank = {v: 'gpio' + k[-1] for k, v in ((p, n) for n, p in ph.items()) if False}
for n, p in ph.items():
    if p.split('/')[-1].startswith('gpio') and '@' in p.split('/')[-1]:
        pass
banks = {}
try:
    pc = d.get_node('/pinctrl')
    for c in pc.nodes:
        if c.name.startswith('gpio') and c.get_property('phandle'):
            addr = c.name.split('@')[1]
            banks[c.get_property('phandle').value] = {'ff720000':'gpio0','ff730000':'gpio1','ff780000':'gpio2','ff788000':'gpio3','ff790000':'gpio4'}.get(addr, c.name)
except Exception: pass
PINS = 'ABCD'
def fmt(prop):
    name = prop.name
    if not hasattr(prop, 'data') or not prop.data or not isinstance(prop.data[0], int):
        return None
    v = list(prop.data)
    if name.endswith('gpios') or name.endswith('gpio') or name in ('gpio',):
        out = []; i = 0
        while i + 2 < len(v) + 0 and i < len(v):
            b = banks.get(v[i], ph.get(v[i], hex(v[i])))
            pin = v[i+1]; fl = v[i+2] if i+2 < len(v) else 0
            out.append(f"<&{b} RK_P{PINS[pin//8]}{pin%8} {'GPIO_ACTIVE_LOW' if fl & 1 else 'GPIO_ACTIVE_HIGH'}>")
            i += 3
        return ', '.join(out)
    if name.startswith('pinctrl-') and name != 'pinctrl-names':
        return ', '.join('&' + ph.get(x, hex(x)).split('/')[-1] for x in v)
    if name.endswith('-supply') or name in ('backlight', 'pwms', 'mmc-pwrseq', 'interrupt-parent', 'remote-endpoint', 'clocks'):
        return ' '.join(('&' + ph[x].split('/')[-1]) if x in ph else hex(x) for x in v)
    return None
def show(n, ind=0):
    pad = '    ' * ind
    print(f"{pad}{n.name} {{")
    for p in n.props:
        f = fmt(p)
        if f is not None: print(f"{pad}    {p.name} = {f};   // decoded")
        else: print(pad + '    ' + p.to_dts().strip())
    for c in n.nodes: show(c, ind + 1)
    print(f"{pad}}};")
for path in sys.argv[2:]:
    show(d.get_node(path))
