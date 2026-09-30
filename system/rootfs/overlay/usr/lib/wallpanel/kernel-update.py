#!/usr/bin/env python3
"""Kernel releases for wallpanel-update (check, fetch, install-release, health, boot-start, boot-check).

A release (GitHub release "kernel-<release>" of the project repository, or a local directory in
WALLPANEL_KERNEL_RELEASE_DIR) holds Image, the board DTB, modules.tar.gz and manifest.json(.sig).
The manifest carries the sha256 of every file and is signed with the project key
(/etc/wallpanel/kernel-release.pub, ssh-keygen -Y). Releases contain no boot image and no keys:
the device assembles its own boot image (usr/lib/wallpanel/boot/mkboot.py) with its own
/root/.ssh/authorized_keys for the rescue mode.

install-release writes slot B, records a baseline health check, sets a pending test and reboots
once into slot B. There /etc/init.d/wallpanel-kernel-health runs boot-check: all checks pass ->
promote + reboot into the new slot A; a failed check -> reboot (slot A still holds the old kernel);
a hang -> hardware watchdog -> slot A. The result goes to last-result.json.
"""
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

STATE = "/var/lib/wallpanel/kernel-update"
RELEASE = f"{STATE}/release"
PENDING = f"{STATE}/pending.json"
LAST = f"{STATE}/last-result.json"
INSTALLED = f"{STATE}/installed.json"
BOOTIMG = f"{STATE}/boot.img"
LOCK = "/run/wallpanel-kernel-update.lock"
CACHE = "/run/wallpanel-kernel-update-check.json"
CACHE_S = 300                       # GitHub API: 60 unauthenticated requests per hour
PUB = "/etc/wallpanel/kernel-release.pub"
NS, SIGNER = "wallpanel-kernel-release", "kernel-release"
MKBOOT = "/usr/lib/wallpanel/boot/mkboot.py"
UPDATE = "/usr/sbin/wallpanel-update"
KEYS = "/root/.ssh/authorized_keys"
DTB = "rk3399-iiyama-tw2424as.dtb"
FILES = ("Image", DTB, "modules.tar.gz")
REPO = "chriopter/iiyama-tw2424as-linux"
SLOTS = {"A": "/dev/disk/by-partlabel/boot", "B": "/dev/disk/by-partlabel/recovery"}
HEALTH_TIMEOUT = int(os.environ.get("WALLPANEL_KERNEL_HEALTH_TIMEOUT", "180"))
HEALTH_MIN_UPTIME = 60
LOG = "/var/log/wallpanel-kernel-health.log"


class Fail(Exception):
    pass


# --- helpers -------------------------------------------------------------

def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def running_slot():
    m = re.search(r"wallpanel\.slot=([AB])", open("/proc/cmdline").read())
    return m.group(1) if m else None


def running_kernel():
    return os.uname().release


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v.split("-")[0]))


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def conf(key):
    try:
        for line in open("/etc/wallpanel/wallpanel.conf"):
            k, _, v = line.strip().partition("=")
            if k == key:
                return v.strip().strip("'\"")
    except OSError:
        pass
    return ""


def repo():
    return os.environ.get("WALLPANEL_KERNEL_REPO") or conf("KERNEL_REPO") or REPO


def local_dir():
    return os.environ.get("WALLPANEL_KERNEL_RELEASE_DIR") or ""


