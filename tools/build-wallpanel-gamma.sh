#!/bin/bash
# SPDX-License-Identifier: MIT
# Build our wallpanel-gamma package (system/wallpanel-gamma: own source + meson.build + APKBUILD) natively
# with abuild on an Alpine v3.24 aarch64 system reachable over tools/tssh (the panel, or the RAM
# installer) and copy the signed apk to build/out/wallpanel-gamma-<ver>-r<rel>.apk.
#
#   tools/build-wallpanel-gamma.sh [--root DIR] [--install]
#     --root DIR   build inside chroot DIR on the device (tools/install.sh: the new system on /mnt/t)
#     --install    afterwards install the apk into / (resp. DIR) and pin it in /etc/apk/world
#
# Same flow as tools/build-cage.sh: build deps in the virtual package .wallpanel-gamma-build, removed
# again with the build directory; signed with the local key tools/apk-key/. The sha512sums in the
# APKBUILD are refreshed from the sources first (our own source: no pin file).
set -euo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
. "$P/tools/lib.sh"
SSH="$P/tools/tssh"
G=$P/system/wallpanel-gamma OUT=$P/build/out
N=wallpanel-gamma
die() { echo "error: $*" >&2; exit 1; }

R= INSTALL=
while [ $# -gt 0 ]; do
	case $1 in
	--root) R=${2:?--root needs a directory}; shift ;;
	--install) INSTALL=1 ;;
	*) sed -n '3,13p' "$0"; exit 1 ;;
	esac
	shift
done

V=$(gamma_version)
MV=$(sed -n "s/^[[:space:]]*version: '\([^']*\)'.*/\1/p" "$G/meson.build")
[ "${V%-r*}" = "$MV" ] || die "pkgver in $G/APKBUILD ($V) does not match the meson.build version ($MV)"
SRCS="meson.build $N.c"
# sha512sums from the sources (only rewritten when they changed)
{
	sed '/^sha512sums="/,$d' "$G/APKBUILD"
	echo 'sha512sums="'
	(cd "$G" && sha512sum $SRCS)
	echo '"'
} > "$G/APKBUILD.new"
if cmp -s "$G/APKBUILD" "$G/APKBUILD.new"; then rm "$G/APKBUILD.new"; else mv "$G/APKBUILD.new" "$G/APKBUILD"; echo "sha512sums in $G/APKBUILD updated"; fi
mkdir -p "$OUT"
KEY=$(apk_key)
# Reproducible timestamps: last commit touching the sources, else their newest mtime (not the build time)
EPOCH=$(git -C "$P" log -1 --format=%ct -- system/wallpanel-gamma 2> /dev/null || true)
[ -n "$EPOCH" ] && git -C "$P" diff --quiet HEAD -- system/wallpanel-gamma 2> /dev/null ||
	EPOCH=$(cd "$G" && stat -c %Y APKBUILD $SRCS | sort -n | tail -1)

B=/tmp/$N-build  # device side, inside the build root
cleanup() {
	$SSH "rm -rf $R$B; ${R:+chroot $R} apk del --wait 300 -q .$N-build 2> /dev/null || true
		if [ -n '$R' ] && [ -f $R/.$N-build-mounts ]; then
			grep -qx resolv $R/.$N-build-mounts && rm -f $R/etc/resolv.conf
			for d in sys proc dev; do grep -qx \$d $R/.$N-build-mounts && umount $R/\$d; done
			rm -f $R/.$N-build-mounts
		fi" || echo "warning: cleanup on the device failed" >&2
}
trap cleanup EXIT

# In a chroot the build also needs dev/proc/sys and resolv.conf (undone by cleanup)
if [ -n "$R" ]; then
	$SSH "[ -x $R/sbin/apk ] || { echo 'no Alpine system in $R' >&2; exit 1; }
		for d in dev proc sys; do mountpoint -q $R/\$d || { mount --bind /\$d $R/\$d && echo \$d >> $R/.$N-build-mounts; }; done
		[ -e $R/etc/resolv.conf ] || { cp /etc/resolv.conf $R/etc/resolv.conf && echo resolv >> $R/.$N-build-mounts; }"
	RUN="chroot $R /bin/sh -s"
else
	RUN="/bin/sh -s"
fi

echo "== sources to the device ($N $V), public key to /etc/apk/keys"
$SSH "mkdir -p $R/etc/apk/keys && cat > $R/etc/apk/keys/$(basename "$KEY").pub" < "$KEY.pub"
# shellcheck disable=SC2086
tar --owner=0 --group=0 -cf - -C "$G" APKBUILD $SRCS -C "$(dirname "$KEY")" "$(basename "$KEY")" "$(basename "$KEY").pub" |
	$SSH "rm -rf $R$B && mkdir -p $R$B/$N $R$B/key && cd $R$B && tar -xf - -C $N &&
		mv $N/$(basename "$KEY")* key/ && chmod 600 key/$(basename "$KEY")"

echo "== build (abuild on $($SSH "${R:+chroot $R} cat /etc/alpine-release") $($SSH uname -m))"
$SSH "$RUN" <<EOF
set -eu
cd $B/$N
srcdir=. && . ./APKBUILD  # only for makedepends (builddir uses srcdir)
apk add --wait 300 -q -t .$N-build abuild build-base \$makedepends
apk info -e -v abuild gcc meson wayland-dev wlr-protocols | tr "\n" " " || true; echo
export PACKAGER_PRIVKEY=$B/key/$(basename "$KEY") PACKAGER='chriopter <82179548+chriopter@users.noreply.github.com>' SOURCE_DATE_EPOCH=$EPOCH
abuild -F -d -m -P $B/repo > $B/build.log 2>&1 || { tail -40 $B/build.log; exit 1; }
grep -E 'Package size|Create $N|ERROR|WARNING|warning:' $B/build.log || true
apk verify $B/repo/*/aarch64/$N-$V.apk
EOF
$SSH "cat $R$B/repo/*/aarch64/$N-$V.apk" > "$OUT/$N-$V.apk.new"
mv "$OUT/$N-$V.apk.new" "$OUT/$N-$V.apk"
ls -l "$OUT/$N-$V.apk"
sha256sum "$OUT/$N-$V.apk"

if [ -n "$INSTALL" ]; then
	echo "== install"
	install_gamma "$SSH" "${R:-/}"
fi
