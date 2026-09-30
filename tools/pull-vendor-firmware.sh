#!/bin/bash
# SPDX-License-Identifier: MIT
# Pull the AP6256 WiFi/BT firmware, NVRAM and BT patch RAM from the vendor
# Android partition into system/firmware/vendor/ (gitignored, proprietary).
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/local.env"   # ADB_SERIAL (gitignored)
P=$(cd "$(dirname "$0")/.." && pwd)
ADB="$P/tools/platform-tools/adb -s $ADB_SERIAL"
mkdir -p "$P/system/firmware/vendor"
for f in fw_bcm43456c5_ag.bin nvram_ap6256.txt BCM4345C5.hcd; do
	$ADB pull -q "/vendor/etc/firmware/$f" "$P/system/firmware/vendor/$f"
done
sha256sum "$P"/system/firmware/vendor/*