def progress(n, total, stage, msg):
    print(f"progress {n}/{total} {stage}: {msg}", flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def http(url, dest=None, api=False, cb=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": "wallpanel-update",
        "Accept": "application/vnd.github+json" if api else "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if dest is None:
            return r.read()
        total, done, last = int(r.headers.get("Content-Length") or 0), 0, 0
        with open(dest, "wb") as f:
            for b in iter(lambda: r.read(1 << 16), b""):
                f.write(b)
                done += len(b)
                if cb and time.monotonic() - last > 2:
                    last = time.monotonic()
                    cb(done, total)
        return None


class Lock:
    def __init__(self, wait=False):
        self.f = open(LOCK, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise Fail("another kernel update is running")


def busy():
    try:
        with open(LOCK, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    except OSError:
        return False


# --- signature + manifest ------------------------------------------------

def verify_manifest(manifest, sig):
    """Signature against the pinned public key, then the manifest's structure. Returns the manifest."""
    pub = open(PUB).read().split()
    with tempfile.NamedTemporaryFile("w", suffix=".signers") as a:
        a.write(f'{SIGNER} namespaces="{NS}" {pub[0]} {pub[1]}\n')
        a.flush()
        with open(manifest, "rb") as m:
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", a.name, "-I", SIGNER, "-n", NS, "-s", sig],
                               stdin=m, capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail(f"manifest signature invalid: {(r.stderr or r.stdout).strip()}")
    m = json.load(open(manifest))
    if m.get("format") != 1 or m.get("tag") != f"kernel-{m.get('kernel')}" or set(m.get("files", {})) != set(FILES):
        raise Fail("manifest has an unexpected format")
    if not re.fullmatch(r"[0-9]+\.[0-9]+(\.[0-9]+)?-iiyama-[0-9a-f]+", m["kernel"]):
        raise Fail(f"manifest: odd kernel release {m['kernel']!r}")
    return m


def latest(refresh=False):
    """The newest release: {tag, kernel, version, published, changelog, manifest, urls, source}."""
    d = local_dir()
    if d:
        m = verify_manifest(f"{d}/manifest.json", f"{d}/manifest.json.sig")
        return dict(tag=m["tag"], kernel=m["kernel"], version=m["version"], published=m.get("date"),
                    changelog=m.get("changelog", []), manifest=m, source=f"dir:{d}",
                    urls={n: f"{d}/{n}" for n in FILES + ("manifest.json", "manifest.json.sig")})
    rp = repo()
    c = read_json(CACHE)
    if not refresh and c and c.get("repo") == rp and time.time() - c.get("time", 0) < CACHE_S:
        return c["release"]
    rels = json.loads(http(f"https://api.github.com/repos/{rp}/releases?per_page=30", api=True))
    cands = [r for r in rels if r.get("tag_name", "").startswith("kernel-")
             and not r.get("draft") and not r.get("prerelease")]
    rel = None
    if cands:
        r = max(cands, key=lambda r: r.get("published_at") or "")
        urls = {a["name"]: a["browser_download_url"] for a in r.get("assets", [])}
        missing = [n for n in FILES + ("manifest.json", "manifest.json.sig") if n not in urls]
        if missing:
            raise Fail(f"release {r['tag_name']} lacks {', '.join(missing)}")
        with tempfile.TemporaryDirectory() as t:
            http(urls["manifest.json"], f"{t}/manifest.json")
            http(urls["manifest.json.sig"], f"{t}/manifest.json.sig")
            m = verify_manifest(f"{t}/manifest.json", f"{t}/manifest.json.sig")
        if m["tag"] != r["tag_name"]:
            raise Fail(f"release {r['tag_name']} carries the manifest of {m['tag']}")
        rel = dict(tag=m["tag"], kernel=m["kernel"], version=m["version"], published=r.get("published_at"),
                   changelog=m.get("changelog", []), manifest=m, source=f"github:{rp}", urls=urls)
    try:
        write_json(CACHE, {"time": time.time(), "repo": rp, "release": rel})
    except OSError:
        pass
    return rel


def is_newer(rel, kernel=None):
    kernel = kernel or running_kernel()
    return bool(rel) and rel["kernel"] != kernel and version_tuple(rel["version"]) >= version_tuple(kernel)


# --- check ---------------------------------------------------------------

def check(as_json, refresh=False):
    out = dict(running_slot=running_slot(), running_kernel=running_kernel(), available=None,
               update_available=False, last_result=read_json(LAST), pending=read_json(PENDING),
               busy=busy(), source=f"dir:{local_dir()}" if local_dir() else f"github:{repo()}", error=None)
    if out["pending"]:
        out["pending"].pop("baseline", None)
    try:
        rel = latest(refresh)
        if rel:
            out["available"] = {k: rel[k] for k in ("tag", "kernel", "published", "changelog")}
            out["update_available"] = is_newer(rel)
    except Exception as e:  # network, GitHub, signature: reported, never fatal for the UI
        out["error"] = str(e)
    if as_json:
        print(json.dumps(out))
        return 0
    print(f"running: slot {out['running_slot'] or '?'}, kernel {out['running_kernel']}  ({out['source']})")
    a = out["available"]
    if a:
        print(f"latest:  {a['tag']} ({a['published']}) - {'UPDATE AVAILABLE' if out['update_available'] else 'no update'}")
        for c in a["changelog"]:
            print(f"  - {c}")
    elif not out["error"]:
        print("latest:  no kernel release found")
    if out["error"]:
        print(f"error:   {out['error']}")
    if out["pending"]:
        print(f"pending: test of {out['pending'].get('to')} since {out['pending'].get('started')}")
    if out["last_result"]:
        r = out["last_result"]
        print(f"last:    {r.get('time')} {r.get('from')} -> {r.get('to')}: {'ok' if r.get('ok') else 'FAILED'} ({r.get('reason')})")
    return 0


# --- fetch + assemble ----------------------------------------------------

def image_release(path):
    with open(path, "rb") as f:
        m = re.search(rb"Linux version (\S+) ", f.read())
    return m.group(1).decode() if m else ""


def fetch(rel=None, total=6, step=1):
    """Download + verify into STATE/release, assemble STATE/boot.img. Idempotent."""
    rel = rel or latest(refresh=True)
    if not rel:
        raise Fail("no kernel release available")
    m = rel["manifest"]
    os.makedirs(RELEASE, exist_ok=True)
    old = read_json(f"{RELEASE}/manifest.json")
    if not old or old.get("tag") != m["tag"]:
        for n in os.listdir(RELEASE):
            os.remove(f"{RELEASE}/{n}")
    for f in ("manifest.json", "manifest.json.sig"):
        get(rel, f, f"{RELEASE}/{f}")
    m = verify_manifest(f"{RELEASE}/manifest.json", f"{RELEASE}/manifest.json.sig")
    if m["tag"] != rel["tag"]:
        raise Fail(f"manifest changed while fetching ({m['tag']} != {rel['tag']})")
    progress(step, total, "download", f"{m['tag']} from {rel['source']}")
    for n in FILES:
        want = m["files"][n]
        dest = f"{RELEASE}/{n}"
        if os.path.exists(dest) and os.path.getsize(dest) == want["size"] and sha256_file(dest) == want["sha256"]:
            progress(step, total, "download", f"{n} already present")
            continue
        progress(step, total, "download", f"{n} ({want['size'] / 1048576:.1f} MiB)")
        get(rel, n, f"{dest}.part", lambda d, t, n=n: progress(step, total, "download", f"{n} {d / 1048576:.1f}/{t / 1048576:.1f} MiB"))
        if os.path.getsize(f"{dest}.part") != want["size"] or sha256_file(f"{dest}.part") != want["sha256"]:
            os.remove(f"{dest}.part")
            raise Fail(f"{n}: sha256/size does not match the signed manifest")
        os.replace(f"{dest}.part", dest)
    progress(step + 1, total, "verify", "signature and sha256 of all files ok")
    if image_release(f"{RELEASE}/Image") != m["kernel"]:
        raise Fail(f"Image is not kernel {m['kernel']}")
    assemble(m, step + 2, total)
    return m


def get(rel, name, dest, cb=None):
    src = rel["urls"][name]
    if rel["source"].startswith("dir:"):
        shutil.copyfile(src, dest)
    else:
        http(src, dest, cb=cb)


def assemble(m, step, total):
    progress(step, total, "assemble", f"rescue initramfs + boot image for {m['kernel']} (keys: {KEYS})")
    rd = f"{STATE}/initramfs.cpio.gz"
    for args in (["initramfs", "--modules", f"{RELEASE}/modules.tar.gz", "--keys", KEYS, "--src", "/", "-o", rd],
                 ["image", "--kernel", f"{RELEASE}/Image", "--dtb", f"{RELEASE}/{DTB}", "--ramdisk", rd,
                  "--slot", "A", "-o", f"{BOOTIMG}.new"]):
        r = subprocess.run([sys.executable, MKBOOT] + args, capture_output=True, text=True)
        if r.returncode != 0:
            raise Fail(f"assembling the boot image failed: {(r.stderr or r.stdout).strip()}")
        progress(step, total, "assemble", r.stdout.strip())
    os.replace(f"{BOOTIMG}.new", BOOTIMG)
    write_json(f"{STATE}/assembled.json", {"tag": m["tag"], "kernel": m["kernel"], "time": now(),
                                           "boot_sha256": sha256_file(BOOTIMG), "keys_sha256": sha256_file(KEYS)})


# --- health --------------------------------------------------------------

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def emmc_host():
    dev = next(line.split()[0] for line in open("/proc/mounts") if line.split()[1] == "/")
    blk = os.path.basename(os.path.realpath(dev))
    disk = os.path.basename(os.path.dirname(os.path.realpath(f"/sys/class/block/{blk}")))
    if not disk.startswith("mmcblk"):
        disk = blk
    return os.path.basename(os.path.dirname(os.path.realpath(f"/sys/class/block/{disk}/device")))


def check_emmc():
    host = emmc_host()
    path = f"/sys/kernel/debug/{host}/err_stats"
    if not os.path.exists(path):
        run(["mount", "-t", "debugfs", "debugfs", "/sys/kernel/debug"])
    try:
        bad = [line.strip() for line in open(path) if int(line.rsplit(":", 1)[1]) != 0]
    except OSError as e:
        return False, f"{path}: {e}"
    return not bad, f"{host} err_stats " + ("all 0" if not bad else "; ".join(bad))


def check_network():
    wl = [n for n in os.listdir("/sys/class/net") if os.path.isdir(f"/sys/class/net/{n}/wireless")]
    up = [n for n in wl if open(f"/sys/class/net/{n}/operstate").read().strip() == "up"]
    routes = [line.split()[0] for line in open("/proc/net/route").readlines()[1:]
              if line.split()[1] == "00000000" and int(line.split()[3], 16) & 1]
    ok = bool(up) and bool(routes)
    return ok, f"wifi up: {','.join(up) or 'none'}; default route via {','.join(routes) or 'none'}"


def check_api():
    rc, _ = run(["rc-service", "-q", "wallpanel-api", "status"])
    if rc != 0:
        return False, "wallpanel-api not started"
    port = int(conf("MQTT_PORT") or 1883)
    est = 0
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in open(f).readlines()[1:]:
                p = line.split()
                if p[3] == "01" and int(p[2].rsplit(":", 1)[1], 16) == port:
                    est += 1
        except OSError:
            pass
    return est > 0, f"wallpanel-api started, MQTT connection to port {port}: {'established' if est else 'none'}"


def check_kiosk():
    rc, _ = run(["rc-service", "-q", "wallpanel-kiosk", "status"])
    if rc != 0:
        return False, "wallpanel-kiosk not started"
    try:
        import websocket
        targets = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3))
        page = next(t for t in targets if t["type"] == "page" and not t["url"].startswith("chrome-extension:"))
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        try:
            expr = ("new Promise(r => { const t = setTimeout(() => r('norender ' + document.readyState), 4000);"
                    " requestAnimationFrame(() => requestAnimationFrame(() => { clearTimeout(t);"
                    " r('frames ' + document.readyState); })); })")
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    break
        finally:
            ws.close()
        v = str(msg.get("result", {}).get("result", {}).get("value"))
        url = page["url"]
        ok = v == "frames complete" and not url.startswith("chrome-error:")
        return ok, f"page {url.split('?')[0]}: {v}"
    except Exception as e:
        return False, f"DevTools: {e}"


def check_services():
    _, crashed = run(["rc-status", "--crashed"])
    bad = crashed.split()
    for rl in ("boot", "default"):
        _, out = run(["rc-status", rl])
        for line in out.splitlines():
            m = re.match(r"\s*(\S+)\s+\[\s*(\w+)", line)
            if m and m.group(2) != "started":
                bad.append(f"{m.group(1)}={m.group(2)}")
    return not bad, "all boot/default services started, none crashed" if not bad else " ".join(sorted(set(bad)))


def health(expect_kernel=None):
    expect_kernel = expect_kernel or running_kernel()
    res = {"kernel": (running_kernel() == expect_kernel, f"running {running_kernel()}, expected {expect_kernel}")}
    for name, fn in (("emmc", check_emmc), ("network", check_network), ("api", check_api),
                     ("kiosk", check_kiosk), ("services", check_services)):
        try:
            res[name] = fn()
        except Exception as e:
            res[name] = (False, f"check failed: {e}")
    return {k: {"ok": bool(v[0]), "detail": v[1]} for k, v in res.items()}


MANDATORY = ("kernel", "emmc")   # always required, even if they failed before the update


# --- install-release -----------------------------------------------------

def record(ok, reason, frm, to, tag, checks=None):
    r = {"time": now(), "from": frm, "to": to, "tag": tag, "ok": ok, "reason": reason}
    if checks is not None:
        r["checks"] = checks
    write_json(LAST, r)
    return r


def install_release(force=False):
    total = 6
    frm = running_kernel()
    rel, tag = None, None
    try:
        lock = Lock()  # noqa: F841 (held until the reboot)
        if running_slot() != "A":
            raise Fail(f"install-release only from slot A (running: {running_slot() or '?'})")
        if os.path.exists(PENDING):
            raise Fail("a kernel test is already pending")
        progress(1, total, "check", "looking for the latest kernel release")
        rel = latest(refresh=True)
        if not rel:
            raise Fail("no kernel release available")
        tag = rel["tag"]
        if not is_newer(rel) and not force:
            raise Fail(f"{rel['kernel']} is not newer than the running {frm} (use --force)")
        progress(1, total, "check", f"{frm} -> {rel['kernel']}")
        m = fetch(rel, total, 2)
        progress(5, total, "install", "writing slot B + modules")
        p = subprocess.Popen([UPDATE, "install", BOOTIMG, f"{RELEASE}/modules.tar.gz"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            progress(5, total, "install", line.strip())
        if p.wait() != 0:
            raise Fail("writing slot B failed")
        progress(5, total, "install", "baseline health check on the running kernel")
        base = health()
        for k, v in base.items():
            progress(5, total, "install", f"baseline {k}: {'ok' if v['ok'] else 'FAIL'} ({v['detail']})")
        write_json(PENDING, {"tag": m["tag"], "from": frm, "to": m["kernel"], "started": now(),
                             "baseline": {k: v["ok"] for k, v in base.items()}})
        progress(6, total, "test", "rebooting once into slot B; the health check promotes or falls back")
        print(f"result ok rebooting into slot B to test {m['kernel']}", flush=True)
        os.sync()
        rc, out = run([UPDATE, "test"])   # does not return when the reboot works
        os.remove(PENDING)
        raise Fail(f"reboot into slot B failed ({rc}): {out}")
    except Exception as e:
        if not isinstance(e, Fail) or str(e) != "another kernel update is running":
            record(False, f"install-release: {e}", frm, rel["kernel"] if rel else None, tag)
        print(f"result error {e}", flush=True)
        return 1


# --- boot: slot B health check -------------------------------------------

def log(msg):
    print(f"{now()} {msg}", flush=True)


def slot_release(dev):
    with open(dev, "rb") as f:
        h = f.read(1664)
        if h[:8] != b"ANDROID!":
            return ""
        ks, = struct.unpack_from("<I", h, 8)
        ps, = struct.unpack_from("<I", h, 36)
        f.seek(ps)
        m = re.search(rb"Linux version (\S+) ", f.read(ks))
    return m.group(1).decode() if m else ""


def cleanup():
    """After a promote: drop the release download and modules no slot uses any more."""
    keep = {slot_release(d) for d in SLOTS.values()} | {running_kernel()}
    for r in os.listdir("/lib/modules"):
        if r not in keep and re.fullmatch(r".*-iiyama-[0-9a-f]+", r):
            shutil.rmtree(f"/lib/modules/{r}", ignore_errors=True)
            log(f"removed /lib/modules/{r}")
    shutil.rmtree(RELEASE, ignore_errors=True)
    for f in (BOOTIMG, f"{STATE}/initramfs.cpio.gz", f"{STATE}/assembled.json"):
        if os.path.exists(f):
            os.remove(f)


def boot_start():
    """OpenRC start of wallpanel-kernel-health: returns at once, the check runs detached."""
    p = read_json(PENDING)
    if not p:
        return 0
    if running_slot() == "B":
        with open(LOG, "a") as out:
            subprocess.Popen([UPDATE, "boot-check"], stdout=out, stderr=out,
                             stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        print(f"kernel test of {p.get('to')}: health check started ({LOG})")
        return 0
    # pending but running slot A: the slot-B boot never finished its check
    r = record(False, "slot B test did not complete (no boot into slot B, hang/watchdog reset or power loss); "
               f"running slot {running_slot() or '?'} with {running_kernel()}", p.get("from"), p.get("to"), p.get("tag"))
    os.remove(PENDING)
    print(f"kernel test of {p.get('to')} failed: {r['reason']}")
    return 0


def boot_check():
    p = read_json(PENDING) or {}
    to, frm, tag = p.get("to"), p.get("from"), p.get("tag")
    base = p.get("baseline", {})
    required = [k for k in ("kernel", "emmc", "network", "api", "kiosk", "services")
                if k in MANDATORY or base.get(k)]
    log(f"health check for {to} (from {frm}); required: {', '.join(required)}; timeout {HEALTH_TIMEOUT} s")
    t0, good, res, ok = time.monotonic(), 0, {}, False
    try:
        while True:
            res = health(to)
            failing = [k for k in required if not res[k]["ok"]]
            log("; ".join(f"{k}={'ok' if v['ok'] else 'FAIL'}" for k, v in res.items()))
            if any(k in MANDATORY for k in failing):
                reason = "; ".join(f"{k}: {res[k]['detail']}" for k in failing if k in MANDATORY)
                break
            uptime = float(open("/proc/uptime").read().split()[0])
            good = good + 1 if not failing else 0
            if good >= 2 and uptime >= HEALTH_MIN_UPTIME:
                ok, reason = True, "all required checks passed"
                break
            if time.monotonic() - t0 > HEALTH_TIMEOUT:
                reason = f"timeout after {HEALTH_TIMEOUT} s: " + "; ".join(f"{k}: {res[k]['detail']}" for k in failing)
                break
            time.sleep(10)
        not_req = [k for k in res if k not in required]
        if not_req:
            reason += f" (not required, failed before the update too: {', '.join(not_req)})"
        if ok:
            rc, out = run([UPDATE, "promote"])
            log(f"promote: {out}")
            if rc != 0:
                ok, reason = False, f"promote failed: {out}"
    except Exception as e:
        ok, reason = False, f"health check crashed: {e}"
    checks = {k: v for k, v in res.items()}
    record(ok, reason, frm, to, tag, checks)
    if ok:
        write_json(INSTALLED, {"tag": tag, "kernel": to, "time": now()})
        try:
            cleanup()
        except Exception as e:
            log(f"cleanup: {e}")
    os.remove(PENDING)
    log(f"result: {'ok' if ok else 'FAILED'} - {reason}; rebooting into slot A")
    os.sync()
    subprocess.run(["reboot"])
    return 0


# --- main ------------------------------------------------------------------

def main(argv):
    cmd = argv[0] if argv else ""
    try:
        if cmd == "check":
            return check("--json" in argv, "--refresh" in argv)
        if cmd == "fetch":
            _lock = Lock()  # noqa: F841
            fetch()
            print(f"ready: {BOOTIMG} + {RELEASE}/modules.tar.gz", flush=True)
            return 0
        if cmd == "install-release":
            return install_release("--force" in argv)
        if cmd == "health":
            res = health()
            if "--json" in argv:
                print(json.dumps(res))
            else:
                for k, v in res.items():
                    print(f"{k:9} {'ok  ' if v['ok'] else 'FAIL'} {v['detail']}")
            return 0 if all(v["ok"] for v in res.values()) else 1
        if cmd == "boot-start":
            return boot_start()
        if cmd == "boot-check":
            return boot_check()
    except Fail as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
