#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Copy a local file to the RAM test system over the USB shell: usbput.py LOCAL REMOTE [mode]."""
import base64, os, subprocess, sys
local, remote = sys.argv[1], sys.argv[2]
mode = sys.argv[3] if len(sys.argv) > 3 else '644'
b64 = base64.b64encode(open(local, 'rb').read()).decode()
cmd = f"echo '{b64}' | base64 -d > {remote} && chmod {mode} {remote} && ls -l {remote}"
here = os.path.dirname(os.path.abspath(__file__))
subprocess.run([os.path.join(here, 'usbsh.py'), cmd], check=True)
