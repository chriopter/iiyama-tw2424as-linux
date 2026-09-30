#!/bin/bash
# Read-only dump of all eMMC partitions (except userdata) via adb root
. "$(dirname "$(readlink -f "$0")")/local.env"   # ADB_SERIAL, SHELLY_IP, ... (gitignored)
set -e
P=$(cd "$(dirname "$0")/.." && pwd)
cd "$P/dumps"
A="../tools/platform-tools/adb -s $ADB_SERIAL"
for p in security uboot trust misc dtbo vbmeta boot recovery backup cache metadata update baseparameter super mmcblk2boot0 mmcblk2boot1; do
  $A exec-out "dd if=/dev/block/by-name/$p bs=1M 2>/dev/null" > $p.img
  $A shell "sha256sum /dev/block/by-name/$p" | awk -v f=$p.img '{print $1"  "f}' >> SHA256SUMS.device
  echo "$p $(stat -c%s $p.img)"
done
# first 16 MiB raw (GPT + idbloader/miniloader area)
$A exec-out "dd if=/dev/block/mmcblk2 bs=1M count=16 2>/dev/null" > mmcblk2_first16M.img
$A shell "dd if=/dev/block/mmcblk2 bs=1M count=16 2>/dev/null | sha256sum" | awk '{print $1"  mmcblk2_first16M.img"}' >> SHA256SUMS.device
sha256sum *.img > SHA256SUMS.local
diff <(sort -k2 SHA256SUMS.device) <(sort -k2 SHA256SUMS.local) && echo "ALL CHECKSUMS MATCH"
