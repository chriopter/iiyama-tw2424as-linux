# system/

Everything that makes up the operating system on the device – excluding the apps (`apps/`).

| Directory | Content | ends up on the device in |
|---|---|---|
| `kernel/` | pin to kernel.org stable + our patches + config | `boot`/`recovery` (boot image) |
| `cage/` | pin to the cage release + our patches + APKBUILD (kiosk compositor with gamma control) | `cage` apk in `/usr/bin` |
| `wallpanel-gamma/` | our own gamma helper (C source + meson.build + APKBUILD): colour temperature from stdin, flicker-free | `wallpanel-gamma` apk in `/usr/bin` |
| `boot/` | mainline U-Boot as maskrom **RAM** loader (install/rescue) | never on the eMMC |
| `rootfs/` | Alpine package list, overlay, boot initramfs, small helpers | `userdata` (ext4 `wallpanel-root`), initramfs in the boot image |
| `firmware/` | AP6256 WiFi/BT (blobs gitignored) | `/lib/firmware/brcm` |

Rule: third-party sources are only pinned (version/commit + checksum), our own changes to them only as a patch series.
Our own programs (`wallpanel-gamma/`) keep their source here and are versioned (no pin).
