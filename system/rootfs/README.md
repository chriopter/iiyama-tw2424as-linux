# system/rootfs/

The root filesystem (Alpine Linux, OpenRC) on `userdata` (ext4, label `wallpanel-root`).

| Path | Content |
|---|---|
| `packages` | Alpine packages that `tools/install.sh` installs |
| `overlay/` | copied 1:1 to `/`: hostname, WiFi, Dropbear, USB maintenance access (`wallpanel-usb`), `wallpanel-update` |
| `overlay/etc/asound.conf` | ALSA `default`: playback via dmix on hw:0,0 (shared, e.g. Chromium + AirPlay), capture = the two digital mics (hw ch2/ch3 via dsnoop, any format through plug) – what Chromium/Assist and `arecord` get |
| `overlay/etc/conf.d/syslog`, `overlay/etc/periodic/` | flash wear: `/var/log` and `/tmp` are tmpfs (fstab from `write_fstab` in `tools/lib.sh`); syslog capped at 3 MiB, service logs trimmed above 2 MiB (every 15 min), `fstrim` weekly (crond) |
| `overlay/etc/conf.d/fsck` | boot never stops at a file system check (`fsck -y`, no abort) – the panel stays reachable after a power loss |
| `overlay/etc/local.d/audio.start` | mixer at boot: DAC → headphone outs/speaker, mic gain `Mic Array DMIC12` 24 dB |
| `overlay/usr/lib/wallpanel/boot/init` | init in the boot image: mounts `wallpanel-root` or starts **rescue mode** (USB network + SSH) |
| `overlay/usr/lib/wallpanel/boot/mkboot.py`, `mkrkboot.py` | boot image assembly (rescue initramfs + Android boot image v2), the same code on the PC (`tools/build-bootimg.sh`) and on the device (`wallpanel-update fetch`) |
| `overlay/usr/sbin/wallpanel-update`, `overlay/usr/lib/wallpanel/kernel-update.py` | A/B kernel updates, signed kernel releases (below) |
| `overlay/etc/init.d/wallpanel-kernel-health` | health check of a slot-B test boot: promote or fall back |
| `overlay/etc/wallpanel/kernel-release.pub` | public key the kernel release manifests must be signed with |
| `src/rebootmode.c` | `wallpanel-rebootmode`: one-shot boot into slot B without writing `misc` (`./build.sh helpers`) |

Rules:
- Only files that are identical on **every** device. Device data (WiFi, HA URL, MQTT, password) is written by
  `tools/install.sh` from `tools/local.env`.
- Never restrict maintenance access: USB gadget (`10.42.0.1` + serial root console) and SSH with key **and**
  password always stay enabled. No `-s`/`-w` for Dropbear.
- Apply without reinstalling: `tools/sync-apps.sh`.

## Kernel updates (`wallpanel-update`)

Slot A = partition `boot` (every power-on), slot B = `recovery` (one-shot test boot). Manual path:
`wallpanel-update install <boot.img> <modules.tar.gz>`, `test`, `promote`, `status`.

Release path: the GitHub releases `kernel-<release>` of this repository (built and signed by
`.github/workflows/kernel.yml`, see [`../kernel/`](../kernel/)) contain `Image`, `rk3399-iiyama-tw2424as.dtb`,
`modules.tar.gz`, `manifest.json` (sha256 + size of every file, changelog) and `manifest.json.sig`
(`ssh-keygen -Y sign`, namespace `wallpanel-kernel-release`). **No boot image and no keys**: the device
checks the signature against `/etc/wallpanel/kernel-release.pub` and every file hash, then assembles its own
boot image (`mkboot.py`: init, busybox, dropbear, the display modules of the new kernel and its own
`/root/.ssh/authorized_keys`; same cmdline and 45 MiB limit as `tools/build-bootimg.sh` – with the same keys and
package versions the result is byte-identical).

| Command | Does |
|---|---|
| `check [--json]` | running slot/kernel, latest release, changelog, whether it is newer, last result |
| `fetch` | download + verify + assemble into `/var/lib/wallpanel/kernel-update/` (idempotent, keeps verified files) |
| `install-release [--force]` | from slot A: fetch → `install` to slot B → baseline health check → pending test → `test` (reboot into B). In B, `wallpanel-kernel-health` checks for up to 3 min; pass → `promote` + reboot into A (new kernel), fail → reboot into A (old kernel), hang → hardware watchdog → A |
| `health [--json]` | the checks of the running system (exit 1 if one fails) |

Source: GitHub API `repos/<repo>/releases` (newest non-draft release tagged `kernel-*`), repo =
`WALLPANEL_KERNEL_REPO` or `KERNEL_REPO` in `/etc/wallpanel/wallpanel.conf` (`tools/local.env`), default
`chriopter/iiyama-tw2424as-linux`; answers are cached 5 min in `/run`. Testing without GitHub:
`WALLPANEL_KERNEL_RELEASE_DIR=/path` (a directory with the five release files).
"Newer" = a different release whose version (7.2.8) is not lower than the running one; older ones only with `--force`.

Health checks (`health`): `kernel` (running release = the tested one), `emmc` (debugfs `err_stats` of the root
eMMC all 0), `network` (WiFi up + default route), `api` (wallpanel-api started + MQTT connection established),
`kiosk` (wallpanel-kiosk started, DevTools: page loaded and two animation frames rendered), `services` (all
boot/default services started, none crashed). In slot B `kernel` and `emmc` are always required, the others only
if they passed on the old kernel right before the test (baseline in `pending.json`); pass = all required
checks ok twice in a row, uptime ≥ 60 s. Log: `/var/log/wallpanel-kernel-health.log`.

### Contract for the api (update page)

Both run as root. `wallpanel-update check --json` prints one JSON object on stdout, exit 0 (also when offline):

```json
{
  "running_slot": "A",
  "running_kernel": "7.2.8-iiyama-8d2b8e7",
  "available": {"tag": "kernel-7.2.8-iiyama-1143360", "kernel": "7.2.8-iiyama-1143360",
                "published": "2026-09-30T06:48:11Z", "changelog": ["Linux 7.2.8 (kernel.org stable)", "..."]},
  "update_available": true,
  "last_result": {"time": "2026-09-30T08:56:46+02:00", "from": "7.2.8-iiyama-8d2b8e7", "to": "7.2.8-iiyama-1143360",
                  "tag": "kernel-7.2.8-iiyama-1143360", "ok": false, "reason": "...", "checks": {"kernel": {"ok": true, "detail": "..."}}},
  "pending": null,
  "busy": false,
  "source": "github:chriopter/iiyama-tw2424as-linux",
  "error": null
}
```

- `available`: `null` if there is no (valid) release; `last_result`: `null` before the first update
  (`/var/lib/wallpanel/kernel-update/last-result.json`; `checks` only from a slot-B health check).
- `pending`: `{tag, from, to, started}` while a test boot is under way, else `null`. `busy`: an
  install-release/fetch holds the lock. `error`: text if the release could not be read or verified (network,
  GitHub, signature), else `null`.

`wallpanel-update install-release` (start it in the background, it ends with a reboot) prints progress lines on stdout:

```
progress <n>/6 <stage>: <text>        stage: check | download | verify | assemble | install | test
result ok rebooting into slot B to test <kernel>
result error <reason>                 (exit 1; also written to last-result.json, except "another kernel update is running")
```

After the reboots `check --json` shows the outcome in `last_result` (`ok` true: the new kernel runs in slot A).
