#!/bin/bash
# SPDX-License-Identifier: MIT
# Android -> Rockusb loader -> maskrom -> mainline U-Boot in RAM -> fastboot boot <image>.
# Nothing is written to the eMMC on this path.
. "$(dirname "$(readlink -f "$0")")/local.env"   # ADB_SERIAL, SHELLY_IP, ... (gitignored)
set -uo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
IMG=${1:-$P/build/out/boot-test-mainline.img}
ADB="$P/tools/platform-tools/adb -s $ADB_SERIAL"
RKD=$P/tools/rkdeveloptool/rkdeveloptool
FB=$P/tools/platform-tools/fastboot
if ! $RKD ld 2>/dev/null | grep -q Maskrom; then
	if ! $RKD ld 2>/dev/null | grep -q Loader; then
		for try in 1 2 3 4 5; do
			timeout 300 $ADB wait-for-device || { echo "no adb"; exit 1; }
			sleep 10; $ADB reboot loader 2>/dev/null
			for i in $(seq 1 20); do $RKD ld 2>/dev/null | grep -q Loader && break 2; sleep 2; done
		done
	fi
	timeout 10 $RKD rd 3; sleep 5
fi
$RKD ld | grep -q Maskrom || { echo "no maskrom"; exit 1; }
timeout 60 $P/build/src/rkusbboot/rkusbboot -p 0x330c $P/build/out/u-boot/u-boot-rockchip-usb471.bin $P/build/out/u-boot/u-boot-rockchip-usb472.bin | tail -1
for i in $(seq 1 30); do lsusb | grep -q "18d1:4ee0" && break; sleep 1; done
lsusb | grep -q "18d1:4ee0" || { echo "U-Boot fastboot did not appear"; exit 2; }
echo "U-Boot up $(date +%T)"
[ "$IMG" = none ] && exit 0
timeout 120 $FB boot "$IMG" 2>&1 | tail -1
echo "kernel started $(date +%T)"
