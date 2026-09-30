# apps/wallpanel-api/

Connects the panel to Home Assistant – via **MQTT discovery**, without a custom HA integration.

All entities of the device "Wallpanel", grouped by topic as on the panel's settings page; the prefix (Screen, Audio,
HA, System, Apps) makes HA's alphabetical list group them (*config*/*diagnostic* = HA's entity category):

| Name | Meaning | Type | Values (default) |
|---|---|---|---|
| **Screen** | | | |
| **Screen light** | screen light: the screen as a bulb (on/off = standby) – works with Adaptive Lighting | `light` | brightness 0–100 %, colour temperature 2202–6500 K (6500 K = neutral; never warmer than the LED bulbs) |
| **Screen on/off** | screen on/off; off = standby (backlight off, animations paused, touch wakes) | `switch` | on/off |
| **Screen lock** | screen locked: off, HA on-commands and touch wake ignored (power key still works) | `switch` | on/off (off) |
| *config* **Screen off after** | screen off after … without input | `number` | 0–240 min, 0 = never (5) |
| *config* **Screen fade** | screen fade | `number` | 0–3000 ms (400) |
| *config* **Screen fade: touch** | screen fade when woken by touch | `number` | 0–3000 ms (100) |
| *config* **Screen kelvin** | tint calibration against real bulbs | `number` | 50–150 % (74) |
| *config* **Screen red**, **Screen blue** | red/blue balance of the panel's white against the bulbs (below 4000 K, fading out towards 6500 K) | `number` | 50–100 % (red 96, blue 100) |
| **Browser & audio** | | | |
| **Audio volume** | volume | `number` | 0–100 % (30 after a restart) |
| **Audio playing** | playback: sound is playing (AirPlay, browser) | `binary_sensor` | on/off |
| **HA page address** | page address (the page shown now) | `text` | http(s) URL |
| *config* **HA home page** | home page | `text` | http(s) URL (`KIOSK_URL`) |
| *config* **HA home page after** | load the home page after … dark | `number` | 0–1440 min, 0 = never (60) |
| *config* **HA zoom** | scaling: page zoom of Home Assistant (restarts the browser) | `select` | 75, 80, 90, 100, 110, 125, 150, 175, 200 % (100) |
| *config* **HA hide header** | hide HA's top bar; search and Assist move next to the badges (restarts the browser) | `switch` | on/off (off) |
| *config* **HA Assist auto-listen** | tapping Assist starts speech recognition at once (restarts the browser) | `switch` | on/off (on) |
| **HA reload page** | reload page | `button` | – |
| *config* **HA restart browser** | restart browser | `button` | – |
| **Maintenance page** | show the on-screen maintenance page (update page) | `switch` | on/off |
| **Maintenance page toggle** | maintenance page ↔ dashboard | `button` | – |
| **System** | | | |
| *config* **System reboot daily** | reboot daily | `switch` | on/off (off) |
| *config* **System reboot: time** | time of the daily reboot | `select` | 00:00–23:30 in 30-min steps (04:00) |
| *config* **Apps update daily** | update Chrome & AirPlay once a day | `switch` | on/off (off) |
| *config* **Apps update: time** | time of the daily update – keep it apart from the reboot time (a reboot due during an update waits for it) | `select` | 00:00–23:30 in 30-min steps (03:30) |
| *config* **Apps update now** | update apps now (Chrome & AirPlay only) | `button` | – |
| *config* **System reboot** | reboot | `button` | – |
| **Diagnose (diagnostic)** | | | |
| *diagnostic* **CPU temperature**, **CPU usage** | CPU temperature, CPU usage | `sensor` | °C, % |
| *diagnostic* **WiFi signal**, **System uptime** | WiFi signal, uptime | `sensor` | dBm, h |
| *diagnostic* **Arbeitsspeicher belegt/frei**, **Speicherplatz frei/belegt**, **CPU load** | memory used/free, disk free/used, load | `sensor` | %, MiB, GiB (*frei*/*belegt* partly disabled by default) |
| *diagnostic* **Apps updates available**, **Apps update: last**, **System reboot: next** | updates available, last update, next reboot | `sensor` | count, time |

Volume: ALSA control `MIXER_CONTROL` (default `DAC` of the ES8316), 100 % = 0 dB, 0.5 dB per % (1 % = -49.5 dB),
0 % = mute; the last value is re-applied on start (default 30 %).
Additionally: Vol± with a centred volume overlay, power key toggles standby; keeps the kiosk in real fullscreen,
logs it in to HA (`KIOSK_USER`/`KIOSK_PASSWORD`, with backoff), reloads the page while the screen is dark
if the JS heap exceeds `RELOAD_HEAP_MB` (350) or free RAM drops below `RELOAD_MEM_PCT` (15 %).
Colour temperature ("night shift"): the compositor's gamma ramp via `wallpanel-gamma` (as user `wallpanel`,
restarted when it or the compositor exits), applied by the VOP's hardware LUT – no rendering cost; 6500 K
= neutral (no gamma client). **Screen kelvin** calibrates the tint against real bulbs in the mired domain,
keeping 6500 K neutral: `mired_eff = 153.85 + (1e6/K − 153.85) × s`. The default 74 % (3000 K is sent as ~3500 K)
comes from matching the panel to a hallway LED bulb (below); HA keeps seeing the requested Kelvin.
**Screen red/blue** scale the panel's red and blue on top (LED bulbs are greener than the panel's white).
On the settings page, **Screen colour → Adjust** turns the whole screen white with the four colour controls at
the bottom (Colour temperature, Screen kelvin, Screen red/blue); the colour temperature follows Home
Assistant live (shown as requested → panel Kelvin), so a bulb and the panel can be switched together from HA
while calibrating. Calibration changes and colour temperatures from the page apply at once; HA commands glide
over their `transition` (Adaptive Lighting without one: 1.5 s).
Calibrated once with a camera (fixed white balance/exposure) looking at the panel (white page) next to the
hallway bulb, bulb and panel at the same Kelvin: 85 % / red 92 % / blue 96 % brought the mean deviation of R/G and
B/G from 18 % to 12 %. Fine-tuned by eye afterwards on the white area, bulb and panel switched together from HA:
**74 % / red 96 % / blue 100 %** (the defaults). Needs our cage build ([`system/cage/`](../../system/cage/)): Alpine's cage 0.3.0 only
advertises the protocol and drops the ramps.
Opens no network port (MQTT client only); the update page listens on **127.0.0.1:8099** only. Diagnostics on the device: `python3 /usr/lib/wallpanel/wallpanel_api.py --state`.

**Updates.** "Apps" means the browser (`chromium`) and the AirPlay receiver (`shairport-sync`, if installed):
**Apps update daily** and the **Apps update now** button only run `apk add -u` for these two (plus the
dependencies apk needs), then restart the kiosk/AirPlay service. All other Alpine packages and the **kernel are
never updated automatically** – only by hand on the on-screen update page. *Apps updates available* counts all
pending packages.

**On-screen update page** – opened by the switch *Maintenance page*, the button *Maintenance page toggle* (page ↔ dashboard) or by **tapping the lit screen 10 times
within 4 s** (hidden gesture; the taps also reach the dashboard below). Two columns: *Apps & System* (pending
packages old → new from `apk upgrade --simulate`, Apps update daily status and time, last result,
**Update now** = full `apk upgrade` with live output) and *Kernel* (running kernel + slot, available
release with changelog, last result from `wallpanel-update check --json`, **Install kernel** with an on-screen
confirmation, then `wallpanel-update install-release` detached, output in `/var/log/wallpanel-kernel-update.log`).
**Close** (top right) or 10 min without touch returns to the previous page and turns the switch off.
A kernel install can only be started by touch on this page: no MQTT topic or HA entity triggers it, the POST
needs a per-start random token that only the served page contains (and our `Host` header), and the api
additionally requires a real finger-down from the touchscreen within the last 20 s.
The api looks for a new kernel release on GitHub once a day (and whenever the page opens or *Check again* is
tapped), so the page already shows it; installing still needs that touch.
Second tab **Settings & service**, grouped by topic; the three sections marked *also in Home Assistant*
mirror the HA entities with the same names – **Screen** (Brightness, Colour temperature, Screen colour, Screen fade
(: touch), Screen off after, Screen lock), **Browser & audio** (Audio volume, HA zoom, HA hide header, HA Assist auto-listen,
HA home page after; HA reload page, HA restart browser) and **System** (System reboot daily / : time, Apps update daily /
: time; Apps update now, System reboot) – and a dashed box **Device only** (Shut down). URLs are set from HA only. **HA zoom** shows the resulting resolution
(e.g. 125 % = 1536×864); *Adjust* opens a live preview of the dashboard in a frame – *Apply* restarts the
browser, the update page itself stays at 100 %.
They run through the same `command()` as the MQTT messages (HA state follows at once); settings need the page
token, anything that restarts or reboots (also *HA zoom*, *HA hide header*, *HA Assist auto-listen* and *Update now*) also the recent real touch.
A row above the columns shows current values with small graphs (1 h / 24 h): CPU, SoC temperature, memory,
WiFi signal, backlight (0 = off), plus uptime and kernel. The api samples them every 10 s into a 24 h ring
buffer in RAM (~0.3 MB, lost on restart, nothing written to the eMMC); the page polls every 5 s while open.

| File | Purpose |
|---|---|
| `rootfs/usr/lib/wallpanel/wallpanel_api.py` | the service (Python, paho-mqtt, evdev) |
| `rootfs/etc/init.d/wallpanel-api` | OpenRC service |
| `wallpanel.conf.example` | template for `/etc/wallpanel/wallpanel.conf` (shared with the kiosk) |

Settings (lock, standby, reboot and update times, fade/auto-off times, start page, …) persist in `/var/lib/wallpanel/api-state.json`. Brightness, colour temperature and volume live in RAM only (they change every few minutes via Adaptive Lighting/AirPlay and would wear the eMMC); after a restart: volume 30 %, brightness 200, neutral colour – Adaptive Lighting sets its values again. AirPlay volume changes reach HA within ~2 s.
