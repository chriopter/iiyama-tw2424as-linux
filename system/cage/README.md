# system/cage/

The kiosk compositor [cage](https://github.com/cage-kiosk/cage) as our own Alpine package: release
tarball pinned, our changes as patches, APKBUILD derived from Alpine's. **No cage source in the repo.**

Why: Alpine's cage 0.3.0 creates the gamma control manager but never connects it to the scene, so
`wlsunset` (night shift, `apps/wallpanel-api`) finds the protocol and its ramps are silently dropped.

| File | Content |
|---|---|
| `VERSION` | the pin: release tarball URL + SHA256, Alpine aports commit the APKBUILD derives from |
| `APKBUILD` | Alpine's `community/cage` minus `cage-run`/`-doc`, `pkgrel=100`, XWayland and man pages off |
| `patches/0001-*` | apply gamma ramps: `wlr_scene_set_gamma_control_manager_v1()` |
| `patches/0002-*` | `xwayland` meson feature option (upstream always follows wlroots, and Alpine's wlroots has XWayland) |

Build, install, pin:
- `./build.sh cage [--install]` (`tools/build-cage.sh`): verifies the tarball SHA256 on the PC, builds natively
  with `abuild` on the device over `tools/tssh` (Alpine v3.24 aarch64; build deps in the virtual package
  `.cage-build`, removed afterwards with the build directory), signs with the local key `tools/apk-key/`
  (gitignored, created on first use), puts its public half into `/etc/apk/keys` and copies the apk to
  `build/out/cage-0.3.0-r100.apk`. Timestamps come from the newest patch date: the same sources give a
  byte-identical apk (checked: native and chroot build).
- `--install` / `tools/install.sh`: `apk add` of that file, then `/etc/apk/world` gets `cage=0.3.0-r100`.
  The exact-version pin keeps `apk upgrade` (also `-a`) from switching to Alpine's cage – checked with
  `apk upgrade -s`. `tools/install.sh` installs it after the package step; without a built apk it builds
  it inside the new system (`--root /mnt/t`).
- The package is in no repository: `apk fix cage` cannot re-fetch it – reinstall with `./build.sh cage --install`.

Rules:
- Changes only as a patch series: `./build.sh fetch cage` (build/src/cage, patches as commits) → edit/commit →
  `./build.sh export cage` (rewrites `patches/` and the `sha512sums` in `APKBUILD`). One patch = one
  upstreamable change.
- pkgrel stays ≥ 100 (wallpanel build); bump it for every change of patches or APKBUILD.
- New cage release or Alpine rebuild (e.g. new wlroots soname, new Alpine branch): adjust `VERSION` and
  `pkgver`/`makedepends` from the aports commit, rebase the patches, rebuild, reinstall – the world pin
  holds the old build until then. Drop patch 0001 once upstream applies gamma itself.
- Check on the device: `apk info -e -v cage` = `cage-0.3.0-r100`; night shift 3000 K from HA → `wlsunset` runs,
  no "XWayland" lines in `/var/log/wallpanel-kiosk.log`.
