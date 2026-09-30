#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run a shell command on the RAM test system over the USB gadget link (tcp/2323)."""
import os, socket, sys, time
# NetworkManager profile 'tw2424as-usb' gives the host 10.42.0.2/24
for attempt in range(10):  # the target restarts nc between sessions
    try:
        s = socket.create_connection(('10.42.0.1', 2323), timeout=15)
        break
    except (ConnectionRefusedError, OSError):
        time.sleep(1)
else:
    sys.exit('no shell on 10.42.0.1:2323')
s.sendall((' '.join(sys.argv[1:]) + '\necho __END__\nexit\n').encode())
out = b''
end = time.time() + float(os.environ.get('USBSH_TIMEOUT', '30'))
while time.time() < end and b'__END__' not in out:
    try:
        c = s.recv(65536)
    except socket.timeout:
        break
    if not c:
        break
    out += c
print(out.decode(errors='replace').replace('__END__', '').rstrip())
