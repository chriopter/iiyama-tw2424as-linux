# tools/debug/

Tools from the porting work – not needed for operation.

| Tool | Purpose |
|---|---|
| `devmem/` | standalone `devmem` (`./build.sh helpers`) + TC358775 test scripts |
| `kiosk/cdp.py`, `kiosk/perf.py` | Chromium via DevTools: HA login, fps/paint measurement (`ssh -L 9222`) |
| `kiosk/kiosk-chroot.sh` | kiosk test in the RAM system |
| `dtshow.py`, `dtdiff.py` | show vendor DT with resolved GPIOs / compare DTBs semantically |
| `usbsh.py`, `usbput.py` | shell/files over the USB gadget without SSH |
| `fbtest/` | test images for rotation/orientation |
| `shelly.sh status\|cycle` | power cycle via a Shelly plug (≥10 s off, ≥60 s on) |
| `snap.sh`, `screenlum.py` | camera image of the display / brightness of the display area (`CAM_DIR`) |
