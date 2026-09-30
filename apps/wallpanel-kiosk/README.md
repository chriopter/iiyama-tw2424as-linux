# apps/wallpanel-kiosk/

Chromium fullscreen under `cage` (Wayland; our build from [`system/cage/`](../../system/cage/): gamma control,
no XWayland) with the Home Assistant page (`URL` in `wallpanel.conf`).

| File (target path) | Purpose |
|---|---|
| `usr/lib/wallpanel/wallpanel-kiosk.sh` | starts cage + Chromium (GPU raster, 180° rotation, DevTools on 127.0.0.1:9222 only); everything Chromium writes in RAM (own tmpfs `/run/wallpanel`: profile, caches, 64 MB HTTP cache) |
| `usr/lib/wallpanel/kiosk-profile.sh` | the profile's lasting part (HA login in Local Storage, permissions such as the microphone, preferences, cookies) goes to `/var/lib/wallpanel/chromium` when the kiosk stops – only if changed – and back into RAM at its first start after boot |
| `usr/lib/wallpanel/extension/` | navigation locked to the HA address (plus the api's update page on 127.0.0.1:8099); `contain` per `ha-card` (≈2× fps) |
| `etc/init.d/wallpanel-kiosk` | OpenRC service as user `wallpanel`, restart on crash |
| `etc/udev/rules.d/90-wallpanel-touch.rules` | touch calibration for the 180° mounting |

Settings (`/etc/wallpanel/wallpanel.conf`): `URL`, `ROTATION`, `OUTPUT`, `CHROMIUM_FLAGS`.
Log in to HA once via touch or `tools/debug/kiosk/cdp.py login`.
