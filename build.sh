#!/bin/bash
# Build entry point. Sources are fetched at pinned versions into build/src,
# our patches are applied as git commits, outputs go to build/out.
#
#   ./build.sh fetch   kernel|uboot|cage   fetch pinned source + apply patches
#   ./build.sh kernel                      configure (defconfig + fragments) and build
#   ./build.sh release                     kernel release files + manifest.json in build/out/release
#   ./build.sh sign-release [KEY]          sign manifest.json (ssh-keygen -Y, default tools/kernel-key/)
#   ./build.sh uboot                       build the maskrom RAM-boot loader
#   ./build.sh helpers                     build wallpanel-rebootmode and devmem (freestanding)
#   ./build.sh image                       RAM installer boot image (build/out/boot-test-mainline.img)
#   ./build.sh cage [--install]            patched cage apk, built on the device (tools/build-cage.sh)
#   ./build.sh wallpanel-gamma [--install] gamma helper apk, built on the device (tools/build-wallpanel-gamma.sh)
#   ./build.sh export  kernel|uboot|cage   regenerate patches/ from build/src commits
set -euo pipefail

P=$(cd "$(dirname "$0")" && pwd)
SRC=$P/build/src
OUT=$P/build/out
TOOLS=$P/tools
KDIR=$P/system/kernel BDIR=$P/system/boot CDIR=$P/system/cage
ME_NAME=chriopter
ME_EMAIL=82179548+chriopter@users.noreply.github.com
mkdir -p "$SRC" "$OUT"

die() { echo "error: $*" >&2; exit 1; }

toolchain() {
	case $1 in
	mainline) GCC=gcc-15.3.0 ;;
	uboot) GCC=gcc-13.5.0 ;;
	esac
	[ -d "$TOOLS/$GCC-nolibc" ] || fetch_toolchain "$GCC"
	export PATH=$TOOLS/bin:$TOOLS/venv/bin:$TOOLS/$GCC-nolibc/aarch64-linux/bin:$PATH
	export CROSS_COMPILE=aarch64-linux- ARCH=arm64
	# Reproducible, anonymous version string (no user/host, no "+" suffix)
	export KBUILD_BUILD_USER=builder KBUILD_BUILD_HOST=iiyama-tw2424as LOCALVERSION=
}

