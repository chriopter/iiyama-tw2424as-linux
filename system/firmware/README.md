# system/firmware/

AP6256 (WiFi BCM43456, BT BCM4345C5): firmware, NVRAM and BT patch from the vendor partition.

- `vendor/` is **gitignored** (proprietary blobs). Obtain: `tools/pull-vendor-firmware.sh`
  (via ADB from the Android device) or from the backup `dumps/super.img`.
- Expected files: `fw_bcm43456c5_ag.bin`, `nvram_ap6256.txt`, `BCM4345C5.hcd`.
