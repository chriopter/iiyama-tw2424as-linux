# system/rootfs/

The root filesystem (Alpine Linux, OpenRC) on `userdata` (ext4, label `wallpanel-root`).

| Path | Content |
|---|---|
| `packages` | Alpine packages that `tools/install.sh` installs |
| `overlay/` | copied 1:1 to `/`: hostname, WiFi, Dropbear, USB maintenance access (`wallpanel-usb`), `wallpanel-update` |
| `initramfs/init` | init in the boot image: mounts `wallpanel-root` or starts **rescue mode** (USB network + SSH) |
| `src/rebootmode.c` | `wallpanel-rebootmode`: one-shot boot into slot B without writing `misc` (`./build.sh helpers`) |

Rules:
- Only files that are identical on **every** device. Device data (WiFi, HA URL, MQTT, password) is written by
  `tools/install.sh` from `tools/local.env`.
- Never restrict maintenance access: USB gadget (`10.42.0.1` + serial root console) and SSH with key **and**
  password always stay enabled. No `-s`/`-w` for Dropbear.
- Apply without reinstalling: `tools/sync-apps.sh`.
