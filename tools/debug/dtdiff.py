#!/usr/bin/env python3
"""Semantic diff of two DTBs: nodes added/removed and properties changed.

Phandle references in well-known reference properties are resolved to node
paths first, so differing phandle numbering does not show up as a change.

  dtdiff.py base.dtb board.dtb
"""
import re
import sys

import fdt

REF_PROPS = re.compile(
    r'(-supply|-gpios?|^gpios?$|^pinctrl-\d+$|^clocks$|^assigned-clocks$|'
    r'^assigned-clock-parents$|^pwms$|^phys$|^remote-endpoint$|^interrupt-parent$|'
    r'^backlight$|^extcon$|^io-channels$|^sound-dai$|^rockchip,grf$|^power-domains$|'
    r'^resets$|^iommus$|^mmc-pwrseq$|^rockchip,cpu$|^rockchip,codec$|^ddc-i2c-bus$|'
    r'^cpu-supply$|^operating-points-v2$|^rockchip,pmu$|^msi-map$|^interrupt-map$|'
    r'^WIFI,host_wake_irq$|^BT,wake_host_irq$|^[A-Za-z0-9]+,[a-z_]*gpio$|_gpios?$)')


def load(path):
    tree = fdt.parse_dtb(open(path, 'rb').read())
    phandles, nodes = {}, {}

    def walk(node, p):
        nodes[p or '/'] = node
        ph = node.get_property('phandle')
        if ph is not None:
            phandles[ph.value] = p or '/'
        for child in node.nodes:
            walk(child, f"{p}/{child.name}")

    walk(tree.root, '')
    return nodes, phandles


def value(prop, phandles):
    data = getattr(prop, 'data', None)
    if not data:
        return '<bool>'
    if isinstance(data[0], int):
        if REF_PROPS.search(prop.name):
            return ' '.join(f'&{phandles[v]}' if v in phandles else hex(v) for v in data)
        return ' '.join(hex(v) for v in data)
    return repr(list(data))


def props(node, phandles):
    return {p.name: value(p, phandles) for p in node.props
            if p.name not in ('phandle', 'linux,phandle')}


def main():
    (a_nodes, a_ph), (b_nodes, b_ph) = load(sys.argv[1]), load(sys.argv[2])
    for path in sorted(set(a_nodes) | set(b_nodes)):
        if path.startswith('/__'):
            continue
        if path not in b_nodes:
            print(f'- {path}')
            continue
        if path not in a_nodes:
            print(f'+ {path}')
            for k, v in props(b_nodes[path], b_ph).items():
                print(f'    + {k} = {v}')
            continue
        pa, pb = props(a_nodes[path], a_ph), props(b_nodes[path], b_ph)
        changes = [(k, pa.get(k), pb.get(k)) for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)]
        if changes:
            print(f'~ {path}')
            for k, va, vb in changes:
                if va is None:
                    print(f'    + {k} = {vb}')
                elif vb is None:
                    print(f'    - {k} = {va}')
                else:
                    print(f'    ~ {k}: {va}  ->  {vb}')


if __name__ == '__main__':
    main()
