#!/bin/bash
# SPDX-License-Identifier: MIT
# Install the wallpanel system onto the eMMC.
#
# Precondition: the device runs the RAM installer (tools/ramboot.sh build/out/boot-test-mainline.img),
# reachable at root@10.42.0.1 over USB-C. What gets written:
#   userdata  -> ext4 LABEL=wallpanel-root with Alpine Linux + wallpanel apps
#   boot      -> build/out/wallpanel-boot.img (slot A)
#   recovery  -> build/out/wallpanel-boot.img (slot B)
# Untouched: bootloader (idbloader/uboot/trust), misc, dtbo, vbmeta, super (Android system).
# Rollback: tools/restore-android.sh (needs the verified backup in dumps/).
set -euo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
. "$P/tools/local.env"
. "$P/tools/lib.sh"
SSH="$P/tools/tssh"
IMG=$P/build/out/wallpanel-boot.img
MODS=$P/build/out/modstage-bootimg/lib/modules
REPO=https://dl-cdn.alpinelinux.org/alpine/v3.24
say() { printf '\n== %s\n' "$*"; }
die() { echo "error: $*" >&2; exit 1; }

say "preconditions"
[ -n "${WIFI_SSID:-}" ] && [ -n "${WIFI_PSK:-}" ] && [ -n "${HA_URL:-}" ] || die "WIFI_SSID, WIFI_PSK, HA_URL needed in tools/local.env"
(cd "$P/dumps" && sha256sum -c --quiet SHA256SUMS) || die "backup in dumps/ missing or damaged"
[ -f "$IMG" ] && [ -d "$MODS" ] || die "run tools/build-bootimg.sh first"
KREL=$(ls "$MODS")
[ -x "$P/build/out/rebootmode" ] || die "run ./build.sh helpers first"
[ "$($SSH uname -r)" = "$KREL" ] || die "device does not run the $KREL RAM installer"
$SSH 'grep -q wallpanel-root /proc/mounts' && die "installer must not run from the installed system"

say "partitions"
PARTS=$($SSH 'for d in /sys/class/block/mmcblk0p*; do n=$(sed -n s/^PARTNAME=//p $d/uevent); echo "$n=/dev/$(basename $d)"; done')
dev() { echo "$PARTS" | sed -n "s/^$1=//p"; }
BOOT=$(dev boot) RECOVERY=$(dev recovery) USERDATA=$(dev userdata)
echo "boot=$BOOT recovery=$RECOVERY userdata=$USERDATA"
[ -n "$BOOT" ] && [ -n "$RECOVERY" ] && [ -n "$USERDATA" ] || die "partitions not found"

# Only overwrite boot/recovery if they hold the Android backup or a wallpanel image
check_slot() { # dev backup
	local want_bak want_img have
	want_bak=$(sha256sum < "$P/dumps/$2" | cut -c1-64)
	have=$($SSH "sha256sum < $1" | cut -c1-64)
	[ "$have" = "$want_bak" ] && { echo "$1: Android backup content"; return; }
	$SSH "head -c 8 $1" | grep -q "ANDROID!" &&
		$SSH "head -c 576 $1 | tail -c 512" | grep -qa "wallpanel.slot=" && { echo "$1: wallpanel image"; return; }
	die "$1 holds unknown content - refusing to overwrite"
}
check_slot "$BOOT" boot.img
check_slot "$RECOVERY" recovery.img

say "WiFi for package download"
$SSH "ip link set wlan0 up; iw dev wlan0 set power_save off 2>/dev/null
	wpa_passphrase '$WIFI_SSID' '$WIFI_PSK' > /tmp/wpa.conf
	pgrep wpa_supplicant >/dev/null || wpa_supplicant -B -i wlan0 -c /tmp/wpa.conf >/dev/null
	udhcpc -i wlan0 -q -t 15 -n >/dev/null; ip -4 addr show wlan0 | grep inet"

say "root filesystem on $USERDATA"
$SSH "umount /mnt/t 2>/dev/null; mkfs.ext4 -q -F -L wallpanel-root -m 1 $USERDATA && mkdir -p /mnt/t && mount $USERDATA /mnt/t"
PKGS=$(grep -vE '^\s*(#|$)' "$P/system/rootfs/packages" | tr '\n' ' ')
$SSH "mkdir -p /mnt/t/etc/apk && printf '%s/main\n%s/community\n' $REPO $REPO > /mnt/t/etc/apk/repositories
	cp -a /etc/apk/keys /mnt/t/etc/apk/
	apk --root /mnt/t --initdb --no-interactive --update-cache add $PKGS 2>&1 | tail -3"

say "cage with gamma control (system/cage), pinned in /etc/apk/world"
if [ -f "$P/build/out/cage-$(cage_version).apk" ]; then
	install_cage "$SSH" /mnt/t
