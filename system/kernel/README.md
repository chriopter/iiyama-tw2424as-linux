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
- The former Rockchip BSP kernel 6.1 remains only as a reference in `docs/analysis/bsp-6.1/`.
