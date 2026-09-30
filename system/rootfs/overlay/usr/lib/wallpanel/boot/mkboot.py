#!/usr/bin/env python3
"""Assemble the wallpanel boot image: rescue-capable initramfs + Android boot image v2.

One code path for the PC (tools/build-bootimg.sh) and the device (wallpanel-update fetch):

  mkboot.py initramfs --modules modules.tar.gz --keys authorized_keys [--src ROOT] -o initramfs.cpio.gz
  mkboot.py image --kernel Image --dtb board.dtb --ramdisk initramfs.cpio.gz [--slot A|B] [--rescue] -o boot.img

The initramfs holds only: init (next to this script), busybox + applet links, dropbear/dropbearkey and
their libraries (taken from ROOT: the installed system on the device, an extracted Alpine minirootfs on
the PC), the display modules (rescue notice on the panel) with their dependencies, minimal /etc and the
maintenance keys. The archive is reproducible (sorted, fixed mtime/owner): same inputs, same bytes.
"""
import argparse
import gzip
import io
import os
import re
import stat
import struct
import subprocess
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mkrkboot  # noqa: E402

CMDLINE = "console=tty1 earlycon=uart8250,mmio32,0xff1a0000 rootwait panic=10 loglevel=4"
BOOT_PART_BYTES = 45 * 1024 * 1024      # smallest slot (boot); recovery is 96 MiB
DISPLAY_MODULES = ("rockchipdrm", "panel-iiyama-tw2424as", "pwm_bl")
BINARIES = ("bin/busybox", "usr/sbin/dropbear", "usr/bin/dropbearkey")
LIBDIRS = ("lib", "usr/lib")
ETC = {
    "etc/passwd": "root:x:0:0:root:/root:/bin/sh\n",
    "etc/group": "root:x:0:root\n",
    "etc/shadow": "root:*::0:::::\n",
    "etc/shells": "/bin/sh\n/bin/ash\n",
    "etc/profile": "export PATH=/usr/sbin:/usr/bin:/sbin:/bin\n",
}
DIRS = ("bin", "sbin", "usr/bin", "usr/sbin", "lib", "usr/lib", "etc", "dev", "proc", "sys", "run",
        "tmp", "mnt", "newroot", "var", "var/tmp")


class Cpio:
    """newc archive, entries sorted by path, uid/gid 0, mtime 0."""

    def __init__(self):
        self.entries = {}   # path -> (mode, data)

    def dir(self, path, mode=0o755):
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            p = "/".join(parts[:i])
            if p not in self.entries:
                self.entries[p] = (stat.S_IFDIR | (mode if i == len(parts) else 0o755), b"")

    def parent(self, path):
        if "/" in path:
            self.dir(os.path.dirname(path))

    def file(self, path, data, mode=0o644):
        self.parent(path)
        self.entries[path] = (stat.S_IFREG | mode, data)

    def link(self, path, target):
        self.parent(path)
        self.entries[path] = (stat.S_IFLNK | 0o777, target.encode())

    def pack(self):
        out = io.BytesIO()

        def entry(ino, name, mode, data, nlink):
            n = name.encode() + b"\0"
            out.write(b"070701" + b"".join(b"%08X" % v for v in (
                ino, mode, 0, 0, nlink, 0, len(data), 0, 0, 0, 0, len(n), 0)))
            out.write(n + b"\0" * (-(110 + len(n)) % 4))
            out.write(data + b"\0" * (-len(data) % 4))

        for ino, path in enumerate(sorted(self.entries), 1):
            mode, data = self.entries[path]
            entry(ino, path, mode, data, 2 if stat.S_ISDIR(mode) else 1)
        entry(0, "TRAILER!!!", 0, b"", 1)
        return out.getvalue()


