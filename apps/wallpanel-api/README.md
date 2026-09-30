# apps/wallpanel-api/

Connects the panel to Home Assistant – via **MQTT discovery**, without a custom HA integration.

All entities of the device "Wallpanel", grouped by topic as on the panel's settings page – HA shows the German
names (*config*/*diagnostic* = HA's entity category); English meaning and values:

| HA name | English | Type | Values (default) |
|---|---|---|---|
| **Bildschirm** | | | |
| **Bildschirm-Beleuchtung** | screen light: the screen as a bulb (on/off = standby) – works with Adaptive Lighting | `light` | brightness 0–100 %, colour temperature 1000–6500 K (6500 K = neutral) |
| **Bildschirm an/aus** | screen on/off; off = standby (backlight off, animations paused, touch wakes) | `switch` | on/off |
| **Bildschirm gesperrt** | screen locked: off, HA on-commands and touch wake ignored (power key still works) | `switch` | on/off (off) |
| *config* **Bildschirm aus nach** | screen off after … without input | `number` | 0–240 min, 0 = never (5) |
| *config* **Bildschirm-Überblendung** | screen fade | `number` | 0–3000 ms (400) |
| *config* **Bildschirm-Überblendung bei Berührung** | screen fade when woken by touch | `number` | 0–3000 ms (100) |
| *config* **Farbton-Kalibrierung** | tint calibration against real bulbs | `number` | 50–150 % (74) |
| *config* **Weißabgleich Rot**, **Weißabgleich Blau** | red/blue balance of the panel's white against the bulbs (below 4000 K, fading out towards 6500 K) | `number` | 50–100 % (red 96, blue 100) |
| **Browser & Ton** | | | |
| **Lautstärke** | volume | `number` | 0–100 % (30 after a restart) |
| **Wiedergabe** | playback: sound is playing (AirPlay, browser) | `binary_sensor` | on/off |
| **Seitenadresse** | page address (the page shown now) | `text` | http(s) URL |
| *config* **Startseite** | home page | `text` | http(s) URL (`KIOSK_URL`) |
| *config* **Startseite laden nach** | load the home page after … dark | `number` | 0–1440 min, 0 = never (60) |
| *config* **Skalierung** | scaling: page zoom of Home Assistant (restarts the browser) | `select` | 75, 80, 90, 100, 110, 125, 150, 175, 200 % (100) |
| *config* **HA-Kopfleiste ausblenden** | hide HA's top bar; search and Assist move next to the badges (restarts the browser) | `switch` | on/off (off) |
| *config* **Assist sofort zuhören** | tapping Assist starts speech recognition at once (restarts the browser) | `switch` | on/off (on) |
| **Seite neu laden** | reload page | `button` | – |
| *config* **Browser neu starten** | restart browser | `button` | – |
| **Wartungsseite anzeigen** | show the on-screen maintenance page (update page) | `switch` | on/off |
| **Wartungsseite umschalten** | maintenance page ↔ dashboard | `button` | – |
| **System** | | | |
| *config* **Neustart täglich** | reboot daily | `switch` | on/off (off) |
| *config* **Neustart-Zeit** | time of the daily reboot | `select` | 00:00–23:30 in 30-min steps (04:00) |
| *config* **Update täglich** | update Chrome & AirPlay once a day | `switch` | on/off (off) |
| *config* **Update-Zeit** | time of the daily update – keep it apart from the reboot time (a reboot due during an update waits for it) | `select` | 00:00–23:30 in 30-min steps (03:30) |
| *config* **Apps aktualisieren** | update apps now (Chrome & AirPlay only) | `button` | – |
| *config* **Neu starten** | reboot | `button` | – |
| **Diagnose (diagnostic)** | | | |
| *diagnostic* **Prozessortemperatur**, **Prozessorauslastung** | CPU temperature, CPU usage | `sensor` | °C, % |
| *diagnostic* **WLAN-Signal**, **Betriebszeit** | WiFi signal, uptime | `sensor` | dBm, h |
| *diagnostic* **Arbeitsspeicher belegt/frei**, **Speicherplatz frei/belegt**, **Systemlast** | memory used/free, disk free/used, load | `sensor` | %, MiB, GiB (*frei*/*belegt* partly disabled by default) |
| *diagnostic* **Updates verfügbar**, **Letztes Update**, **Nächster Neustart** | updates available, last update, next reboot | `sensor` | count, time |