else  # first install without a build: build it inside the new system (chroot, build deps removed again)
	"$P/tools/build-cage.sh" --root /mnt/t --install
fi

say "wallpanel-gamma (system/wallpanel-gamma), pinned in /etc/apk/world"
if [ -f "$P/build/out/wallpanel-gamma-$(gamma_version).apk" ]; then
	install_gamma "$SSH" /mnt/t
else  # same as cage: build it inside the new system
	"$P/tools/build-wallpanel-gamma.sh" --root /mnt/t --install
fi

say "wallpanel apps, overlay, kernel modules, firmware"
$SSH 'mkdir -p /mnt/t/etc/wallpanel /mnt/t/lib/firmware/brcm'
push_rootfs "$SSH" /mnt/t
tar --owner=0 --group=0 -cf - -C "$MODS/.." "modules/$KREL" | $SSH 'tar -xf - -C /mnt/t/lib'
cat "$P/system/firmware/vendor/fw_bcm43456c5_ag.bin" | $SSH 'cat > /mnt/t/lib/firmware/brcm/brcmfmac43456-sdio.bin'
cat "$P/system/firmware/vendor/nvram_ap6256.txt" | $SSH 'cat > /mnt/t/lib/firmware/brcm/brcmfmac43456-sdio.iiyama,tw2424as.txt'
cat "$P/system/firmware/vendor/BCM4345C5.hcd" | $SSH 'cat > /mnt/t/lib/firmware/brcm/BCM4345C5.hcd'

say "device configuration (from tools/local.env, never in git)"
$SSH "umask 077; mkdir -p /mnt/t/etc/wpa_supplicant
	wpa_passphrase '$WIFI_SSID' '$WIFI_PSK' | grep -v '#psk=' > /mnt/t/etc/wpa_supplicant/wpa_supplicant.conf
	echo 'LABEL=wallpanel-root / ext4 defaults,noatime,commit=60 0 1' > /mnt/t/etc/fstab"
write_conf "$SSH" /mnt/t

say "services and users"
$SSH 'for d in dev proc sys; do mount --bind /$d /mnt/t/$d; done; chroot /mnt/t /bin/sh -e -c "
	addgroup -S seat 2>/dev/null || true
	adduser -D -h /var/lib/wallpanel -s /sbin/nologin wallpanel 2>/dev/null || true
	for g in video input audio seat render; do addgroup wallpanel \$g 2>/dev/null || true; done
	chown root:wallpanel /etc/wallpanel/wallpanel.conf; chmod 640 /etc/wallpanel/wallpanel.conf
	echo brcmfmac >> /etc/modules
	depmod -a '"$KREL"'
	for s in devfs dmesg udev udev-trigger udev-settle; do rc-update add \$s sysinit >/dev/null; done
	for s in modules sysctl hostname bootmisc hwclock syslog wallpanel-usb wallpanel-console watchdog; do rc-update add \$s boot >/dev/null; done
	for s in networking wpa_supplicant chronyd dropbear seatd udev-postmount local wallpanel-kiosk wallpanel-api; do rc-update add \$s default >/dev/null; done
	for s in mount-ro killprocs savecache; do rc-update add \$s shutdown >/dev/null; done
	rc-update show | grep -c . >/dev/null
"; for d in sys proc dev; do umount /mnt/t/$d; done; df -h /mnt/t | tail -1'

say "root password"
set_root_password "$SSH" /mnt/t

say "kernel slots (boot = A, recovery = B)"
IMG_B=$P/build/out/wallpanel-boot-slotB.img
python3 - "$IMG" "$IMG_B" <<'PY'
import sys
b = bytearray(open(sys.argv[1], 'rb').read())
i = b.find(b'wallpanel.slot=A', 64, 576)
assert i > 0, 'no wallpanel.slot=A in cmdline'
b[i + 15] = ord('B'); open(sys.argv[2], 'wb').write(b)
PY
for pair in "$BOOT:$IMG" "$RECOVERY:$IMG_B"; do
	d=${pair%%:*} f=${pair#*:}
	H=$(sha256sum < "$f" | cut -c1-64); N=$(stat -c%s "$f")
	cat "$f" | $SSH 'cat > /tmp/slot.img'
	$SSH "dd if=/tmp/slot.img of=$d bs=1M conv=fsync 2>/dev/null; sync"
	[ "$($SSH "head -c $N $d | sha256sum" | cut -c1-64)" = "$H" ] || die "verify of $d failed"
	echo "$d written and verified ($(basename "$f"))"
done
$SSH 'sync; umount /mnt/t'

say "done - reboot the device (e.g. 'reboot -f' over ssh) to start the wallpanel system from slot A"
