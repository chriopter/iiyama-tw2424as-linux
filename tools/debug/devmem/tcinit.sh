#!/bin/sh
# SPDX-License-Identifier: MIT
# Re-send the TC358775 init sequence by hand (DSI generic long writes, LP).
D=/usr/sbin/devmem; B=0xff960000
r(){ $D $((B+$1)); }
w(){ $D $((B+$1)) $2 >/dev/null; }
waitidle(){ i=0; while [ $i -lt 500 ]; do v=$(( $(r 0x74) )); [ $(( v & 0x5 )) -eq 5 ] && return 0; i=$((i+1)); done; echo "timeout status $(r 0x74)"; }
tcw(){ # $1 reg, $2 value (32 bit)
	a=$1; v=$2
	w 0x70 $(( (a & 0xff) | ((a >> 8 & 0xff) << 8) | ((v & 0xff) << 16) | ((v >> 8 & 0xff) << 24) ))
	w 0x70 $(( (v >> 16) & 0xffff ))
	w 0x6c $(( 0x29 | (6 << 8) ))
	waitidle
	usleep 5000
}
G=0xff730000   # GPIO1, PA2 = bridge reset (high = running)
gw(){ v=$(( $($D $G) )); if [ $1 = 1 ]; then v=$(( v | 4 )); else v=$(( v & ~4 )); fi; $D $G $v >/dev/null; }
if [ -n "${INVIDEO:-}" ]; then w 0x38 $(( $(r 0x38) | 0x8000 )); w 0x94 1; else w 0x34 1; fi
w 0x68 0x010f7f00
if [ "${HSCLK:-on}" = off ]; then w 0x94 0; fi
if [ "${RESET:-no}" = yes ]; then gw 0; usleep 20000; gw 1; usleep 120000; fi
if [ -n "${CLK_AFTER_RESET:-}" ]; then w 0x94 1; usleep 20000; fi
if [ -n "${SETTLE_VIDEO_MS:-}" ]; then w 0x94 1; w 0x34 0; usleep $(( SETTLE_VIDEO_MS * 1000 )); w 0x34 1; fi
tcseq(){
tcw 0x013c 0x000a000c; tcw 0x0114 0x00000008
tcw 0x0164 0x0000000f; tcw 0x0168 0x0000000f; tcw 0x016c 0x0000000f; tcw 0x0170 0x0000000f
tcw 0x0134 0x0000001f; tcw 0x0210 0x0000001f; tcw 0x0104 0x00000001; tcw 0x0204 0x00000001
tcw 0x0450 0x03f00120; tcw 0x0454 0x00460028; tcw 0x0458 0x00460780
tcw 0x045c 0x000a0005; tcw 0x0460 0x000f0438; tcw 0x0464 0x00000001
tcw 0x04a0 0x00448006; usleep 5000; tcw 0x04a0 0x00048006; tcw 0x0504 0x00000004
tcw 0x0480 0x03020200; tcw 0x0484 0x08050704; tcw 0x0488 0x0f0e0a09; tcw 0x048c 0x100d0c0b
tcw 0x0490 0x12111716; tcw 0x0494 0x1b151413; tcw 0x0498 0x061a1918
tcw 0x049c 0x00000033; usleep 50000
}
i=0; while [ $i -lt ${PASSES:-1} ]; do
	tcseq
	# optionally start the HS clock between passes (clock lane LP->HS after reset)
	if [ -n "${CLK_BETWEEN:-}" ]; then w 0x94 1; usleep 20000; fi
	i=$((i+1))
done
echo "int0=$(r 0xbc) int1=$(r 0xc0) status=$(r 0x74)"
w 0x94 1
[ -z "${INVIDEO:-}" ] && w 0x34 0
echo done
