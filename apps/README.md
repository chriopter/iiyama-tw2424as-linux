# apps/

The two wallpanel apps. Each app contains its **target tree** under `rootfs/`, which is copied unchanged to `/`
on the device (`tools/install.sh`, `tools/sync-apps.sh`).

- [`wallpanel-kiosk/`](wallpanel-kiosk/) – Chromium fullscreen with the Home Assistant page
- [`wallpanel-api/`](wallpanel-api/) – control from Home Assistant (MQTT discovery), keys, overlay

Shared configuration: `/etc/wallpanel/wallpanel.conf` (template `wallpanel-api/wallpanel.conf.example`,
generated from `tools/local.env`). Rules: no device data in the repo; apps run unprivileged where possible.
