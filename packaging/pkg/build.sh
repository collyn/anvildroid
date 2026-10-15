#!/bin/sh
set -eu
umask 022
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
VERSION=${1:-$(python3 "$ROOT/scripts/release-version.py")}
case "$VERSION" in ''|*[!0-9A-Za-z.+_-]*) echo "Invalid version: $VERSION" >&2; exit 2;; esac
command -v zstd >/dev/null || { echo 'Missing build tool: zstd' >&2; exit 1; }
OUT="$ROOT/target/releases"
STAGE="$ROOT/target/pkg/anvildroid-controller-${VERSION}-1-x86_64"
rm -rf "$STAGE" "$OUT/anvildroid-controller-${VERSION}-1-x86_64.pkg.tar.zst"
mkdir -p "$OUT"
sh "$ROOT/scripts/stage-package.sh" "$STAGE"
INSTALLED_SIZE=$(du -sk "$STAGE" | awk '{print $1 * 1024}')
cat > "$STAGE/.PKGINFO" <<EOF
pkgname = anvildroid-controller
pkgver = ${VERSION}-1
pkgdesc = AnvilDroid GUI, CLI and Android runtime controller
url = https://github.com/collyn/anvildroid
builddate = $(date +%s)
packager = AnvilDroid contributors
size = $INSTALLED_SIZE
arch = x86_64
license = MIT
license = BSD-3-Clause
license = Apache-2.0
depend = glibc>=2.39
depend = libepoxy
depend = libdrm
depend = mesa
depend = libx11
depend = expat
depend = vulkan-icd-loader
depend = util-linux
depend = iproute2
depend = nftables
depend = slirp4netns
depend = lxc
depend = kmod
depend = curl
depend = weston
depend = python3
depend = java-runtime-headless>=17
depend = systemd
depend = patchelf>=0.18
depend = webkit2gtk-4.1
depend = gtk3
depend = glib2
depend = libsoup3
depend = pango
depend = cairo
depend = gdk-pixbuf2
EOF
cat > "$STAGE/.INSTALL" <<'EOF'
post_install() {
  python3 /usr/local/lib/anvildroid-controller/scripts/install-runtime-apparmor.py || return 1
  python3 /usr/local/lib/anvildroid-controller/scripts/patch-waydroid.py >/dev/null 2>&1 || true
  net=/usr/lib/waydroid/data/scripts/waydroid-net.sh
  if [ -f "$net" ] && command -v iptables-nft >/dev/null 2>&1 && command -v iptables-legacy >/dev/null 2>&1 && ! iptables-legacy -t filter -L >/dev/null 2>&1 && grep -q 'command -v iptables-legacy' "$net"; then
    [ -e "$net.anvildroid-legacy-backup" ] || cp -p "$net" "$net.anvildroid-legacy-backup"
    sed -i 's/command -v iptables-legacy/command -v iptables-nft/g; s/command -v ip6tables-legacy/command -v ip6tables-nft/g' "$net"
  fi
  if [ -d /run/systemd/system ]; then
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl enable anvildroid-controller.service >/dev/null 2>&1 || true
  systemctl restart anvildroid-controller.service >/dev/null 2>&1 || true
  fi
}
post_upgrade() { post_install; }
pre_remove() {
  if [ -d /run/systemd/system ]; then
    systemctl disable --now anvildroid-controller.service >/dev/null 2>&1 || true
  fi
}
EOF
# Include directory entries so pacman restores the staged permissions, even
# when the installer/build host has a permissive umask. Keep pipeline failures
# visible in POSIX sh by separating archive creation from compression.
ARCHIVE=$(mktemp "$OUT/.anvildroid-pkg.XXXXXX")
trap 'rm -f "$ARCHIVE"' EXIT HUP INT TERM
tar --format=ustar --transform='s,^\./,,' --owner=0 --group=0 --numeric-owner -cf "$ARCHIVE" -C "$STAGE" .
zstd -19 -T0 -f "$ARCHIVE" -o "$OUT/anvildroid-controller-${VERSION}-1-x86_64.pkg.tar.zst"
chmod 0644 "$OUT/anvildroid-controller-${VERSION}-1-x86_64.pkg.tar.zst"
rm -f "$ARCHIVE"
trap - EXIT HUP INT TERM
rm -rf "$STAGE"
tar --use-compress-program=zstd -tf "$OUT/anvildroid-controller-${VERSION}-1-x86_64.pkg.tar.zst" >/dev/null
sha256sum "$OUT/anvildroid-controller-${VERSION}-1-x86_64.pkg.tar.zst"
