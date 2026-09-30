#!/bin/bash
# SPDX-License-Identifier: MIT
# Resumable, chunked read-only dump: dump_chunked.sh <partition-name> [chunk_MiB]
. "$(dirname "$(readlink -f "$0")")/local.env"   # ADB_SERIAL, SHELLY_IP, ... (gitignored)
set -u
P=$(cd "$(dirname "$0")/.." && pwd)
PART=$1; C=${2:-256}
A="$P/tools/platform-tools/adb -s $ADB_SERIAL"
OUT=$P/dumps/chunks/$PART; mkdir -p "$OUT"
SIZE=$($A shell "blockdev --getsize64 /dev/block/by-name/$PART" | tr -d '\r')
N=$(( (SIZE + C*1048576 - 1) / (C*1048576) ))
for ((i=0;i<N;i++)); do
  f=$OUT/$(printf %04d $i).bin
  want=$($A shell "dd if=/dev/block/by-name/$PART bs=1M skip=$((i*C)) count=$C 2>/dev/null | sha256sum" | awk '{print $1}')
  for try in 1 2 3 4 5; do
    [ -f $f ] && [ "$(sha256sum $f | awk '{print $1}')" = "$want" ] && break
    timeout 90 $A exec-out "dd if=/dev/block/by-name/$PART bs=1M skip=$((i*C)) count=$C 2>/dev/null" > $f
  done
  got=$(sha256sum $f | awk '{print $1}')
  [ "$got" = "$want" ] && echo "$PART chunk $((i+1))/$N OK" || { echo "$PART chunk $i FAILED"; exit 1; }
done
cat "$OUT"/*.bin > "$P/dumps/$PART.img" && rm -rf "$OUT"
echo "$PART DONE $(stat -c%s "$P/dumps/$PART.img") of $SIZE"
