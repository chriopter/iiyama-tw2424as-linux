#!/bin/sh
# SPDX-License-Identifier: MIT
# Run on the RAM test system: enter the kiosk rootfs (unpacked to /mnt/k)
# and start WiFi + cage/Chromium. Args: SSID PSK URL
set -e
K=/mnt/k SSID=$1 PSK=$2 URL=$3
mkdir -p /dev/shm; mountpoint -q /dev/shm || mount -t tmpfs -o mode=1777,nosuid,nodev shm /dev/shm
for d in proc sys dev dev/pts dev/shm run; do mkdir -p $K/$d; mountpoint -q $K/$d || mount --bind /$d $K/$d; done
mkdir -p $K/lib/modules $K/lib/firmware
mountpoint -q $K/lib/modules || mount --bind /lib/modules $K/lib/modules
cp /etc/resolv.conf $K/etc/resolv.conf 2>/dev/null || true
cat > $K/root/start.sh <<EOS
#!/bin/sh
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
/bin/busybox --install -s 2>/dev/null
udevd --daemon 2>/dev/null; udevadm trigger; udevadm settle
# WiFi
ip link set wlan0 up
wpa_passphrase "$SSID" "$PSK" > /etc/wpa.conf
wpa_supplicant -B -i wlan0 -c /etc/wpa.conf >/dev/null
udhcpc -i wlan0 -q -t 10 -n -s /usr/share/udhcpc/default.script >/dev/null 2>&1 || udhcpc -i wlan0 -q -t 10 -n
# Seat + compositor + browser
seatd -g video >/tmp/seatd.log 2>&1 &
sleep 1
mkdir -p /run/xdg && chmod 700 /run/xdg
export XDG_RUNTIME_DIR=/run/xdg LIBSEAT_BACKEND=seatd
cage -d -s -- chromium --kiosk --no-sandbox --ozone-platform=wayland \
	--enable-gpu-rasterization --ignore-gpu-blocklist --enable-zero-copy \
	--disable-features=Translate --noerrdialogs --no-first-run \
	--remote-debugging-port=9222 --user-data-dir=/root/chromium "$URL" >/tmp/cage.log 2>&1 &
echo started
EOS
chmod +x $K/root/start.sh
chroot $K /root/start.sh
