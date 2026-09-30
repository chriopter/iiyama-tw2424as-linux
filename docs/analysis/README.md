# docs/analysis/

Derived, sanitized analysis results (decoded init sequence, register comparisons, DT excerpts).
Raw data from the device (getprop, live DT, dmesg …) contains serial number/MACs and is kept only locally in
`raw/` (gitignored).

`bsp-6.1/` – Rockchip BSP kernel `develop-6.1` (pin `BASE`, patches, config) that the board first
ran on. Reference only now (e.g. for HDMI-in/RK628); no longer built.
