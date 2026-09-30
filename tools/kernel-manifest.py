#!/usr/bin/env python3
"""manifest.json of a kernel release (./build.sh release): release, upstream pin, git commit,
sha256 + size of every release file, short changelog (upstream version + our patch subjects).

  kernel-manifest.py RELEASE_DIR KERNEL_RELEASE system/kernel GIT_SHA
"""
import email
import glob
import hashlib
import json
import re
import sys
import time

FILES = ("Image", "rk3399-iiyama-tw2424as.dtb", "modules.tar.gz")

r, krel, kdir, git = sys.argv[1:]
ver = dict(line.split("=", 1) for line in open(f"{kdir}/VERSION").read().split())
subjects = [re.sub(r"^\[PATCH[^]]*\]\s*", "", " ".join(email.message_from_file(open(p))["Subject"].split()))
            for p in sorted(glob.glob(f"{kdir}/patches/*.patch"))]
files = {}
for n in FILES:
    d = open(f"{r}/{n}", "rb").read()
    files[n] = {"sha256": hashlib.sha256(d).hexdigest(), "size": len(d)}
m = {
    "format": 1,
    "tag": f"kernel-{krel}",
    "kernel": krel,
    "version": ver["VERSION"],
    "upstream": {"url": ver["URL"], "sha256": ver["SHA256"]},
    "git": git,
    "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "files": files,
    "changelog": [f"Linux {ver['VERSION']} (kernel.org stable)"] + subjects,
}
with open(f"{r}/manifest.json", "w") as f:
    json.dump(m, f, indent=1)
    f.write("\n")
print(f"{r}/manifest.json: {m['tag']}")
