#!/bin/bash
# Build build/out/wallpanel-boot.img for the vendor U-Boot (boot/recovery partition):
# mainline Image (uncompressed: the vendor U-Boot hangs on Image.gz) + board DTB (with symbols for the vendor DTBO overlay) +
# rescue-capable initramfs (system/rootfs/initramfs/init, busybox, dropbear, display modules).
set -euo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
K=$P/build/src/linux
KO=$P/build/out/mainline
OUT=$P/build/out
ROOT=$OUT/bootimg-initramfs
BOOT_PART_BYTES=$((45 * 1024 * 1024))   # smallest slot (boot); recovery is 96 MiB
. "$P/tools/lib.sh"
export PATH=$P/tools/bin:$P/tools/gcc-15.3.0-nolibc/aarch64-linux/bin:$PATH

rm -rf "$ROOT" && mkdir -p "$ROOT"
tar -C "$ROOT" -xzf "$P"/tools/alpine-minirootfs-*-aarch64.tar.gz
for a in dropbear zlib utmps-libs skalibs-libs; do
	tar -xzf "$P"/tools/apk/$a-[0-9]*.apk -C "$ROOT" --exclude='.PKGINFO' --exclude='.SIGN*' \
		--exclude='.pre-*' --exclude='.post-*' 2>/dev/null
done

# Maintenance SSH keys: all public keys in tools/ssh/ (gitignored, created on first use)
mkdir -p "$ROOT/root/.ssh"
ssh_pubkeys > "$ROOT/root/.ssh/authorized_keys"
chmod 700 "$ROOT/root" "$ROOT/root/.ssh"; chmod 600 "$ROOT/root/.ssh/authorized_keys"

# Display modules (rescue notice on the panel) incl. dependencies
KREL=$(cat "$KO/include/config/kernel.release")
STAGE=$OUT/modstage-bootimg
rm -rf "$STAGE"
make -s -C "$K" O="$KO" ARCH=arm64 CROSS_COMPILE=aarch64-linux- INSTALL_MOD_PATH="$STAGE" INSTALL_MOD_STRIP=1 modules_install
MD=$STAGE/lib/modules/$KREL
need=$(for m in rockchipdrm panel-iiyama-tw2424as pwm_bl; do
	awk -v m="$m" -F: '$1 ~ "/"m".ko" {print $1; n=split($2,d," "); for(i=1;i<=n;i++) print d[i]}' "$MD/modules.dep"
	done | sort -u)
for f in $need; do
	mkdir -p "$ROOT/lib/modules/$KREL/$(dirname "$f")"
	cp "$MD/$f" "$ROOT/lib/modules/$KREL/$f"
done
cp "$MD"/modules.{order,builtin,builtin.modinfo} "$ROOT/lib/modules/$KREL/"
depmod -b "$ROOT" "$KREL"

install -m755 "$P/system/rootfs/initramfs/init" "$ROOT/init"
(cd "$ROOT" && find . -print0 | cpio --null -o -H newc --owner=0:0 2>/dev/null | gzip -9) > "$OUT/wallpanel-initramfs.cpio.gz"

# wallpanel-boot.img (slot A), -slotB.img (update tests), -rescue.img (rescue mode, for Rockusb recovery)
CMDLINE="console=tty1 earlycon=uart8250,mmio32,0xff1a0000 rootwait panic=10 loglevel=4"
for v in A:wallpanel-boot B:wallpanel-boot-slotB A-rescue:wallpanel-boot-rescue; do
	slot=${v%%:*} img=$OUT/${v#*:}.img extra=
	[ "$slot" = A-rescue ] && slot=A extra=" wallpanel.rescue"
	python3 "$P/tools/mkrkboot.py" \
		--kernel "$KO/arch/arm64/boot/Image" \
		--dtb "$KO/arch/arm64/boot/dts/rockchip/rk3399-iiyama-tw2424as.dtb" \
		--ramdisk "$OUT/wallpanel-initramfs.cpio.gz" \
		--cmdline "$CMDLINE wallpanel.slot=$slot$extra" \
		--addrs vendor \
		-o "$img" > /dev/null
	size=$(stat -c%s "$img")
	[ "$size" -le "$BOOT_PART_BYTES" ] || { echo "image too large for boot partition: $img $size" >&2; exit 1; }
	echo "OK: $img $((size / 1024 / 1024)) MiB"
done
# All modules for the root file system (wallpanel-update install <img> <modules.tar.gz>)
tar --owner=0 --group=0 -czf "$OUT/wallpanel-modules.tar.gz" -C "$STAGE/lib/modules" "$KREL"
echo "OK: $OUT/wallpanel-modules.tar.gz"
echo "kernel $KREL, rescue modules: $(echo "$need" | wc -l)"
