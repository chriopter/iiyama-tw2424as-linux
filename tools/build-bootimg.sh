#!/bin/bash
# Build the boot images for the vendor U-Boot (boot/recovery partition) from the built kernel:
# mainline Image (uncompressed: the vendor U-Boot hangs on Image.gz) + board DTB (with symbols for the
# vendor DTBO overlay) + rescue-capable initramfs (init, busybox, dropbear, display modules, keys).
# Same code path as on the device (wallpanel-update fetch): ./build.sh release, then
# system/rootfs/overlay/usr/lib/wallpanel/boot/mkboot.py; busybox/dropbear come from the Alpine
# minirootfs + tools/apk here, from the installed system there.
set -euo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
OUT=$P/build/out
R=$OUT/release
SRCROOT=$OUT/bootimg-rootfs
MKBOOT=$P/system/rootfs/overlay/usr/lib/wallpanel/boot/mkboot.py
. "$P/tools/lib.sh"

"$P/build.sh" release > /dev/null

rm -rf "$SRCROOT" && mkdir -p "$SRCROOT"
tar -C "$SRCROOT" -xzf "$P"/tools/alpine-minirootfs-*-aarch64.tar.gz
for a in dropbear zlib utmps-libs skalibs-libs; do
	tar -xzf "$P"/tools/apk/$a-[0-9]*.apk -C "$SRCROOT" --exclude='.PKGINFO' --exclude='.SIGN*' \
		--exclude='.pre-*' --exclude='.post-*' 2>/dev/null
done

# Maintenance SSH keys: all public keys in tools/ssh/ (gitignored, created on first use)
ssh_pubkeys > "$OUT/bootimg-authorized_keys"
python3 "$MKBOOT" initramfs --src "$SRCROOT" --modules "$R/modules.tar.gz" \
	--keys "$OUT/bootimg-authorized_keys" -o "$OUT/wallpanel-initramfs.cpio.gz"

# wallpanel-boot.img (slot A), -slotB.img (update tests), -rescue.img (rescue mode, for Rockusb recovery)
for v in A:wallpanel-boot B:wallpanel-boot-slotB A-rescue:wallpanel-boot-rescue; do
	slot=${v%%:*} img=$OUT/${v#*:}.img extra=
	[ "$slot" = A-rescue ] && slot=A extra=--rescue
	python3 "$MKBOOT" image --kernel "$R/Image" --dtb "$R/rk3399-iiyama-tw2424as.dtb" \
		--ramdisk "$OUT/wallpanel-initramfs.cpio.gz" --slot "$slot" $extra -o "$img"
done
# All modules for the root file system (wallpanel-update install <img> <modules.tar.gz>)
cp "$R/modules.tar.gz" "$OUT/wallpanel-modules.tar.gz"
echo "OK: $OUT/wallpanel-modules.tar.gz"
