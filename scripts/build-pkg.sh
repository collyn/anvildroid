#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
VERSION=${1:-$(python3 "$ROOT/scripts/release-version.py")}
command -v zstd >/dev/null || { echo 'Missing build tool: zstd' >&2; exit 1; }
OUT="$ROOT/target/releases"
STAGE="$ROOT/target/pkg/anvildroid-controller-${VERSION}-1-x86_64"
rm -rf "$STAGE" "$OUT/anvildroid-controller-${VERSION}-1-x86_64.pkg.tar.zst"
mkdir -p "$OUT" "$STAGE/var/lib/anvildroid-controller" "$STAGE/run/anvildroid"
sh "$ROOT/scripts/build-runtime.sh"
sh "$ROOT/scripts/stage-package.sh" "$STAGE"
cat > "$STAGE/.PKGINFO" <<EOF
pkgname = anvildroid-controller
pkgver = ${VERSION}-1
pkgdesc = AnvilDroid Android runtime controller
url = https://github.com/collyn/anvildroid
builddate = $(date +%s)
packager = AnvilDroid contributors
size = 0
arch = x86_64
license = MIT
depend = python3
depend = systemd
depend = patchelf>=0.18
EOF
cat > "$STAGE/.INSTALL" <<'EOF'
post_install() {
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl enable anvildroid-controller.service >/dev/null 2>&1 || true
  systemctl restart anvildroid-controller.service >/dev/null 2>&1 || true
}
post_upgrade() { post_install; }
pre_remove() {
  systemctl disable --now anvildroid-controller.service >/dev/null 2>&1 || true
}
EOF
(cd "$STAGE" && find . -type f -o -type l | sort | tar --format=ustar --owner=0 --group=0 --numeric-owner -cf - -T - | zstd -19 -T0 > "$OUT/anvildroid-controller-${VERSION}-1-x86_64.pkg.tar.zst")
rm -rf "$STAGE"
tar --use-compress-program=zstd -tf "$OUT/anvildroid-controller-${VERSION}-1-x86_64.pkg.tar.zst" >/dev/null
sha256sum "$OUT/anvildroid-controller-${VERSION}-1-x86_64.pkg.tar.zst"
