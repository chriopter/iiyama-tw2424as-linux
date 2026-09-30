# apps/wallpanel-api/

Connects the panel to Home Assistant – via **MQTT discovery**, without a custom HA integration.

| Entity (HA name) | Type |
|---|---|
| **Bildschirm-Beleuchtung**: the screen as a bulb – on/off = standby, brightness, colour temperature 1000–6500 K (night shift); works with Adaptive Lighting | `light` |
| **Bildschirm an/aus**: off = standby (backlight off, animations paused, instant wake, touch wakes) | `switch` |
| **Bildschirm gesperrt**: switches the display off; while on, on commands from HA and touch wake are ignored (power key still works) | `switch` |
| **Lautstärke**; config: **Bildschirm aus nach** (min without input, 0 = never), **Bildschirm-Überblendung** (ms) | `number` |
| **Seitenadresse** (current page), config: **Startseite** | `text` |
| **Seite neu laden**; config: **Browser neu starten**, **Neu starten** (reboot), **Updates installieren** | `button` |
| Config: **Startseite laden nach** (min dark, 0 = never), **Neustart täglich**, **Updates automatisch** (before the reboot), **Wartungszeit** (30-min steps) | `number`, `switch`, `select` |
| Diagnostic: Prozessortemperatur/-auslastung, WLAN-Signal, Betriebszeit (h), Arbeitsspeicher/Speicherplatz belegt/frei, Systemlast, Nächster Neustart, Letztes Update, Updates verfügbar – *Systemlast, Arbeitsspeicher frei, Speicherplatz belegt* are disabled by default | `sensor` |

Volume: ALSA control `MIXER_CONTROL` (default `DAC` of the ES8316), 100 % = 0 dB, 0.5 dB per % (1 % = -49.5 dB),
0 % = mute; the last value is re-applied on start (default 30 %).
Additionally: Vol± with a centred volume overlay, power key toggles standby; keeps the kiosk in real fullscreen,
logs it in to HA (`KIOSK_USER`/`KIOSK_PASSWORD`, with backoff), reloads the page while the screen is dark
if the JS heap exceeds `RELOAD_HEAP_MB` (350) or free RAM drops below `RELOAD_MEM_PCT` (15 %).
Colour temperature ("night shift"): the compositor's gamma ramp via `wlsunset` (as user `wallpanel`,
restarted when it or the compositor exits), applied by the VOP's hardware LUT – no rendering cost; 6500 K
= neutral (no gamma client). Needs our cage build ([`system/cage/`](../../system/cage/)): Alpine's cage 0.3.0 only
advertises the protocol and drops the ramps.
Opens **no port** (MQTT client only). Diagnostics on the device: `python3 /usr/lib/wallpanel/wallpanel_api.py --state`.

| File | Purpose |
|---|---|
| `rootfs/usr/lib/wallpanel/wallpanel_api.py` | the service (Python, paho-mqtt, evdev) |
| `rootfs/etc/init.d/wallpanel-api` | OpenRC service |
| `wallpanel.conf.example` | template for `/etc/wallpanel/wallpanel.conf` (shared with the kiosk) |

State (brightness, colour temperature, volume, reboot time) persists across reboots in `/var/lib/wallpanel/api-state.json`.
