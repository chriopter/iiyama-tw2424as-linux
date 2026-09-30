#!/bin/bash
# Update the wallpanel apps and the rootfs overlay on an installed device without reinstalling.
# Works against the running system (root on /) or the rescue initramfs (mounts LABEL=wallpanel-root).
set -euo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
SSH="$P/tools/tssh"
. "$P/tools/local.env"
. "$P/tools/lib.sh"

if $SSH 'grep -q " / ext4" /proc/mounts'; then
	R=/ MODE=running
else
	$SSH 'mkdir -p /mnt/t; mountpoint -q /mnt/t || mount "$(findfs LABEL=wallpanel-root)" /mnt/t'
	R=/mnt/t MODE=rescue
fi
echo "target: $MODE system, root at $R"

push_rootfs "$SSH" "$R"
write_conf "$SSH" "$R"
$SSH "cd $R && grep -qx brcmfmac etc/modules || echo brcmfmac >> etc/modules"
set_root_password "$SSH" "$R"

if [ $MODE = running ]; then
	$SSH 'udevadm control --reload; rc-service wallpanel-api restart; rc-service wallpanel-kiosk restart; rc-service dropbear restart' || true
else
	$SSH 'sync; umount /mnt/t'
fi
echo "done ($MODE)"
