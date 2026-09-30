#!/bin/bash
# SPDX-License-Identifier: MIT
# Power-cycle the display via the Shelly plug, honouring the shared-plug rules:
# off for >= 10 s, and >= 60 s on-time since the last switch-on before cycling.
D=$(dirname "$(readlink -f "$0")")
. "$D/../local.env"   # ADB_SERIAL, SHELLY_IP, ... (gitignored)
set -euo pipefail
IP=$SHELLY_IP
STATE=$D/.shelly_last_on
rpc() { curl -fsS -m 5 "http://$IP/rpc/$1"; }
case ${1:-status} in
status) rpc "Switch.GetStatus?id=0"; echo ;;
cycle)
	if [ -f "$STATE" ]; then
		wait=$(( $(cat "$STATE") + 60 - $(date +%s) ))
		[ $wait -gt 0 ] && { echo "waiting ${wait}s (min on-time)"; sleep $wait; }
	fi
	rpc "Switch.Set?id=0&on=false" >/dev/null; echo "off $(date +%T)"
	sleep 12
	rpc "Switch.Set?id=0&on=true" >/dev/null; date +%s > "$STATE"; echo "on  $(date +%T)" ;;
*) echo "usage: $0 status|cycle" >&2; exit 1 ;;
esac
