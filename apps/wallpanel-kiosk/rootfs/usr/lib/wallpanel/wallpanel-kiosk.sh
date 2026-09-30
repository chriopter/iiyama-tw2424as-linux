#!/bin/sh
# wallpanel-kiosk: Chromium fullscreen with the Home Assistant dashboard (cage, Wayland).
# Runs as the unprivileged user "wallpanel"; started and respawned by OpenRC.
set -eu
. /etc/wallpanel/wallpanel.conf
# Start page (MQTT text "Home page") and scaling (update page "Skalierung", percent) saved by wallpanel-api:
# the start page wins over the config
st() { python3 -c "import json, sys; print(json.load(open('/var/lib/wallpanel/api-state.json')).get(sys.argv[1], ''))" "$1" 2>/dev/null || true; }
H=$(st home_url)
[ -n "$H" ] && URL=$H
S=$(st scale)
: "${URL:?URL not set in /etc/wallpanel/wallpanel.conf}"
ROTATION=${ROTATION:-180}
OUTPUT=${OUTPUT:-DSI-1}
# Everything Chromium writes lives in RAM (/run/wallpanel, own tmpfs): the root file system is read-only.
# The profile comes from the last save on the eMMC (kiosk-profile.sh: HA login, permissions, preferences).
RUN=/run/wallpanel
PROFILE=$RUN/chromium
EXT=$RUN/extension

export XDG_RUNTIME_DIR=$RUN LIBSEAT_BACKEND=seatd HOME=$RUN/home
mkdir -p "$HOME" "$EXT"
/usr/lib/wallpanel/kiosk-profile.sh restore

# Trusted server certificates (e.g. a self-signed Home Assistant certificate): every
# /etc/wallpanel/trust/*.crt goes into Chromium's NSS database, CA:FALSE certificates as trusted peer.
NSS=sql:$HOME/.pki/nssdb
mkdir -p "$HOME/.pki/nssdb"
[ -f "$HOME/.pki/nssdb/cert9.db" ] || certutil -N -d "$NSS" --empty-password
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
# tweaks for the content scripts (update page / MQTT "HA-Kopfleiste ausblenden")
[ "$(st hide_header)" = True ] && HIDE_HEADER=true || HIDE_HEADER=false
printf 'const WALLPANEL_TWEAKS = { hideHeader: %s };\n' "$HIDE_HEADER" > "$EXT/settings.js"
# Chromium caches the extension service worker's importScripts (config.js) in the profile and keeps
# using it across restarts. When the extension or home URL changed (kiosk restart after an update),
# drop the service worker storage (Home Assistant registers its own worker again on the next load).
v=$(cat "$EXT"/* | cksum | cut -d' ' -f1)
if [ "$(cat "$RUN/.extension-id" 2>/dev/null)" != "$v" ]; then
	rm -rf "$PROFILE/Default/Service Worker"
	echo "$v" > "$RUN/.extension-id"
fi

# Scaling ("Skalierung"): Chromium's page zoom of the Home Assistant host, written into the profile before
# the start - 125 % lays HA out for 1536 x 864; the update page (127.0.0.1) keeps 100 %.
# (--force-device-scale-factor does not change the layout under Wayland: only a larger buffer, scaled down.)
python3 - "$PROFILE/Default/Preferences" "$URL" "${S:-100}" <<'PY' || echo "scaling not applied" >&2
import json, math, os, sys, urllib.parse
path, url, scale = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    s = int(float(scale))
except ValueError:
    s = 100
s = s if 50 <= s <= 300 else 100
try:
    p = json.load(open(path))
except (OSError, ValueError):
    p = {}
levels = p.setdefault('partition', {}).setdefault('per_host_zoom_levels', {}).setdefault('x', {})
host = urllib.parse.urlsplit(url).hostname
if s == 100:
    levels.pop(host, None)
else:  # Chromium zoom level: factor = 1.2 ** level
    levels[host] = {'last_modified': '13400000000000000', 'zoom_level': math.log(s / 100) / math.log(1.2)}
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(p, open(path + '.new', 'w'))
os.replace(path + '.new', path)
PY

# Apply output rotation once the compositor is up
(
	for _ in $(seq 1 50); do
		sock=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -E '^wayland-[0-9]+$' | head -1)
		[ -n "$sock" ] && WAYLAND_DISPLAY=$sock wlr-randr --output "$OUTPUT" --transform "$ROTATION" && break
		sleep 0.2
	done
) &

# ThirdPartyStoragePartitioning off: the update page's scaling preview frames the dashboard, which then needs
# the kiosk's HA login (Local Storage) - partitioned, the frame would only show the login page.
# --force-prefers-reduced-motion: HA's energy-distribution card then draws static flow lines instead of
# endlessly animated SMIL dots, which forced a full main-thread frame at display rate (~70 % CPU).
exec cage -d -s -- chromium \
	--kiosk --start-fullscreen --no-first-run --noerrdialogs --disable-infobars --log-level=3 \
	--ozone-platform=wayland --enable-gpu-rasterization --ignore-gpu-blocklist --enable-zero-copy \
	--disable-pinch --overscroll-history-navigation=0 --force-prefers-reduced-motion \
	--disable-features=ThirdPartyStoragePartitioning,Translate,TouchpadOverscrollHistoryNavigation,MediaRouter,DisableLoadExtensionCommandLineSwitch \
	--check-for-update-interval=31536000 --disable-component-update \
	--password-store=basic --autoplay-policy=no-user-gesture-required \
	--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 \
	--user-data-dir="$PROFILE" --load-extension="$EXT" --disk-cache-size=67108864 \
	${CHROMIUM_FLAGS:-} "$URL"