fetch_toolchain() {
	local v=${1#gcc-}
	curl -fsSL "https://mirrors.edge.kernel.org/pub/tools/crosstool/files/bin/x86_64/$v/x86_64-gcc-$v-nolibc-aarch64-linux.tar.xz" |
		tar -C "$TOOLS" -xJ
}

git_id() { git -C "$1" config user.name "$ME_NAME"; git -C "$1" config user.email "$ME_EMAIL"; }

apply_patches() { # dir patchdir
	git_id "$1"
	git -C "$1" am -q --committer-date-is-author-date "$2"/*.patch
}

fetch_mainline() {
	# shellcheck source=system/kernel/VERSION
	. "$KDIR/VERSION"
	local tar=$SRC/linux-$VERSION.tar.xz dir=$SRC/linux
	[ -f "$tar" ] || curl -fsSL -o "$tar" "$URL"
	echo "$SHA256  $tar" | sha256sum -c --quiet || die "checksum mismatch for $tar"
	[ -d "$dir" ] && die "$dir exists; remove it to refetch"
	mkdir -p "$dir" && tar -C "$dir" --strip-components=1 -xJf "$tar"
	git -C "$dir" init -q -b base && git_id "$dir"
	git -C "$dir" add -A && git -C "$dir" commit -q -m "Linux $VERSION (kernel.org tarball)"
	git -C "$dir" tag "v$VERSION"
	git -C "$dir" checkout -q -b iiyama
	apply_patches "$dir" "$KDIR/patches"
}

fetch_cage() {
	# shellcheck source=system/cage/VERSION
	. "$CDIR/VERSION"
	local tar=$SRC/cage-$VERSION.tar.gz dir=$SRC/cage
	[ -f "$tar" ] || curl -fsSL -o "$tar" "$URL"
	echo "$SHA256  $tar" | sha256sum -c --quiet || die "checksum mismatch for $tar"
	[ -d "$dir" ] && die "$dir exists; remove it to refetch"
	mkdir -p "$dir" && tar -C "$dir" --strip-components=1 -xzf "$tar"
	git -C "$dir" init -q -b iiyama && git_id "$dir"
	# base commit dated like the release: same commit ids (and patch files) on every fetch
	git -C "$dir" add -A && GIT_AUTHOR_DATE="@$(stat -c %Y "$dir/meson.build")" GIT_COMMITTER_DATE="@$(stat -c %Y "$dir/meson.build")" \
		git -C "$dir" commit -q -m "cage $VERSION (release tarball)"
	git -C "$dir" tag base
	apply_patches "$dir" "$CDIR/patches"
}

cage_sums() { # APKBUILD: every patch in source=, sha512sums= (last block) regenerated
	# shellcheck source=system/cage/VERSION
	. "$CDIR/VERSION"
	local a=$CDIR/APKBUILD f
	for f in "$CDIR"/patches/*.patch; do
		grep -qE "^[[:space:]]*${f##*/}\$" "$a" || die "add ${f##*/} to source= in $a"
	done
	{
		sed '/^sha512sums="/,$d' "$a"
		echo 'sha512sums="'
		(cd "$SRC" && sha512sum "cage-$VERSION.tar.gz")
		(cd "$CDIR/patches" && sha512sum ./*.patch | sed 's|  \./|  |')
		echo '"'
	} > "$a.new" && mv "$a.new" "$a"
}

fetch_git() { # dir basefile patchdir
	local url branch commit
	read -r url branch commit < "$2"
	[ -d "$1" ] && die "$1 exists; remove it to refetch"
	git init -q "$1"
	git -C "$1" fetch -q --depth 1 "$url" "$commit"
	git -C "$1" checkout -q -b iiyama FETCH_HEAD
	git -C "$1" tag base
	apply_patches "$1" "$3"
}

config_kernel() { # srcdir outdir defconfig fragment...
	local src=$1 out=$2 def=$3 l bad=0
	shift 3
	make -s -C "$src" O="$out" "$def"
	"$src"/scripts/kconfig/merge_config.sh -m -O "$out" "$out/.config" "$@" > /dev/null
	make -s -C "$src" O="$out" olddefconfig
	while read -r l; do
		case $l in
		CONFIG_*=*)
			grep -qxF "$l" "$out/.config" || { echo "config not applied: $l" >&2; bad=1; } ;;
		"# CONFIG_"*" is not set")  # also fine if the symbol vanished (parent disabled)
			l=${l#\# }; l=${l% is not set}
			! grep -qE "^$l=" "$out/.config" || { echo "config not disabled: $l" >&2; bad=1; } ;;
		esac
	done < <(cat "$@")
	[ $bad = 0 ] || die "config fragment not fully applied"
}

case ${1:-} in
fetch)
	case ${2:-} in
	kernel) fetch_mainline ;;
	uboot)
		fetch_git "$SRC/u-boot" "$BDIR/BASE" "$BDIR/patches"
		read -r url commit _ < "$BDIR/RKBIN"
		[ -d "$SRC/rkbin" ] || { git init -q "$SRC/rkbin" && git -C "$SRC/rkbin" fetch -q --depth 1 "$url" "$commit" && git -C "$SRC/rkbin" checkout -q FETCH_HEAD; }
		read -r url commit < "$BDIR/RKUSBBOOT"
		[ -d "$SRC/rkusbboot" ] || { git init -q "$SRC/rkusbboot" && git -C "$SRC/rkusbboot" fetch -q --depth 1 "$url" "$commit" && git -C "$SRC/rkusbboot" checkout -q FETCH_HEAD; }
		make -s -C "$SRC/rkusbboot" ;;
	cage) fetch_cage ;;
	*) die "fetch kernel|uboot|cage" ;;
	esac ;;
kernel)
	toolchain mainline
	K=$SRC/linux O=$OUT/mainline
	# Build id from source tree + config: every different kernel gets its own release (and module
	# directory), so wallpanel-update never mixes modules of two builds. Deterministic per source state.
	BID=$( { git -C "$K" rev-parse 'HEAD^{tree}'; cat "$KDIR"/config.*; } | sha256sum | cut -c1-7)
	# (not named localversion*: the kernel build would read such a file in $O as a version suffix)
	echo "CONFIG_LOCALVERSION=\"-iiyama-$BID\"" > "$OUT/mainline-release.fragment"
	config_kernel "$K" "$O" defconfig "$KDIR/config.platforms" "$KDIR/config.fragment" "$OUT/mainline-release.fragment"
	make -C "$K" O="$O" -j"$(nproc)" DTC_FLAGS=-@ Image Image.gz modules rockchip/rk3399-iiyama-tw2424as.dtb ;;
release)
	# Release files of the built kernel (CI: .github/workflows/kernel.yml; PC: tools/build-bootimg.sh).
	# No boot image and no keys: the device assembles its own boot image (wallpanel-update fetch).
	toolchain mainline
	K=$SRC/linux O=$OUT/mainline R=$OUT/release MS=$OUT/modstage-release
	KREL=$(cat "$O/include/config/kernel.release")
	rm -rf "$R" "$MS" && mkdir -p "$R"
	make -s -C "$K" O="$O" INSTALL_MOD_PATH="$MS" INSTALL_MOD_STRIP=1 modules_install
	# without the build/source links (they point into this build tree)
	tar --owner=0 --group=0 --sort=name --mtime=@0 --exclude="$KREL/build" --exclude="$KREL/source" \
		-cf - -C "$MS/lib/modules" "$KREL" | gzip -9n > "$R/modules.tar.gz"
	cp "$O/arch/arm64/boot/Image" "$O/arch/arm64/boot/dts/rockchip/rk3399-iiyama-tw2424as.dtb" "$R/"
	grep -qaF "Linux version $KREL " "$R/Image" || die "Image does not contain release $KREL"
	GIT=${GITHUB_SHA:-$(git -C "$P" rev-parse HEAD 2>/dev/null || echo unknown)}
	[ -n "${GITHUB_SHA:-}" ] || git -C "$P" diff --quiet HEAD -- system/kernel 2>/dev/null || GIT=$GIT-dirty
	python3 "$TOOLS/kernel-manifest.py" "$R" "$KREL" "$KDIR" "$GIT"
	ls -l "$R" ;;
sign-release)
	# manifest.json holds the sha256 of every release file; its signature is checked on the device
	# against system/rootfs/overlay/etc/wallpanel/kernel-release.pub (ssh-keygen -Y verify).
	R=$OUT/release KEY=${2:-$TOOLS/kernel-key/kernel-release} PUB=$P/system/rootfs/overlay/etc/wallpanel/kernel-release.pub
	[ -f "$R/manifest.json" ] || die "run ./build.sh release first"
	[ -f "$KEY" ] || die "signing key $KEY missing"
	rm -f "$R/manifest.json.sig"
	ssh-keygen -q -Y sign -f "$KEY" -n wallpanel-kernel-release "$R/manifest.json" || die "signing failed"
	printf 'kernel-release namespaces="wallpanel-kernel-release" %s\n' "$(cut -d' ' -f1,2 "$PUB")" > "$R/allowed_signers"
	ssh-keygen -Y verify -f "$R/allowed_signers" -I kernel-release -n wallpanel-kernel-release \
		-s "$R/manifest.json.sig" < "$R/manifest.json" || die "signature does not match $PUB"
	rm -f "$R/allowed_signers" ;;
uboot)
	toolchain uboot
	U=$SRC/u-boot R=$SRC/rkbin/bin/rk33
	make -s -C "$U" iiyama-tw2424as-ramboot_defconfig
	make -C "$U" -j"$(nproc)" BL31="$R/rk3399_bl31_v1.36.elf" ROCKCHIP_TPL="$R/rk3399_ddr_800MHz_v1.30.bin"
	mkdir -p "$OUT/u-boot" && cp "$U"/u-boot-rockchip-usb47[12].bin "$OUT/u-boot/" ;;
helpers)
	toolchain mainline
	for c in "$P/system/rootfs/src/rebootmode.c" "$TOOLS/debug/devmem/devmem.c"; do
		aarch64-linux-gcc -O2 -static -nostdlib -ffreestanding -fno-stack-protector -o "$OUT/$(basename "$c" .c)" "$c"
	done
	ls -l "$OUT/rebootmode" "$OUT/devmem" ;;
image) "$TOOLS/build-test-image.sh" ;;
cage) shift; "$TOOLS/build-cage.sh" "$@" ;;
wallpanel-gamma) shift; "$TOOLS/build-wallpanel-gamma.sh" "$@" ;;
export)
	case ${2:-} in
	kernel) D=$SRC/linux B=base PD=$KDIR/patches ;;
	uboot) D=$SRC/u-boot B=base PD=$BDIR/patches ;;
	cage) D=$SRC/cage B=base PD=$CDIR/patches ;;
	*) die "export kernel|uboot|cage" ;;
	esac
	rm -f "$PD"/*.patch
	git -C "$D" format-patch -q --no-signature -o "$PD" "$B"..HEAD
	[ "$2" = cage ] && cage_sums
	ls "$PD" ;;
*)
	sed -n '2,14p' "$0"
	exit 1 ;;
esac
