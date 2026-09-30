#!/bin/bash
# Build our cage package (system/cage: pinned release tarball + patches + APKBUILD) natively with abuild
# on an Alpine v3.24 aarch64 system reachable over tools/tssh (the panel, or the RAM installer) and copy
# the signed apk to build/out/cage-<ver>-r<rel>.apk.
#
#   tools/build-cage.sh [--root DIR] [--install]
#     --root DIR   build inside chroot DIR on the device (tools/install.sh: the new system on /mnt/t)
#     --install    afterwards install the apk into / (resp. DIR) and pin it in /etc/apk/world
#
# Build dependencies go into the virtual package .cage-build and are removed again, as is the build
# directory. Signed with the local key tools/apk-key/ (gitignored, created on first use); its public half
# is installed to /etc/apk/keys on the device (trust for apk index/add, no --allow-untrusted).
set -euo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
. "$P/tools/lib.sh"
SSH="$P/tools/tssh"
C=$P/system/cage OUT=$P/build/out SRC=$P/build/src
die() { echo "error: $*" >&2; exit 1; }

R= INSTALL=
while [ $# -gt 0 ]; do
	case $1 in
	--root) R=${2:?--root needs a directory}; shift ;;
	--install) INSTALL=1 ;;
	*) sed -n '2,13p' "$0"; exit 1 ;;
	esac
	shift
done

# shellcheck source=system/cage/VERSION
. "$C/VERSION"
V=$(cage_version)
[ "${V%-r*}" = "$VERSION" ] || die "pkgver in $C/APKBUILD ($V) does not match VERSION ($VERSION)"
TAR=$SRC/cage-$VERSION.tar.gz
mkdir -p "$SRC" "$OUT"
[ -f "$TAR" ] || curl -fsSL -o "$TAR" "$URL"
echo "$SHA256  $TAR" | sha256sum -c --quiet || die "checksum mismatch for $TAR"
KEY=$(apk_key)
# Reproducible timestamps: date of the newest patch (stable across re-exports), not the build time
EPOCH=$(sed -n 's/^Date: //p' "$C"/patches/*.patch | while read -r d; do date -d "$d" +%s; done | sort -n | tail -1)

B=/tmp/cage-build  # device side, inside the build root
cleanup() {
	$SSH "rm -rf $R$B; ${R:+chroot $R} apk del --wait 300 -q .cage-build 2> /dev/null || true
		if [ -n '$R' ] && [ -f $R/.cage-build-mounts ]; then
			grep -qx resolv $R/.cage-build-mounts && rm -f $R/etc/resolv.conf
			for d in sys proc dev; do grep -qx \$d $R/.cage-build-mounts && umount $R/\$d; done
			rm -f $R/.cage-build-mounts
		fi" || echo "warning: cleanup on the device failed" >&2
}
trap cleanup EXIT

# In a chroot the build also needs dev/proc/sys and resolv.conf (undone by cleanup)
if [ -n "$R" ]; then
	$SSH "[ -x $R/sbin/apk ] || { echo 'no Alpine system in $R' >&2; exit 1; }
		for d in dev proc sys; do mountpoint -q $R/\$d || { mount --bind /\$d $R/\$d && echo \$d >> $R/.cage-build-mounts; }; done
		[ -e $R/etc/resolv.conf ] || { cp /etc/resolv.conf $R/etc/resolv.conf && echo resolv >> $R/.cage-build-mounts; }"
	RUN="chroot $R /bin/sh -s"
else
	RUN="/bin/sh -s"
fi

echo "== sources to the device (cage $V), public key to /etc/apk/keys"
$SSH "mkdir -p $R/etc/apk/keys && cat > $R/etc/apk/keys/$(basename "$KEY").pub" < "$KEY.pub"
tar --owner=0 --group=0 -cf - -C "$C" APKBUILD patches -C "$SRC" "cage-$VERSION.tar.gz" -C "$(dirname "$KEY")" \
	"$(basename "$KEY")" "$(basename "$KEY").pub" |
	$SSH "rm -rf $R$B && mkdir -p $R$B/cage $R$B/dist $R$B/key && cd $R$B && tar -xf - -C cage &&
		mv cage/patches/*.patch cage/ && rmdir cage/patches && mv cage/cage-$VERSION.tar.gz dist/ &&
		mv cage/$(basename "$KEY")* key/ && chmod 600 key/$(basename "$KEY")"

echo "== build (abuild on $($SSH "${R:+chroot $R} cat /etc/alpine-release") $($SSH uname -m))"
$SSH "$RUN" <<EOF
set -eu
cd $B/cage
. ./APKBUILD
apk add --wait 300 -q -t .cage-build abuild build-base \$makedepends
apk info -e -v abuild gcc meson wlroots0.20-dev wayland-protocols wayland-dev | tr "\n" " " || true; echo
export PACKAGER_PRIVKEY=$B/key/$(basename "$KEY") PACKAGER='chriopter <82179548+chriopter@users.noreply.github.com>' SOURCE_DATE_EPOCH=$EPOCH
abuild -F -d -m -s $B/dist -P $B/repo > $B/build.log 2>&1 || { tail -40 $B/build.log; exit 1; }
grep -E '^[[:space:]]+xwayland +:|Package size|Create cage|ERROR|WARNING' $B/build.log || true
apk verify $B/repo/*/aarch64/cage-$V.apk
EOF
$SSH "cat $R$B/repo/*/aarch64/cage-$V.apk" > "$OUT/cage-$V.apk.new"
mv "$OUT/cage-$V.apk.new" "$OUT/cage-$V.apk"
ls -l "$OUT/cage-$V.apk"
sha256sum "$OUT/cage-$V.apk"

if [ -n "$INSTALL" ]; then
	echo "== install"
	install_cage "$SSH" "${R:-/}"
fi
