#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mean luminance of the display area in the camera frame (0-255). Dark ~<60, lit ~>150."""
import sys
from PIL import Image, ImageStat
img = Image.open(sys.argv[1]).convert("L")
box = (500, 120, 1030, 320)  # inside the panel for the current camera position
print(round(ImageStat.Stat(img.crop(box)).mean[0]))
