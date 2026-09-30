# tools/

Scripts for building, installing, updating and rescuing. Device-specific data lives **only** in `local.env`
(gitignored; template `local.env.example`), the maintenance key in `ssh/`, the apk signing key in `apk-key/`
(both gitignored, generated on first run) and the kernel release signing key in `kernel-key/` (gitignored;
in CI the secret `KERNEL_SIGNING_KEY`).

| Script | Purpose |
|---|---|
| `build-bootimg.sh` | boot images for `boot`/`recovery` (kernel, DTB, rescue initramfs) from `./build.sh release`, assembled by `system/rootfs/overlay/usr/lib/wallpanel/boot/mkboot.py` (same code as on the device) |
| `kernel-manifest.py` | `manifest.json` of a kernel release (`./build.sh release`) |
| `kernel-bump.sh [--dry-run]` | upstream watch: newer kernel.org stable → pull request (`.github/workflows/kernel-bump.yml`) |
| `build-cage.sh [--root DIR] [--install]` | our cage apk (`system/cage/`), built with abuild on the device → `build/out/`; `./build.sh cage` |
| `build-wallpanel-gamma.sh [--root DIR] [--install]` | our gamma helper apk (`system/wallpanel-gamma/`), same flow; `./build.sh wallpanel-gamma` |
| `build-test-image.sh` | RAM installer: kernel + Alpine initramfs with SSH/USB gadget |
| `ramboot.sh [img]` | load an image into RAM via maskrom and start it (no eMMC access) |
| `install.sh` | installation to the eMMC from the RAM installer (README → "Install") |
| `sync-apps.sh` | apps + overlay + password into the installed system (running or rescue mode) |
| `restore-android.sh ssh\|rockusb` | back to Android from the backup |
| `backup.sh`, `dump_chunked.sh` | eMMC backup via ADB with SHA256 verification |
| `pull-vendor-firmware.sh` | fetch AP6256 firmware from the Android system |
| `tssh` | SSH to the device via USB-C (`10.42.0.1`) |
| `init-test.sh` | init of the RAM installer |
| `lib.sh` | shared functions (keys, applying files, root password, installing our apks: cage, wallpanel-gamma) |
| `70-rockchip.rules` | udev rule for Rockusb/maskrom access on the PC |
| `debug/` | development and measurement tools; `debug/ct-sync.py`: bulb and panel step through 10 colour temperatures in sync (`--white`: plain white panel) to check the colour calibration by eye |
