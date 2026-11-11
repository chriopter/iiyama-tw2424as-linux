# apps/wallpanel-kiosk/

Chromium fullscreen under `cage` (Wayland; our build from [`system/cage/`](../../system/cage/): gamma control,
no XWayland) with the Home Assistant page (`URL` in `wallpanel.conf`).

| File (target path) | Purpose |
|---|---|
| `usr/lib/wallpanel/wallpanel-kiosk.sh` | starts cage + Chromium (GPU raster, 180° rotation, DevTools on 127.0.0.1:9222 only); everything Chromium writes in RAM (own tmpfs `/run/wallpanel`: profile, caches, 64 MB HTTP cache) |
| `usr/lib/wallpanel/kiosk-profile.sh` | the profile's lasting part (HA login in Local Storage, permissions such as the microphone, preferences, cookies) goes to `/var/lib/wallpanel/chromium` when the kiosk stops – only if changed – and back into RAM at its first start after boot |
| `usr/lib/wallpanel/extension/` | navigation locked to the HA address (plus the api's update page on 127.0.0.1:8099); `contain` per `ha-card` (≈2× fps); tweak *HA hide header* (`settings.js`, written at start: HA's top bar hidden, search + Assist top right next to the badges; not in edit mode or on dashboards with view tabs) and *HA Assist auto-listen* (tapping Assist starts listening at once); drops HA's `X-Frame-Options` so the update page's scaling preview can frame the dashboard (third-party storage partitioning is off for the same reason: the frame needs the kiosk's HA login) |
| `etc/init.d/wallpanel-kiosk` | OpenRC service as user `wallpanel`, restart on crash |
| `etc/udev/rules.d/90-wallpanel-touch.rules` | touch calibration for the 180° mounting |

Settings (`/etc/wallpanel/wallpanel.conf`): `URL`, `ROTATION`, `OUTPUT`, `CHROMIUM_FLAGS`. Scaling (*HA zoom*, api): Chromium's
page zoom of the HA host, written into the profile's `Preferences` before each start – `--force-device-scale-factor`
does not change the layout under Wayland (it only renders a larger buffer and scales it down).
Log in to HA once via touch or `tools/debug/kiosk/cdp.py login`.

Text fields open the built-in German touch keyboard (`extension/keyboard.js`), including fields inside
Home Assistant's shadow DOM. It supports shift, umlauts, symbols, a numeric layout, backspace and Enter;
tap outside or **Schließen** to dismiss it. Passwords are edited in their existing masked field and are
never stored by the keyboard. This works within the kiosk pages without a separate Wayland input-method
service (cage does not implement the necessary text-input protocols).
The keyboard uses a manual HTML popover in the browser's top layer and stays inside the focused
field's dialog/dropdown, so Home Assistant pickers neither cover it nor close when a key is tapped.
Tapping an already focused field opens it again. Browser regression checks (including nested modal
dialogs and dropdowns): open `tests/keyboard.html`; the result should start with `PASS`.
