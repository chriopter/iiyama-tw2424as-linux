#!/bin/sh
# SPDX-License-Identifier: MIT
# wallpanel-kiosk: Chromium fullscreen with the Home Assistant dashboard (cage, Wayland).
# Runs as the unprivileged user "wallpanel"; started and respawned by OpenRC.
set -eu
. /etc/wallpanel/wallpanel.conf
# Start page set from Home Assistant (MQTT text "Home page", saved by wallpanel-api) wins over the config
H=$(python3 -c "import json; print(json.load(open('/var/lib/wallpanel/api-state.json')).get('home_url', ''))" 2>/dev/null) || H=
[ -n "$H" ] && URL=$H
: "${URL:?URL not set in /etc/wallpanel/wallpanel.conf}"
ROTATION=${ROTATION:-180}
OUTPUT=${OUTPUT:-DSI-1}
DATA=/var/lib/wallpanel
EXT=$DATA/extension

export XDG_RUNTIME_DIR=/run/wallpanel LIBSEAT_BACKEND=seatd HOME=$DATA
mkdir -p "$XDG_RUNTIME_DIR" "$DATA/chromium" "$EXT"

# Trusted server certificates (e.g. a self-signed Home Assistant certificate): every
# /etc/wallpanel/trust/*.crt goes into Chromium's NSS database, CA:FALSE certificates as trusted peer.
NSS=sql:$DATA/.pki/nssdb
mkdir -p "$DATA/.pki/nssdb"
[ -f "$DATA/.pki/nssdb/cert9.db" ] || certutil -N -d "$NSS" --empty-password
for c in /etc/wallpanel/trust/*.crt; do
	[ -f "$c" ] || continue
	t=$(python3 - "$c" <<'PY'
import base64, sys
der = base64.b64decode(''.join(l for l in open(sys.argv[1]) if not l.startswith('-----')))
# basicConstraints: OID, optional critical BOOLEAN, OCTET STRING { SEQUENCE { cA BOOLEAN ... } }
i, ca = der.find(bytes.fromhex('0603551d13')), False
if i >= 0:
    j = i + 5
    if der[j:j + 3] == bytes.fromhex('0101ff'):
        j += 3
    inner = der[j + 2:j + 2 + der[j + 1]] if der[j] == 0x04 else b''
    ca = inner[2:5] == bytes.fromhex('0101ff')
print('C,,' if ca else 'P,,')
PY
)
	certutil -A -d "$NSS" -n "wallpanel-$(basename "$c" .crt)" -t "$t" -i "$c"
done

# Extension with the configured home URL (navigation lock + paint containment)
cp /usr/lib/wallpanel/extension/* "$EXT/"
printf 'const WALLPANEL_HOME = %s;\n' "\"$URL\"" > "$EXT/config.js"
# Chromium caches the extension service worker's importScripts (config.js) in the profile and keeps
# using it across restarts and version changes. When the extension or home URL changed, drop the
# service worker storage (Home Assistant registers its own worker again on the next load).
v=$(cat "$EXT"/* | cksum | cut -d' ' -f1)
if [ "$(cat "$DATA/.extension-id" 2>/dev/null)" != "$v" ]; then
	rm -rf "$DATA/chromium/Default/Service Worker"
	echo "$v" > "$DATA/.extension-id"
fi

# Apply output rotation once the compositor is up
(
	for _ in $(seq 1 50); do
		sock=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -E '^wayland-[0-9]+$' | head -1)
		[ -n "$sock" ] && WAYLAND_DISPLAY=$sock wlr-randr --output "$OUTPUT" --transform "$ROTATION" && break
		sleep 0.2
	done
) &

# --force-prefers-reduced-motion: HA's energy-distribution card then draws static flow lines instead of
# endlessly animated SMIL dots, which forced a full main-thread frame at display rate (~70 % CPU).
exec cage -d -s -- chromium \
	--kiosk --no-first-run --noerrdialogs --disable-infobars --log-level=3 \
	--ozone-platform=wayland --enable-gpu-rasterization --ignore-gpu-blocklist --enable-zero-copy \
	--disable-pinch --overscroll-history-navigation=0 --force-prefers-reduced-motion \
	--disable-features=Translate,TouchpadOverscrollHistoryNavigation,MediaRouter,DisableLoadExtensionCommandLineSwitch \
	--check-for-update-interval=31536000 --disable-component-update \
	--password-store=basic --autoplay-policy=no-user-gesture-required \
	--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 \
	--user-data-dir="$DATA/chromium" --load-extension="$EXT" \
	${CHROMIUM_FLAGS:-} "$URL"
