#!/usr/bin/env python3
"""Build an Android boot image (header v2) for the Rockchip vendor U-Boot.

The vendor U-Boot loads the kernel DTB from a Rockchip resource image (RSCE)
stored in the boot image's "second" section, so the DTB is placed there as
rk-kernel.dtb and additionally in the v2 dtb section. Load addresses match
the stock TW2424AS boot image.

  mkrkboot.py --kernel Image --dtb board.dtb --ramdisk initrd.cpio.gz \
              --cmdline "..." [--logo logo.bmp] -o boot-test.img
"""
import argparse
import hashlib
import struct

PAGE = 2048
BLK = 512

# Load address profiles:
#  vendor:   stock header values (dumps/boot.img), for the Rockchip vendor U-Boot
#  mainline: mainline U-Boot rk3399 defaults (fdt_addr_r, kernel_addr_r,
#            ramdisk_addr_r). Mainline copies a v2 ramdisk to ramdisk_addr and
#            moves the kernel to a 2 MiB boundary, so the vendor layout would
#            let a 40 MB kernel overwrite ramdisk and DTB.
ADDRS = {
    "vendor": dict(kernel=0x10008000, ramdisk=0x11000000, second=0x10F00000,
                   tags=0x10000100, dtb=0x11F00000),
    "mainline": dict(kernel=0x02080000, ramdisk=0x06000000, second=0x05F00000,
                     tags=0x00000100, dtb=0x01F00000),
}
OS_VERSION = 0x18000163


def pad(data, align):
    return data + b"\0" * (-len(data) % align)


def resource_image(files):
    """RSCE: 1 block header, 1 block per index entry, then 512-byte aligned contents."""
    hdr = b"RSCE" + struct.pack("<HHBBBBI", 0, 0, 1, 1, 1, 0, len(files))
    hdr = pad(hdr, BLK)
    offset = 1 + len(files)
    entries, blobs = b"", b""
    for name, data in files:
        # tag, name[220], sha1[32], hash_size, blk_offset, size
        e = b"ENTR" + name.encode()[:220].ljust(220, b"\0")
        e += hashlib.sha1(data).digest().ljust(32, b"\0") + struct.pack("<III", 20, offset, len(data))
        entries += pad(e, BLK)
        blob = pad(data, BLK)
        blobs += blob
        offset += len(blob) // BLK
    return hdr + entries + blobs


def boot_image(kernel, ramdisk, second, dtb, cmdline, name=b"", addrs="vendor"):
    a = ADDRS[addrs]
    if len(cmdline) >= 512:
        raise SystemExit("cmdline too long (max 511 bytes)")
    sha = hashlib.sha1()
    for blob in (kernel, ramdisk, second, b"", dtb):  # recovery_dtbo empty
        sha.update(blob)
        sha.update(struct.pack("<I", len(blob)))
    hdr = b"ANDROID!"
    hdr += struct.pack("<10I", len(kernel), a["kernel"], len(ramdisk), a["ramdisk"],
                       len(second), a["second"], a["tags"], PAGE, 2, OS_VERSION)
    hdr += name[:16].ljust(16, b"\0")
    hdr += cmdline.encode().ljust(512, b"\0")
    hdr += sha.digest().ljust(32, b"\0")
    hdr += b"\0" * 1024                                   # extra_cmdline
    hdr += struct.pack("<IQI", 0, 0, 1660)                # recovery_dtbo size/offset, header_size
    hdr += struct.pack("<IQ", len(dtb), a["dtb"])
    assert len(hdr) == 1660, len(hdr)
    return b"".join(pad(x, PAGE) for x in (hdr, kernel, ramdisk, second, dtb))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--dtb", required=True)
    ap.add_argument("--ramdisk", required=True)
    ap.add_argument("--cmdline", default="")
    ap.add_argument("--logo", help="logo.bmp shown by U-Boot")
    ap.add_argument("--addrs", choices=sorted(ADDRS), default="vendor",
                    help="load address profile (default: vendor)")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()

    dtb = open(a.dtb, "rb").read()
    files = [("rk-kernel.dtb", dtb)]
    if a.logo:
        files.append(("logo.bmp", open(a.logo, "rb").read()))
    img = boot_image(open(a.kernel, "rb").read(), open(a.ramdisk, "rb").read(),
                     resource_image(files), dtb, a.cmdline, addrs=a.addrs)
    open(a.output, "wb").write(img)
    print(f"{a.output}: {len(img)} bytes")


if __name__ == "__main__":
    main()