def elf_needed(data):
    """PT_INTERP and DT_NEEDED of a 64-bit little-endian ELF."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise SystemExit("not a 64-bit little-endian ELF")
    phoff, = struct.unpack_from("<Q", data, 32)
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    interp, dyn, loads = None, None, []
    for i in range(phnum):
        p_type, _, p_off, p_vaddr, _, p_filesz = struct.unpack_from("<IIQQQQ", data, phoff + i * phentsize)
        if p_type == 3:
            interp = data[p_off:p_off + p_filesz].rstrip(b"\0").decode()
        elif p_type == 2:
            dyn = (p_off, p_filesz)
        elif p_type == 1:
            loads.append((p_vaddr, p_off, p_filesz))
    needed, strtab = [], None
    if dyn:
        for o in range(dyn[0], dyn[0] + dyn[1], 16):
            tag, val = struct.unpack_from("<qQ", data, o)
            if tag == 0:
                break
            if tag == 1:
                needed.append(val)
            elif tag == 5:
                strtab = next(off + val - va for va, off, sz in loads if va <= val < va + sz)

    def cstr(o):
        return data[o:data.index(b"\0", o)].decode()
    return interp, [cstr(strtab + v) for v in needed]


def add_path(cpio, src, rel, seen):
    """Copy ROOT/rel into the archive; symlinks are kept and followed. Returns the resolved rel path."""
    if rel in seen:
        return seen[rel]
    full = os.path.join(src, rel)
    if os.path.islink(full):
        t = os.readlink(full)
        cpio.link(rel, t)
        target = os.path.normpath(os.path.join(os.path.dirname(rel), t) if not t.startswith("/") else t[1:])
        seen[rel] = add_path(cpio, src, target, seen)
        return seen[rel]
    data = open(full, "rb").read()
    cpio.file(rel, data, stat.S_IMODE(os.stat(full).st_mode) & 0o755)
    seen[rel] = rel
    return rel


def add_elf(cpio, src, rel, seen):
    real = add_path(cpio, src, rel, seen)
    interp, needed = elf_needed(open(os.path.join(src, real), "rb").read())
    if interp:
        add_path(cpio, src, interp.lstrip("/"), seen)
    for lib in needed:
        for d in LIBDIRS:
            if os.path.lexists(os.path.join(src, d, lib)):
                add_elf(cpio, src, f"{d}/{lib}", seen)
                break
        else:
            raise SystemExit(f"{rel}: library {lib} not found in {src}")


def busybox_applets(src):
    """Applet paths (bin/ls, sbin/ip ...). Natively (on the device) busybox lists them itself;
    in a foreign-arch root they are the symlinks to busybox/bbsuid its package installed."""
    if src == "/":
        out = subprocess.run(["/bin/busybox", "--list-full"], check=True, capture_output=True, text=True).stdout
        return sorted(line for line in out.split() if line)
    applets = []
    for d in ("bin", "sbin", "usr/bin", "usr/sbin"):
        for n in os.listdir(os.path.join(src, d)):
            f = os.path.join(src, d, n)
            if os.path.islink(f) and os.path.basename(os.readlink(f)) in ("busybox", "bbsuid"):
                applets.append(f"{d}/{n}")
    return sorted(applets)


def add_modules(cpio, modules):
    """Display modules + dependencies from modules.tar.gz (<release>/...); filtered modules.dep."""
    with tarfile.open(modules, "r:gz") as t:
        members = {m.name: m for m in t.getmembers() if m.isfile()}
        rel = sorted({n.split("/")[0] for n in members})
        if len(rel) != 1:
            raise SystemExit(f"{modules}: expected one kernel release, found {rel}")
        rel = rel[0]
        dep = {}
        for line in t.extractfile(members[f"{rel}/modules.dep"]).read().decode().splitlines():
            k, _, v = line.partition(":")
            dep[k] = v.split()
        need = set()
        for m in DISPLAY_MODULES:
            hit = [k for k in dep if re.search(r"/" + re.escape(m) + r"\.ko(\.[a-z]+)?$", k)]
            if len(hit) != 1:
                raise SystemExit(f"module {m} not found (or ambiguous) in {modules}")
            need.add(hit[0])
            need.update(dep[hit[0]])
        for f in sorted(need):
            cpio.file(f"lib/modules/{rel}/{f}", t.extractfile(members[f"{rel}/{f}"]).read())
        for f in ("modules.order", "modules.builtin", "modules.builtin.modinfo"):
            cpio.file(f"lib/modules/{rel}/{f}", t.extractfile(members[f"{rel}/{f}"]).read())
        cpio.file(f"lib/modules/{rel}/modules.dep",
                  "".join(f"{k}:{''.join(' ' + d for d in dep[k])}\n" for k in dep if k in need).encode())
    return rel, len(need)


def initramfs(a):
    keys = open(a.keys, "rb").read()
    if b"ssh-" not in keys:
        raise SystemExit(f"{a.keys}: no SSH public key - the rescue mode would be unreachable")
    c = Cpio()
    for d in DIRS:
        c.dir(d)
    c.dir("root", 0o700)
    c.dir("root/.ssh", 0o700)
    c.file("root/.ssh/authorized_keys", keys, 0o600)
    c.link("var/run", "../run")
    for path, text in ETC.items():
        c.file(path, text.encode(), 0o640 if path == "etc/shadow" else 0o644)
    c.file("init", open(a.init, "rb").read(), 0o755)
    seen = {}
    for b in BINARIES:
        add_elf(c, a.src, b, seen)
    for ap in busybox_applets(a.src):
        if ap != "bin/busybox":
            c.link(ap, "/bin/busybox")
    rel, n = add_modules(c, a.modules)
    data = c.pack()
    with open(a.output, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", compresslevel=9, mtime=0, filename="") as g:
        g.write(data)
    print(f"{a.output}: kernel {rel}, {n} rescue modules, {len(c.entries)} entries, "
          f"{os.path.getsize(a.output)} bytes")


def image(a):
    extra = " wallpanel.rescue" if a.rescue else ""
    img = mkrkboot.boot_image(open(a.kernel, "rb").read(), open(a.ramdisk, "rb").read(),
                              mkrkboot.resource_image([("rk-kernel.dtb", open(a.dtb, "rb").read())]),
                              open(a.dtb, "rb").read(), f"{CMDLINE} wallpanel.slot={a.slot}{extra}",
                              addrs="vendor")
    if len(img) > BOOT_PART_BYTES:
        raise SystemExit(f"image too large for the boot partition: {len(img)} > {BOOT_PART_BYTES}")
    with open(a.output, "wb") as f:
        f.write(img)
    print(f"{a.output}: {len(img)} bytes ({len(img) // 1048576} MiB), slot {a.slot}{extra}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("initramfs")
    i.add_argument("--modules", required=True, help="modules.tar.gz (<release>/... as modules_install)")
    i.add_argument("--keys", required=True, help="authorized_keys for the rescue SSH")
    i.add_argument("--src", default="/", help="root with busybox, dropbear and their libraries (default /)")
    i.add_argument("--init", default=os.path.join(HERE, "init"))
    i.add_argument("-o", "--output", required=True)
    m = sub.add_parser("image")
    m.add_argument("--kernel", required=True)
    m.add_argument("--dtb", required=True)
    m.add_argument("--ramdisk", required=True)
    m.add_argument("--slot", choices="AB", default="A")
    m.add_argument("--rescue", action="store_true", help="start the rescue mode (wallpanel.rescue)")
    m.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    initramfs(a) if a.cmd == "initramfs" else image(a)


if __name__ == "__main__":
    main()
