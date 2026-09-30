# apps/wallpanel-api/

Connects the panel to Home Assistant – via **MQTT discovery**, without a custom HA integration.

| Entity (HA name) | Type |
|---|---|
| **Bildschirm-Beleuchtung**: the screen as a bulb – on/off = standby, brightness, colour temperature 1000–6500 K (night shift); works with Adaptive Lighting | `light` |
| **Bildschirm an/aus**: off = standby (backlight off, animations paused, instant wake, touch wakes) | `switch` |
| **Bildschirm gesperrt**: switches the display off; while on, on commands from HA and touch wake are ignored (power key still works) | `switch` |
| **Lautstärke**; config: **Bildschirm aus nach** (min without input, 0 = never), **Bildschirm-Überblendung** (ms), **Farbton-Abgleich** (50–150 %, default 83) | `number` |
| **Seitenadresse** (current page), config: **Startseite** | `text` |
| **Update-Seite anzeigen**: shows the on-screen update page (see below) and wakes the display; off = back to the page shown before | `switch` |
| **Seite neu laden**; config: **Browser neu starten**, **Neu starten** (reboot), **Apps aktualisieren** (Chrome & AirPlay only) | `button` |
| Config: **Startseite laden nach** (min dark, 0 = never), **Neustart täglich**, **Auto-Update Apps** (Chrome & AirPlay, before the reboot), **Wartungszeit** (30-min steps) | `number`, `switch`, `select` |
| Diagnostic: Prozessortemperatur/-auslastung, WLAN-Signal, Betriebszeit (h), Arbeitsspeicher/Speicherplatz belegt/frei, Systemlast, Nächster Neustart, Letztes Update, Updates verfügbar – *Systemlast, Arbeitsspeicher frei, Speicherplatz belegt* are disabled by default | `sensor` |

Volume: ALSA control `MIXER_CONTROL` (default `DAC` of the ES8316), 100 % = 0 dB, 0.5 dB per % (1 % = -49.5 dB),
0 % = mute; the last value is re-applied on start (default 30 %).
Additionally: Vol± with a centred volume overlay, power key toggles standby; keeps the kiosk in real fullscreen,
logs it in to HA (`KIOSK_USER`/`KIOSK_PASSWORD`, with backoff), reloads the page while the screen is dark
if the JS heap exceeds `RELOAD_HEAP_MB` (350) or free RAM drops below `RELOAD_MEM_PCT` (15 %).
Colour temperature ("night shift"): the compositor's gamma ramp via `wallpanel-gamma` (as user `wallpanel`,
restarted when it or the compositor exits), applied by the VOP's hardware LUT – no rendering cost; 6500 K
= neutral (no gamma client). **Farbton-Abgleich** calibrates the tint against real bulbs in the mired domain,
keeping 6500 K neutral: `mired_eff = 153.85 + (1e6/K − 153.85) × s`. The default 83 % comes from "3000 K on
the panel looks like a 2700 K bulb" (3000 K is sent as ~3300 K); HA keeps seeing the requested Kelvin. Needs our cage build ([`system/cage/`](../../system/cage/)): Alpine's cage 0.3.0 only
advertises the protocol and drops the ramps.
Opens no network port (MQTT client only); the update page listens on **127.0.0.1:8099** only. Diagnostics on the device: `python3 /usr/lib/wallpanel/wallpanel_api.py --state`.

**Updates.** "Apps" means the browser (`chromium`) and the AirPlay receiver (`shairport-sync`, if installed):
**Auto-Update Apps** and the **Apps aktualisieren** button only run `apk add -u` for these two (plus the
dependencies apk needs), then restart the kiosk/AirPlay service. All other Alpine packages and the **kernel are
never updated automatically** – only by hand on the on-screen update page. *Updates verfügbar* counts all
pending packages.

**On-screen update page** – opened by the switch *Update-Seite anzeigen* or by **tapping the lit screen 10 times
within 4 s** (hidden gesture; the taps also reach the dashboard below). Two columns: *Apps & System* (pending
packages old → new from `apk upgrade --simulate`, Auto-Update Apps status and maintenance time, last result,
**Jetzt aktualisieren** = full `apk upgrade` with live output) and *Kernel* (running kernel + slot, available
release with changelog, last result from `wallpanel-update check --json`, **Kernel installieren** with an on-screen
confirmation, then `wallpanel-update install-release` detached, output in `/var/log/wallpanel-kernel-update.log`).
**Schließen** (top right) or 10 min without touch returns to the previous page and turns the switch off.
A kernel install can only be started by touch on this page: no MQTT topic or HA entity triggers it, the POST
needs a per-start random token that only the served page contains (and our `Host` header), and the api
additionally requires a real finger-down from the touchscreen within the last 20 s.
A row above the columns shows current values with small graphs (1 h / 24 h): CPU, SoC temperature, memory,
WiFi signal, backlight (0 = off), plus uptime and kernel. The api samples them every 10 s into a 24 h ring
buffer in RAM (~0.3 MB, lost on restart, nothing written to the eMMC); the page polls every 5 s while open.

| File | Purpose |
|---|---|
| `rootfs/usr/lib/wallpanel/wallpanel_api.py` | the service (Python, paho-mqtt, evdev) |
| `rootfs/etc/init.d/wallpanel-api` | OpenRC service |
| `wallpanel.conf.example` | template for `/etc/wallpanel/wallpanel.conf` (shared with the kiosk) |

State (brightness, colour temperature, volume, reboot time) persists across reboots in `/var/lib/wallpanel/api-state.json`.
