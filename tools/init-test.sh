#!/bin/sh
# PID 1 of the RAM-only test system. Never mounts or writes the eMMC.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/pts /sys/kernel/config /tmp /run
mount -t devpts devpts /dev/pts
mount -t configfs configfs /sys/kernel/config
mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null

log() { echo "[test-init] $*" | tee /dev/kmsg; }

# RAM-test only: warm reboot back to Android after 120 s so the kernel log can
# be collected from ramoops. Started first so nothing below can block it.
(sleep 120; log "auto warm reboot"; sync; reboot -f) &

log "iiyama TW2424AS mainline test boot: $(uname -r)"

for m in rockchipdrm panel-iiyama-tw2424as pwm_bl panfrost \
	 dwmac-rk brcmfmac hci_uart snd-soc-es8316 snd-soc-simple-card \
	 snd-soc-rockchip-i2s rockchip_saradc adc-keys rtc-hym8563 libcomposite usb_f_ncm usb_f_acm; do
	modprobe "$m" 2>/dev/null || log "modprobe $m failed"
done

# USB gadget on the Type-C port: CDC-NCM network + CDC-ACM serial
G=/sys/kernel/config/usb_gadget/g1
if [ -d /sys/kernel/config/usb_gadget ]; then
	mkdir -p $G && cd $G
	echo 0x1d6b > idVendor
	echo 0x0104 > idProduct
	mkdir -p strings/0x409
	echo "iiyama TW2424AS test" > strings/0x409/product
	echo "tw2424as-mainline" > strings/0x409/serialnumber
	mkdir -p configs/c.1 functions/ncm.usb0 functions/acm.usb0
	ln -s functions/ncm.usb0 configs/c.1/
	ln -s functions/acm.usb0 configs/c.1/
	UDC=$(ls /sys/class/udc | head -1)
	echo "$UDC" > UDC && log "USB gadget bound to $UDC"
	cd /
fi

sleep 2
ip link set lo up
if ip link set usb0 up 2>/dev/null; then
	# No DHCP server in busybox here: IPv6 link-local works without host setup
	ip addr add 10.42.0.1/24 dev usb0
	ip -6 addr add fe80::1/64 dev usb0
	# Unauthenticated root shell; only reachable over the USB cable
	mkfifo /tmp/shell.fifo
	(while true; do sh -i < /tmp/shell.fifo 2>&1 | nc -l -p 2323 > /tmp/shell.fifo; done) &
	# SSH (key only, root) on tcp/22; host key generated per boot
	dropbearkey -t ed25519 -f /etc/dropbear/dropbear_ed25519_host_key >/dev/null 2>&1
	dropbear -R -s -E -p 22 2>/dev/null && log "dropbear listening on tcp/22"
	log "usb0 up: fe80::1 / 10.42.0.1, ssh tcp/22, shell tcp/2323, report tcp/2324"
fi

# Summary on the internal display (fbcon on tty1)
{
	echo
	echo "=== iiyama TW2424AS - mainline Linux $(uname -r) ==="
	echo "RAM-only test boot. eMMC untouched. 'reboot' returns to Android."
	echo
	cat /sys/class/drm/card*-*/status 2>/dev/null | paste -sd' '
	ip -br addr
} > /dev/tty1 2>&1

# Collect a report for the host (served via nc on 2324)
{
	uname -a; echo; cat /proc/cpuinfo | grep -E "CPU part|Hardware" | sort | uniq -c
	echo; lsmod; echo; ls -l /sys/class/drm/; echo
	for c in /sys/class/drm/card*-*; do echo "$c $(cat $c/status) $(cat $c/enabled)"; done
	echo; cat /proc/bus/input/devices | grep -E "^N:"
	echo; ip -br link; echo; cat /proc/asound/cards
	echo; dmesg
} > /tmp/report.txt 2>&1
(while true; do nc -l -p 2324 < /tmp/report.txt; done) &

setsid sh -c 'exec getty -n -l /bin/sh 0 ttyGS0' 2>/dev/null &

# Diagnostics into the kernel log, which survives a warm reboot in ramoops and
# can be read from /sys/fs/pstore under Android afterwards.
kdiag() {
	log "=== diag: $1"
	sh -c "$2" 2>&1 | while IFS= read -r l; do echo "[diag] $l" > /dev/kmsg; done
}
kdiag drm 'for c in /sys/class/drm/card*-*; do echo "$c $(cat $c/status) $(cat $c/enabled) $(head -1 $c/modes 2>/dev/null)"; done'
kdiag backlight 'for b in /sys/class/backlight/*; do echo "$b brightness=$(cat $b/brightness) power=$(cat $b/bl_power) max=$(cat $b/max_brightness)"; done'
kdiag gpio 'cat /sys/kernel/debug/gpio'
kdiag regulators 'grep -E "lcd|vcc_sys|vcc12|usb" /sys/kernel/debug/regulator/regulator_summary'
kdiag udc 'ls /sys/class/udc; for u in /sys/class/udc/*; do echo "$u state=$(cat $u/state 2>/dev/null)"; done'
kdiag vop 'cat /sys/kernel/debug/dri/0/summary 2>/dev/null | head -20'
kdiag deferred 'cat /sys/kernel/debug/devices_deferred'
kdiag input 'grep -E "^N:" /proc/bus/input/devices'
kdiag net 'ip -br link'

while true; do
	setsid sh -c 'exec sh </dev/tty1 >/dev/tty1 2>&1'
	sleep 1
done
