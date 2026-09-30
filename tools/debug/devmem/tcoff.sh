#!/bin/sh
# SPDX-License-Identifier: MIT
# Disable the TC358775 LVDS output (LVCFG=0) so the panel goes dark again
D=/usr/sbin/devmem; B=0xff960000
r(){ $D $((B+$1)); }; w(){ $D $((B+$1)) $2 >/dev/null; }
w 0x34 1; w 0x94 1; w 0x68 0x010f7f00
w 0x70 $(( 0x9c | (0x04 << 8) )); w 0x70 0; w 0x6c $(( 0x29 | (6 << 8) )); usleep 20000
w 0x34 0; echo off
