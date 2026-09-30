# apps/wallpanel-airplay/

AirPlay audio receiver: the panel shows up as an AirPlay speaker (name = `DEVICE_NAME`, default
"Wallpanel") for iPhone/iPad/Mac and for Home Assistant via Music Assistant.
[shairport-sync](https://github.com/mikebrady/shairport-sync) 5.0.4 from Alpine – **AirPlay 1 (RAOP) only**
(Alpine builds it without AirPlay 2/nqptp and without FFmpeg), announced over mDNS by avahi.

| File (target path) | Purpose |
|---|---|
| `etc/init.d/wallpanel-airplay` | OpenRC service: writes `/run/wallpanel-airplay/shairport-sync.conf` from `wallpanel.conf`, runs shairport-sync as user `airplay` (group `audio`, created on first start), restart on crash, health check on the RTSP port |
| `etc/alsa/conf.d/60-wallpanel-airplay.conf` | ALSA PCM `wallpanel_airplay`: 44.1 kHz → the shared 48 kHz dmix of `/etc/asound.conf` |
| `etc/avahi/avahi-daemon.conf` | mDNS only on `wlan0`/`eth0` (not on the USB maintenance link), no host info published |

Settings (`/etc/wallpanel/wallpanel.conf`, from `tools/local.env`): `DEVICE_NAME`, optional `AIRPLAY_PASSWORD`
(AirPlay 1 password, asked by the sender). Services: `dbus`, `avahi-daemon`, `wallpanel-airplay` (default runlevel).
Log: `/var/log/wallpanel-airplay.log`; debug run: stop the service, then
`su -s /bin/sh airplay -c "shairport-sync -c /run/wallpanel-airplay/shairport-sync.conf -vv"`.

## Audio path and volume

- Output goes through the dmix (never `hw:0,0` directly), so Chromium (Assist, dashboard sounds) keeps
  playing at the same time. shairport-sync without FFmpeg only outputs 44100/S16/2 and probes the device
  with resampling disabled, so it can not use `default` (the plug in front of the 48 kHz dmix refuses
  44.1 kHz then – playback aborts with "unknown format"); `wallpanel_airplay` is an explicit rate plugin.
- **One volume**: AirPlay drives the same ALSA control as the panel (`DAC` of the ES8316), on the same
  scale – AirPlay volume -30…0 is mapped linearly onto -50…0 dB (`volume_range_db = 50`, profile `flat`),
  exactly like the `wallpanel-api` volume (100 % = 0 dB, 0.5 dB per %). AirPlay 20 % = panel 20 %.
  The HA number "Lautstärke" reads the control back on its periodic state update (≤ 60 s).
- When a sender connects it sets its own volume (e.g. the iPhone's slider), and the value stays after
  the session. Vol± on the panel during a session changes the loudness, but the sender's slider does not follow.

## Network

| Port | Direction | Purpose |
|---|---|---|
| 5353/udp | both | mDNS (avahi): announcement `_raop._tcp` |
| 5000/tcp | sender → panel | RTSP (AirPlay 1 control) |
| 6001–6010/udp | sender → panel | audio, control, timing (fixed range in the init script) |
| any/udp | panel → sender | timing/control replies to the ports the sender announces in SETUP |

Anyone who can reach the panel can play audio unless `AIRPLAY_PASSWORD` is set (plain AirPlay 1, no
encryption of the control channel). Firewall: allow the ports above only from the HA/Music Assistant host
and your clients. Stop it completely: `rc-update del wallpanel-airplay default; rc-service wallpanel-airplay stop`.

**Different VLAN**: mDNS does not cross VLANs, and avahi also ignores *unicast* mDNS queries from other
subnets ("Received non-local unicast query") – so a sender in another VLAN can neither discover nor
"add by IP" the panel via mDNS. Options: an mDNS reflector/repeater between the VLANs on the router
(UniFi "Multicast DNS", OPNsense/pfSense `mdns-repeater`/avahi reflector), or an interface of the HA host in
the panel's VLAN.

## Home Assistant

**Music Assistant (recommended)** – its AirPlay provider sends ALAC (libraop), the format iOS/macOS use too
(tested here with an ALAC RAOP test sender; Music Assistant and iOS themselves not yet):
1. Make mDNS reach the HA host (same VLAN or reflector, see above) and allow the ports above.
2. Music Assistant → Settings → Providers → add **AirPlay**. The player "Wallpanel" appears; enable it.
3. The Music Assistant integration in HA exposes it as `media_player` (TTS, announcements, music).

**Apple TV integration (pyatv): not usable with this receiver.** pyatv sends uncompressed PCM (L16) with an
`a=fmtp` line in the SDP; shairport-sync 5.0.4 then takes the stream for ALAC and feeds PCM into its ALAC
decoder – garbage or a segfault (the service restarts after 5 s). pyatv 0.18.0 also can not parse
shairport-sync's empty `GET /info` reply. Both are upstream issues (see the root README, Findings).

iPhone/iPad/Mac: in the same network as the panel (or with a reflector) "Wallpanel" appears in the
AirPlay menu.
