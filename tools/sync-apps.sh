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
setup_storage "$SSH" "$R"  # tmpfs for /tmp and /var/log: active after the next reboot
$SSH "cd $R && grep -qx brcmfmac etc/modules || echo brcmfmac >> etc/modules"
# kernel update health check (wallpanel-update install-release) in the default runlevel
$SSH "ln -sf /etc/init.d/wallpanel-kernel-health $R/etc/runlevels/default/wallpanel-kernel-health"
set_root_password "$SSH" "$R"

if [ $MODE = running ]; then
	# added to system/rootfs/packages after the first installations (kernel release signatures);
	# not "apk add <all packages>": that would drop the version pins of our cage/wallpanel-gamma
	$SSH 'apk info -e openssh-keygen >/dev/null || apk add --wait 300 -q openssh-keygen' || echo "warning: apk add openssh-keygen failed" >&2
	# AirPlay receiver (apps/wallpanel-airplay), also added after the first installations
	$SSH 'for p in shairport-sync avahi avahi-openrc dbus-openrc; do apk info -e $p >/dev/null || m="$m $p"; done
		[ -z "$m" ] || apk add --wait 300 -q $m' || echo "warning: apk add of the AirPlay packages failed" >&2
	$SSH "[ ! -x $R/usr/bin/shairport-sync ] || for s in dbus avahi-daemon wallpanel-airplay; do ln -sf /etc/init.d/\$s $R/etc/runlevels/default/\$s; done"
	$SSH 'udevadm control --reload; rc-service wallpanel-airplay restart; rc-service wallpanel-api restart; rc-service wallpanel-kiosk restart; rc-service dropbear restart' || true
else
	$SSH 'sync; umount /mnt/t'
fi
echo "done ($MODE)"
