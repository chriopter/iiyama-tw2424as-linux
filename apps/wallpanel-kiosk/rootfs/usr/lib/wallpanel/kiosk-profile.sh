#!/bin/sh
# The Chromium profile lives in RAM (/run/wallpanel/chromium): Chromium rewrites its databases, caches,
# history and metrics all the time. What has to survive a reboot is saved to the eMMC when the kiosk
# stops (restart, reboot, shutdown) and copied back at its first start after boot:
#   Home Assistant login (Local Storage: hassTokens), site permissions (microphone) and preferences,
#   cookies, saved logins.
# After a power cut the previous save is used (the HA refresh token in it stays valid).
#   kiosk-profile.sh restore   (kiosk start, as user wallpanel)
#   kiosk-profile.sh save      (kiosk stop, as root; makes / writable via wallpanel-rw)
set -eu
SEED=/var/lib/wallpanel/chromium
LIVE=/run/wallpanel/chromium
KEEP='Local State
Default/Preferences
Default/Secure Preferences
Default/Cookies
Default/Cookies-journal
Default/Local Storage
Default/Login Data
Default/Login Data-journal
Default/Web Data
Default/Web Data-journal'

# the KEEP entries that exist below $1
present() { printf '%s\n' "$KEEP" | while IFS= read -r i; do [ ! -e "$1/$i" ] || printf '%s\n' "$i"; done; }

# copy the KEEP entries from $1 to the (new) directory $2
copy() {
	mkdir -p "$2"
	l=$(present "$1")
	[ -z "$l" ] || printf '%s\n' "$l" | tar -C "$1" -cf - -T - | tar -C "$2" -xf -
}

case ${1:-} in
restore)
	[ ! -d "$LIVE" ] || exit 0  # kiosk restarted within this boot: keep the live profile
	src=$SEED
	[ -d "$src" ] || src=$SEED.old  # power cut in the middle of a save
	rm -rf "$LIVE.new"
	if [ -d "$src" ]; then copy "$src" "$LIVE.new"; else mkdir -p "$LIVE.new"; fi
	mv "$LIVE.new" "$LIVE" ;;
save)
	[ -d "$LIVE" ] || exit 0
	# nothing changed since the last save: no write at all
	if present "$LIVE" | while IFS= read -r i; do diff -rq "$SEED/$i" "$LIVE/$i" >/dev/null 2>&1 || exit 1; done
	then exit 0; fi
	[ -n "${WALLPANEL_RW:-}" ] || ! command -v wallpanel-rw >/dev/null || exec wallpanel-rw run "$0" save
	rm -rf "$SEED.new"
	copy "$LIVE" "$SEED.new"
	chown -R wallpanel:wallpanel "$SEED.new"
	rm -rf "$SEED.old"
	[ ! -d "$SEED" ] || mv "$SEED" "$SEED.old"
	mv "$SEED.new" "$SEED"
	rm -rf "$SEED.old"
	sync
	echo "kiosk profile saved ($(du -sh "$SEED" | cut -f1))" ;;
*)
	sed -n '2,9p' "$0"; exit 2 ;;
esac
