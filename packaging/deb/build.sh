#!/bin/sh
set -eu
umask 022
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
VER=${1:-$(python3 "$ROOT/scripts/release-version.py" --debian)}
case "$VER" in
  ''|*[!0-9A-Za-z.+~-]*) echo "Invalid package version: $VER" >&2; exit 2 ;;
esac
dpkg --validate-version "$VER"
OUT="$ROOT/target/releases"
PKG="$OUT/anvildroid-$VER"
rm -rf "$PKG" "$OUT/anvildroid-controller_${VER}_amd64.deb"
sh "$ROOT/scripts/stage-package.sh" "$PKG"
mkdir -p "$PKG/DEBIAN"
cat > "$PKG/DEBIAN/control" <<EOF
Package: anvildroid-controller
Version: $VER
Section: misc
Priority: optional
Architecture: amd64
Maintainer: AnvilDroid
Pre-Depends: python3
Recommends: adb
Description: AnvilDroid GUI, CLI and Android runtime controller
 GPU accelerated Waydroid runtime controller with BinderFS, Wayland and native X11 support.
Depends: libc6 (>= 2.39), libepoxy0, libdrm2, libgbm1, libx11-6, libxcb1, libegl1, libgles2, libwayland-server0, libxkbcommon0, libxcursor1, libxi6, libxfixes3, libxext6, libstdc++6, libgcc-s1, libexpat1, libvulkan1, util-linux, mount, iproute2, nftables, slirp4netns, lxc, kmod, curl, weston, python3, default-jre-headless (>= 2:1.17), systemd, patchelf (>= 0.18), libwebkit2gtk-4.1-0, libgtk-3-0, libglib2.0-0, libjavascriptcoregtk-4.1-0, libsoup-3.0-0, libpango-1.0-0, libcairo2, libgdk-pixbuf-2.0-0, libayatana-appindicator3-1 | libappindicator3-1
EOF
install -m 0755 "$ROOT/packaging/deb/preinst.py" "$PKG/DEBIAN/preinst"
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
install -d -m 0700 /var/lib/anvildroid-controller
install -d -m 0755 /run/anvildroid
python3 /usr/local/lib/anvildroid-controller/scripts/install-runtime-apparmor.py
python3 /usr/local/lib/anvildroid-controller/scripts/patch-waydroid.py || true
# Ubuntu kernels commonly provide nftables without the legacy iptables tables
# expected by Waydroid's bundled network helper.
net=/usr/lib/waydroid/data/scripts/waydroid-net.sh
if [ -f "$net" ] && command -v iptables-nft >/dev/null 2>&1 \
  && command -v iptables-legacy >/dev/null 2>&1 \
  && ! iptables-legacy -t filter -L >/dev/null 2>&1 \
  && grep -q 'command -v iptables-legacy' "$net"; then
  backup="$net.anvildroid-legacy-backup"
  [ -e "$backup" ] || cp -p "$net" "$backup"
  sed -i 's/command -v iptables-legacy/command -v iptables-nft/g; s/command -v ip6tables-legacy/command -v ip6tables-nft/g' "$net"
fi
if [ -d /run/systemd/system ]; then
systemctl daemon-reload
# Load upgraded Waydroid hooks without interrupting a running Android session.
if systemctl is-active --quiet waydroid-container.service \
  && [ "$(lxc-info -P /var/lib/waydroid/lxc -n waydroid -sH 2>/dev/null)" = STOPPED ]; then
  systemctl try-restart waydroid-container.service
fi
systemctl enable anvildroid-controller.service >/dev/null 2>&1 || true
systemctl restart anvildroid-controller.service
fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
exit 0
EOF
chmod 0755 "$PKG/DEBIAN/postinst"
# cp -a preserves source permissions, including group-write bits from a
# developer checkout. Installed controller/payload assets must be immutable
# to non-root users, regardless of the build host's umask.
chmod -R go-w "$PKG"
mkdir -p "$OUT"
dpkg-deb --build --root-owner-group "$PKG" "$OUT/anvildroid-controller_${VER}_amd64.deb" >/dev/null
rm -rf "$PKG"
dpkg-deb --info "$OUT/anvildroid-controller_${VER}_amd64.deb"
sha256sum "$OUT/anvildroid-controller_${VER}_amd64.deb"
