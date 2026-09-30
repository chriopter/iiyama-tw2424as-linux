#!/bin/sh
# Read TC358775 registers over DSI generic read (host briefly in command mode).
D=/tmp/devmem; B=0xff960000
r(){ $D $((B+$1)); }
w(){ $D $((B+$1)) $2 >/dev/null; }
waitbit(){ i=0; while [ $i -lt 200 ]; do v=$(( $(r 0x74) )); [ $(( (v >> $1) & 1 )) -eq $2 ] && return 0; i=$((i+1)); done; return 1; }
tcrd(){ # $1 = register address (16 bit)
	lo=$(( $1 & 0xff )); hi=$(( ($1 >> 8) & 0xff ))
	waitbit 0 1 || echo "cmd fifo busy"
	w 0x6c $(( 0x37 | (4 << 8) ))                 # set max return packet size = 4
	waitbit 0 1
	w 0x6c $(( 0x24 | (lo << 8) | (hi << 16) ))  # generic read, 2 params
	waitbit 0 1
	if waitbit 4 0; then printf "reg 0x%04x = %s\n" $1 $(r 0x70); else printf "reg 0x%04x: no response (status %s, int0 %s, int1 %s)\n" $1 $(r 0x74) $(r 0xbc) $(r 0xc0); fi
}
w 0x34 1              # command mode
tcrd 0x0580           # IDREG
tcrd 0x049c           # LVCFG
tcrd 0x0450           # VPCTRL
tcrd 0x0464           # VFUEN
w 0x34 0              # back to video mode
