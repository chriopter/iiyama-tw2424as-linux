# system/rootfs/

The root filesystem (Alpine Linux, OpenRC) on `userdata` (ext4, label `wallpanel-root`).

| Path | Content |
|---|---|
| `packages` | Alpine packages that `tools/install.sh` installs |
| `overlay/` | copied 1:1 to `/`: hostname, WiFi, Dropbear, USB maintenance access (`wallpanel-usb`), `wallpanel-update` |
| `overlay/etc/asound.conf` | ALSA `default`: playback via dmix on hw:0,0 (shared, e.g. Chromium + AirPlay), capture = the two digital mics (hw ch2/ch3 via dsnoop, any format through plug) – what Chromium/Assist and `arecord` get |
| `overlay/etc/local.d/audio.start` | mixer at boot: DAC → headphone outs/speaker, mic gain `Mic Array DMIC12` 24 dB |
| `initramfs/init` | init in the boot image: mounts `wallpanel-root` or starts **rescue mode** (USB network + SSH) |
| `src/rebootmode.c` | `wallpanel-rebootmode`: one-shot boot into slot B without writing `misc` (`./build.sh helpers`) |

Rules:
- Only files that are identical on **every** device. Device data (WiFi, HA URL, MQTT, password) is written by
  `tools/install.sh` from `tools/local.env`.
- Never restrict maintenance access: USB gadget (`10.42.0.1` + serial root console) and SSH with key **and**
  password always stay enabled. No `-s`/`-w` for Dropbear.
- Apply without reinstalling: `tools/sync-apps.sh`.
