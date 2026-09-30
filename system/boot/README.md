# system/boot/

Mainline U-Boot as a **pure RAM loader** for maskrom mode (see README → "Boot chain").
Never written to the eMMC; no flash, UMS or MMC write functions are compiled in.

- `BASE` – U-Boot commit; `RKBIN` – Rockchip DDR init/BL31; `RKUSBBOOT` – upload tool.
- `patches/` – SPL boot order fix + `iiyama-tw2424as-ramboot_defconfig`.
- Build: `./build.sh fetch uboot && ./build.sh uboot` → `build/out/u-boot/u-boot-rockchip-usb47{1,2}.bin`
