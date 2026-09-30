#!/bin/bash
# SPDX-License-Identifier: MIT
# Save the current camera frame (display view) with a timestamped name; prints the path.
# CAM_DIR (tools/local.env): directory where the camera capture keeps latest.jpg
. "$(dirname "$(readlink -f "$0")")/../local.env"
S=${CAM_DIR:?set CAM_DIR in tools/local.env}
mkdir -p $S/snaps; f=$S/snaps/$(date +%H%M%S)-${1:-snap}.jpg; cp $S/latest.jpg "$f"; echo "$f"
