# system/kernel/

kernel.org stable, pinned, plus our patches. **No kernel source in the repo.**

| File | Content |
|---|---|
| `VERSION` | the pin: version, tarball URL, SHA256 |
| `patches/` | our changes as a `git am` series (panel driver, DT bindings, board DTS) |
| `config.fragment` | kernel options for the board (deviations from `defconfig`) |
| `config.platforms` | trimming to RK3399 (all other `ARCH_*` off → kernel fits uncompressed in `boot`) |

Rules:
- Changes only as a patch series (`./build.sh export kernel`, never edit by hand).
- One patch = one logical change, upstreamable (`scripts/checkpatch.pl --strict` clean).
- Kernel update: adjust `VERSION` → `./build.sh fetch kernel` (verifies SHA256) → rebase patches →
  `./build.sh export kernel` → test via slot B (README → "Kernel update").

## Releases (CI)

`.github/workflows/kernel.yml` runs on every push to `main` that touches `system/kernel/**` (and manually):
`./build.sh fetch kernel`, `./build.sh kernel`, `./build.sh release`, `./build.sh sign-release` – the same
commands as on the PC – and publishes GitHub release **`kernel-<release>`** (e.g. `kernel-7.2.8-iiyama-1143360`)
with `Image`, `rk3399-iiyama-tw2424as.dtb`, `modules.tar.gz`, `manifest.json` and `manifest.json.sig`.
The release string comes from the source tree + config, so the same source never produces two releases
(an existing tag is skipped). Other branches only build (artifact, no release). The panel installs releases
with `wallpanel-update install-release` ([`../rootfs/`](../rootfs/) → "Kernel updates").

`manifest.json`: tag, kernel release, upstream version/URL/SHA256, git commit, build date, sha256 + size of every
file, changelog (upstream version + our patch subjects). It is signed with `ssh-keygen -Y sign`
(namespace `wallpanel-kernel-release`); the devices trust only
[`../rootfs/overlay/etc/wallpanel/kernel-release.pub`](../rootfs/overlay/etc/wallpanel/kernel-release.pub).
Releases contain no boot image and no keys: every panel assembles its boot image with its own rescue SSH keys.

Signing key: repository secret **`KERNEL_SIGNING_KEY`** = the complete OpenSSH private key file
(`-----BEGIN OPENSSH PRIVATE KEY-----` … `-----END OPENSSH PRIVATE KEY-----`, unencrypted ed25519). Locally it
lives in `tools/kernel-key/kernel-release` (gitignored); `sign-release` checks the signature against the committed
public key, so a wrong secret fails the build instead of publishing an unusable release. New key: `ssh-keygen -t
ed25519 -N '' -C wallpanel-kernel-release -f tools/kernel-key/kernel-release`, commit the `.pub` as
`kernel-release.pub`, update the secret, deploy the overlay (older panels then reject new releases until updated).

Local release for tests: `./build.sh kernel && ./build.sh release && ./build.sh sign-release` → `build/out/release/`;
copy it to the panel and use `WALLPANEL_KERNEL_RELEASE_DIR=<dir> wallpanel-update install-release`.

## Upstream watch

`.github/workflows/kernel-bump.yml` (daily, `tools/kernel-bump.sh`; locally `tools/kernel-bump.sh --dry-run`):
a newer kernel.org stable of the pinned series (7.2.y) → `VERSION` + SHA256 from kernel.org's signed
`sha256sums.asc` (autosigner key via WKD), `./build.sh fetch kernel` checks that all patches apply → branch
`kernel-bump/<version>` + pull request + a build of the branch. Patches do not apply → issue. A newer series
(7.3) or an end-of-life series → issue only. Nothing is released before the pull request is merged.
Needs *Settings → Actions → General → Allow GitHub Actions to create and approve pull requests*.
- The former Rockchip BSP kernel 6.1 remains only as a reference in `docs/analysis/bsp-6.1/`.