Volume: ALSA control `MIXER_CONTROL` (default `DAC` of the ES8316), 100 % = 0 dB, 0.5 dB per % (1 % = -49.5 dB),
0 % = mute; the last value is re-applied on start (default 30 %).
Additionally: Vol± with a centred volume overlay, power key toggles standby; keeps the kiosk in real fullscreen,
logs it in to HA (`KIOSK_USER`/`KIOSK_PASSWORD`, with backoff), reloads the page while the screen is dark
if the JS heap exceeds `RELOAD_HEAP_MB` (350) or free RAM drops below `RELOAD_MEM_PCT` (15 %).
Colour temperature ("night shift"): the compositor's gamma ramp via `wallpanel-gamma` (as user `wallpanel`,
restarted when it or the compositor exits), applied by the VOP's hardware LUT – no rendering cost; 6500 K
= neutral (no gamma client). **Farbton-Kalibrierung** calibrates the tint against real bulbs in the mired domain,
keeping 6500 K neutral: `mired_eff = 153.85 + (1e6/K − 153.85) × s`. The default 74 % (3000 K is sent as ~3500 K)
comes from matching the panel to a hallway LED bulb (below); HA keeps seeing the requested Kelvin.
**Weißabgleich Rot/Blau** scale the panel's red and blue on top (LED bulbs are greener than the panel's white).
On the settings page, **Farbabgleich → Weißfläche** turns the whole screen white with the four colour controls at
the bottom (Farbtemperatur, Farbton-Kalibrierung, Weißabgleich Rot/Blau); the Farbtemperatur follows Home
Assistant live (shown as requested → panel Kelvin), so a bulb and the panel can be switched together from HA
while calibrating. Calibration changes and colour temperatures from the page apply at once; HA commands glide
over their `transition` (Adaptive Lighting without one: 1.5 s).
Calibrated once with a camera (fixed white balance/exposure) looking at the panel (white page) next to the
hallway bulb, bulb and panel at the same Kelvin: 85 % / red 92 % / blue 96 % brought the mean deviation of R/G and
B/G from 18 % to 12 %. Fine-tuned by eye afterwards on the Weißfläche, bulb and panel switched together from HA:
**74 % / red 96 % / blue 100 %** (the defaults). Needs our cage build ([`system/cage/`](../../system/cage/)): Alpine's cage 0.3.0 only
advertises the protocol and drops the ramps.
Opens no network port (MQTT client only); the update page listens on **127.0.0.1:8099** only. Diagnostics on the device: `python3 /usr/lib/wallpanel/wallpanel_api.py --state`.

**Updates.** "Apps" means the browser (`chromium`) and the AirPlay receiver (`shairport-sync`, if installed):
**Update täglich** and the **Apps aktualisieren** button only run `apk add -u` for these two (plus the
dependencies apk needs), then restart the kiosk/AirPlay service. All other Alpine packages and the **kernel are
never updated automatically** – only by hand on the on-screen update page. *Updates verfügbar* counts all
pending packages.

**On-screen update page** – opened by the switch *Wartungsseite anzeigen*, the button *Wartungsseite umschalten* (page ↔ dashboard) or by **tapping the lit screen 10 times
within 4 s** (hidden gesture; the taps also reach the dashboard below). Two columns: *Apps & System* (pending
packages old → new from `apk upgrade --simulate`, Update täglich status and time, last result,
**Jetzt aktualisieren** = full `apk upgrade` with live output) and *Kernel* (running kernel + slot, available
release with changelog, last result from `wallpanel-update check --json`, **Kernel installieren** with an on-screen
confirmation, then `wallpanel-update install-release` detached, output in `/var/log/wallpanel-kernel-update.log`).
**Schließen** (top right) or 10 min without touch returns to the previous page and turns the switch off.
A kernel install can only be started by touch on this page: no MQTT topic or HA entity triggers it, the POST
needs a per-start random token that only the served page contains (and our `Host` header), and the api
additionally requires a real finger-down from the touchscreen within the last 20 s.
The api looks for a new kernel release on GitHub once a day (and whenever the page opens or *Erneut prüfen* is
tapped), so the page already shows it; installing still needs that touch.
Second tab **Einstellungen & Service**, grouped by topic; the three sections marked *auch in Home Assistant*
mirror the HA entities with the same names – **Bildschirm** (Helligkeit, Farbtemperatur, Farbabgleich, Überblendung (+ bei Berührung), Bildschirm aus nach, Bildschirm gesperrt), **Browser & Ton** (Lautstärke, Skalierung,
HA-Kopfleiste ausblenden, Assist sofort zuhören, Startseite laden nach; Seite neu laden, Browser neu starten) and **System** (Neustart
täglich, Neustart-Zeit, Update täglich, Update-Zeit; Apps aktualisieren, Neu starten) – and a dashed box **Nur am Gerät**
(Herunterfahren). URLs are set from HA only. **Skalierung** shows the resulting resolution
(e.g. 125 % = 1536×864); *Anpassen* opens a live preview of the dashboard in a frame – *Übernehmen* restarts the
browser, the update page itself stays at 100 %.
They run through the same `command()` as the MQTT messages (HA state follows at once); settings need the page
token, anything that restarts or reboots (also *Skalierung*, *HA-Kopfleiste ausblenden*, *Assist sofort zuhören* and *Jetzt aktualisieren*) also the recent real touch.
A row above the columns shows current values with small graphs (1 h / 24 h): CPU, SoC temperature, memory,
WiFi signal, backlight (0 = off), plus uptime and kernel. The api samples them every 10 s into a 24 h ring
buffer in RAM (~0.3 MB, lost on restart, nothing written to the eMMC); the page polls every 5 s while open.

| File | Purpose |
|---|---|
| `rootfs/usr/lib/wallpanel/wallpanel_api.py` | the service (Python, paho-mqtt, evdev) |
| `rootfs/etc/init.d/wallpanel-api` | OpenRC service |
| `wallpanel.conf.example` | template for `/etc/wallpanel/wallpanel.conf` (shared with the kiosk) |

Settings (lock, standby, reboot and update times, fade/auto-off times, start page, …) persist in `/var/lib/wallpanel/api-state.json`. Brightness, colour temperature and volume live in RAM only (they change every few minutes via Adaptive Lighting/AirPlay and would wear the eMMC); after a restart: volume 30 %, brightness 200, neutral colour – Adaptive Lighting sets its values again. AirPlay volume changes reach HA within ~2 s.
