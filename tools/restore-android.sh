#!/bin/bash
# Roll back to the original Android from the verified backup in dumps/.
#
#   tools/restore-android.sh ssh       device runs Linux (normal, rescue or RAM installer), root@10.42.0.1
#   tools/restore-android.sh rockusb   device in Rockusb: hold Volume+ while powering on (USB-C
#                                      cable to this PC connected), or "reboot loader" from Linux
#
# Writes boot, recovery (original images) and a misc boot command
# "boot-recovery --wipe_data": the original Android recovery then formats
# userdata (removes the Linux root filesystem) and Android starts like after a
# factory reset. Bootloader and super (Android system) were never modified.
set -euo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
D=$P/dumps
T=$P/build/out/restore
MODE=${1:-}
die() { echo "error: $*" >&2; exit 1; }

(cd "$D" && sha256sum -c --quiet SHA256SUMS) || die "backup in dumps/ missing or damaged"
mkdir -p "$T"

# misc with an Android bootloader message (offset 0, used by Android >= 10)
python3 - "$D/misc.img" "$T/misc-wipe.img" <<'PY'
import sys
b = bytearray(open(sys.argv[1], 'rb').read())
msg = bytearray(2048)
msg[0:13] = b'boot-recovery'                        # command[32]
rec = b'recovery\n--wipe_data\n'
msg[64:64 + len(rec)] = rec                         # status[32] at 32, recovery[768] at 64
b[0:2048] = msg
open(sys.argv[2], 'wb').write(b)
PY
head -c $((64 * 1024 * 1024)) /dev/zero > "$T/zero-64M.img"

case $MODE in
ssh)
	SSH="$P/tools/tssh"
	PARTS=$($SSH 'for d in /sys/class/block/mmcblk0p*; do n=$(sed -n s/^PARTNAME=//p $d/uevent); echo "$n=/dev/$(basename $d)"; done')
	dev() { echo "$PARTS" | sed -n "s/^$1=//p"; }
	for pair in "boot:$D/boot.img" "recovery:$D/recovery.img" "misc:$T/misc-wipe.img"; do
		n=${pair%%:*} f=${pair#*:} d=$(dev "${pair%%:*}")
		[ -n "$d" ] || die "partition $n not found"
		cat "$f" | $SSH "cat > /tmp/r.img && dd if=/tmp/r.img of=$d bs=1M conv=fsync 2>/dev/null && rm /tmp/r.img"
		[ "$($SSH "head -c $(stat -c%s "$f") $d | sha256sum" | cut -c1-64)" = "$(sha256sum < "$f" | cut -c1-64)" ] ||
			die "verify of $n failed"
		echo "$n restored and verified"
	done
	$SSH "umount -a -t ext4 2>/dev/null; dd if=/dev/zero of=$(dev userdata) bs=1M count=64 conv=fsync 2>/dev/null; sync"
	echo "userdata header cleared; rebooting"
	$SSH 'reboot -f' || true ;;
rockusb)
	R=$P/tools/rkdeveloptool/rkdeveloptool
	$R ld | grep -q Loader || die "no Rockusb device (hold Volume+ while powering on, USB-C connected)"
	lba() { $R ppt | awk -v n="$1" '$3 == n {print $2}'; }
	for pair in "boot:$D/boot.img" "recovery:$D/recovery.img" "misc:$T/misc-wipe.img" "userdata:$T/zero-64M.img"; do
		n=${pair%%:*} f=${pair#*:} l=$(lba "${pair%%:*}")
		[ -n "$l" ] || die "partition $n not found"
		$R wl "0x$l" "$f" | tail -1
	done
	# note: Rockusb read-back is unreliable above ~32 MiB (see README, Findings); Android verifies by booting
	$R rd && echo "reset; Android recovery wipes userdata, then Android starts" ;;
*)
	sed -n '2,12p' "$0"; exit 1 ;;
esac
