#!/bin/bash
# SPDX-License-Identifier: MIT
# Build a RAM-only boot image (installer/test system): mainline kernel + board DTB + Alpine initramfs.
#   build-test-image.sh [suffix]   -> build/out/boot-test-mainline[-suffix].img
set -euo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
. "$P/tools/lib.sh"
SUFFIX=${1:+-$1}
VARIANT=mainline
K=$P/build/src/linux
KO=$P/build/out/mainline
DTB=$KO/arch/arm64/boot/dts/rockchip/rk3399-iiyama-tw2424as.dtb
GCC=gcc-15.3.0-nolibc
CONSOLE="console=ttyS2,1500000n8"
ADDRS=mainline
IMG=boot-test-mainline$SUFFIX.img
OUT=$P/build/out
ROOT=$OUT/initramfs-$VARIANT
export PATH=$P/tools/bin:$P/tools/$GCC/aarch64-linux/bin:$PATH
MAKE="make -C $K O=$KO ARCH=arm64 CROSS_COMPILE=aarch64-linux-"

rm -rf "$ROOT" && mkdir -p "$ROOT"
tar -C "$ROOT" -xzf $P/tools/alpine-minirootfs-3.24.2-aarch64.tar.gz

# Only the module subtrees the board needs, to keep the image small
if grep -q '^CONFIG_MODULES=y' $KO/.config && [ -n "$(find $KO -name '*.ko' -print -quit)" ]; then
	STAGE=$OUT/modstage-$VARIANT
	rm -rf "$STAGE"
	$MAKE -s INSTALL_MOD_PATH="$STAGE" INSTALL_MOD_STRIP=1 modules_install
	KREL=$(ls "$STAGE/lib/modules")
	MD=$STAGE/lib/modules/$KREL/kernel
	KEEP="drivers/gpu drivers/video drivers/net/wireless/broadcom drivers/net/ethernet/stmicro
	      drivers/net/phy drivers/net/pcs drivers/bluetooth net/bluetooth sound drivers/usb drivers/phy
	      drivers/pwm drivers/iio drivers/input drivers/rtc drivers/regulator drivers/mfd
	      drivers/i2c drivers/mmc drivers/media/cec drivers/hid drivers/clk drivers/devfreq
	      drivers/thermal drivers/cpufreq drivers/soc crypto lib net/wireless net/rfkill"
	mkdir -p "$ROOT/lib/modules/$KREL/kernel"
	for d in $KEEP; do
		[ -d "$MD/$d" ] && mkdir -p "$ROOT/lib/modules/$KREL/kernel/$(dirname $d)" &&
			cp -a "$MD/$d" "$ROOT/lib/modules/$KREL/kernel/$d"
	done
	cp "$STAGE/lib/modules/$KREL"/modules.{order,builtin,builtin.modinfo} "$ROOT/lib/modules/$KREL/"
	depmod -b "$ROOT" "$KREL"
fi

# Host linux-firmware: DP firmware and the WiFi regulatory database
for f in rockchip/dptx.bin regulatory.db regulatory.db.p7s; do
	for src in /usr/lib/firmware/$f /usr/lib/firmware/$f.zst /usr/lib/firmware/$f.xz; do
		[ -f "$src" ] || continue
		mkdir -p "$ROOT/lib/firmware/$(dirname $f)"
		case $src in
			*.zst) zstd -qdc "$(readlink -f "$src")" > "$ROOT/lib/firmware/$f" ;;
			*.xz)  xz -dc "$(readlink -f "$src")" > "$ROOT/lib/firmware/$f" ;;
			*)     cp -L "$src" "$ROOT/lib/firmware/$f" ;;
		esac
	done
done
# WiFi/BT module: AMPAK AP6256 (BCM43456 = BCM4345 rev 9, SDIO 02d0:a9bf; BT BCM4345C5).
# linux-firmware has no 43456 image here, so use the vendor (bcmdhd) firmware,
# which brcmfmac loads as well; NVRAM and BT patch RAM are also from the vendor.
VF=$P/system/firmware/vendor
mkdir -p "$ROOT/lib/firmware/brcm"
cp $VF/fw_bcm43456c5_ag.bin "$ROOT/lib/firmware/brcm/brcmfmac43456-sdio.bin"
cp $VF/nvram_ap6256.txt "$ROOT/lib/firmware/brcm/brcmfmac43456-sdio.iiyama,tw2424as.txt"
cp $VF/nvram_ap6256.txt "$ROOT/lib/firmware/brcm/brcmfmac43456-sdio.txt"
cp $VF/BCM4345C5.hcd "$ROOT/lib/firmware/brcm/BCM4345C5.hcd"

# SSH maintenance access: dropbear from Alpine packages + project key
for a in $P/tools/apk/*.apk $P/tools/apk-installer/*.apk; do tar -xzf "$a" -C "$ROOT" --exclude='.PKGINFO' --exclude='.SIGN*' --exclude='.pre-*' --exclude='.post-*' 2>/dev/null; done
mkdir -p "$ROOT/root/.ssh" "$ROOT/etc/dropbear"
ssh_pubkeys > "$ROOT/root/.ssh/authorized_keys"; chmod 600 "$ROOT/root/.ssh/authorized_keys"
chmod 700 "$ROOT/root" "$ROOT/root/.ssh"
install -m755 $P/build/out/devmem "$ROOT/usr/sbin/devmem"

install -m755 $P/tools/init-test.sh "$ROOT/init"

(cd "$ROOT" && find . -print0 | cpio --null -o -H newc --owner=0:0 2>/dev/null | gzip -9) > $OUT/initramfs-$VARIANT.cpio.gz

python3 $P/tools/mkrkboot.py \
	--kernel $KO/arch/arm64/boot/Image \
	--dtb $DTB \
	--ramdisk $OUT/initramfs-$VARIANT.cpio.gz \
	--cmdline "console=tty1 $CONSOLE earlycon=uart8250,mmio32,0xff1a0000 rdinit=/init loglevel=7 fbcon=nodefer panic=10 panic_on_oops=1 softlockup_panic=1 ${EXTRA_CMDLINE:-}" \
	--addrs $ADDRS \
	-o $OUT/$IMG
ls -la $OUT/initramfs-$VARIANT.cpio.gz $OUT/$IMG
