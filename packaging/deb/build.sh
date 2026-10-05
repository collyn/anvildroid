#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
VER=${1:-$(python3 "$ROOT/scripts/release-version.py" --debian)}
case "$VER" in
  ''|*[!0-9A-Za-z.+~-]*) echo "Invalid package version: $VER" >&2; exit 2 ;;
esac
dpkg --validate-version "$VER"
OUT="$ROOT/target/releases"
PKG="$OUT/anvildroid-$VER"
rm -rf "$PKG" "$OUT/anvildroid-controller_${VER}_amd64.deb"
mkdir -p "$PKG/DEBIAN" "$PKG/usr/local/lib/anvildroid-controller/provision/vendor/lib64" \
  "$PKG/usr/local/lib/anvildroid-controller/provision/vendor/bin" "$PKG/usr/local/lib/anvildroid-controller/provision/system/etc/init" \
  "$PKG/usr/local/lib/anvildroid-controller/provision/system/etc" "$PKG/usr/local/lib/anvildroid-controller/provision"
mkdir -p "$PKG/usr/bin" "$PKG/usr/share/applications"
install -m 0755 "$ROOT/target/release/anvildroid-gui" "$PKG/usr/bin/anvildroid-gui"
for size in 32 48 64 128 256; do mkdir -p "$PKG/usr/share/icons/hicolor/${size}x${size}/apps"; install -m 0644 "$ROOT/crates/anvildroid-gui/icons/icon-${size}.png" "$PKG/usr/share/icons/hicolor/${size}x${size}/apps/org.anvildroid.gui.png"; done
install -m 0644 "$ROOT/packaging/appimage/org.anvildroid.gui.desktop" "$PKG/usr/share/applications/org.anvildroid.gui.desktop"
for f in runtime-controller.py runtime-existing.py runtime-host.py runtime-images.py runtime-android.py runtime-worker.py runtime-linux.py runtime-image-format.py runtime-network.py runtime-storage.py runtime-labels.py runtime-transfer.py runtime-catalog.py runtime-download.py runtime-extract.py runtime-provision.py runtime-arm.py runtime-arm-source.py runtime-gpu.py runtime-resources.py runtime-desktop.py runtime-workarea.py; do
  install -m 0644 "$ROOT/services/$f" "$PKG/usr/local/lib/anvildroid-controller/$f"
done
install -m 0755 "$ROOT/target/native/libanvildroid-runtime-window.so" "$PKG/usr/local/lib/anvildroid-controller/libanvildroid-window.so"
install -m 0644 "$ROOT/services/runtime-path-guard.py" "$PKG/usr/local/lib/anvildroid-controller/runtime-path-guard.py"
install -m 0644 "$ROOT/target/native/libanvildroid-path-guard.so" "$PKG/usr/local/lib/anvildroid-controller/libanvildroid-path-guard.so"
install -m 0644 "$ROOT/target/ime/AnvilDroidIme.apk" "$PKG/usr/local/lib/anvildroid-controller/AnvilDroidIme.apk"
install -m 0644 "$ROOT/target/shutdown/anvildroid-shutdown.jar" "$PKG/usr/local/lib/anvildroid-controller/anvildroid-shutdown.jar"
cp -a "$ROOT/native/overlay/." "$PKG/usr/local/lib/anvildroid-controller/provision/"
for f in anvildroid-apps.rc anvildroid-tasks.rc anvildroid-apps.sh anvildroid-tasks.sh; do install -m 0755 "$ROOT/native/$f" "$PKG/usr/local/lib/anvildroid-controller/provision/$f"; done
install -m 0644 "$ROOT/target/native/overlay-key.p12" "$PKG/usr/local/lib/anvildroid-controller/provision/overlay-key.p12"
install -D -m 0644 "$ROOT/target/native/init.waydroid.rc" "$PKG/usr/local/lib/anvildroid-controller/provision/system/etc/init/init.waydroid.rc"
install -D -m 0644 "$ROOT/target/native/AnvilDroidCaption.apk" "$PKG/usr/local/lib/anvildroid-controller/provision/vendor/overlay/AnvilDroidCaption/AnvilDroidCaption.apk"
install -m 0644 "$ROOT/target/native/framework-res.apk.sha256" "$PKG/usr/local/lib/anvildroid-controller/provision/framework-res.apk.sha256"
install -D -m 0644 "$ROOT/packaging/systemd/anvildroid-controller.service" "$PKG/lib/systemd/system/anvildroid-controller.service"
cat > "$PKG/DEBIAN/control" <<EOF
Package: anvildroid-controller
Version: $VER
Section: misc
Priority: optional
Architecture: amd64
Maintainer: AnvilDroid
Description: AnvilDroid Android runtime controller
 GPU accelerated Waydroid runtime controller with BinderFS and Wayland support.
Depends: python3, systemd, patchelf (>= 0.18), libwebkit2gtk-4.1-0, libgtk-3-0, libglib2.0-0, libjavascriptcoregtk-4.1-0, libsoup-3.0-0, libpango-1.0-0, libcairo2, libgdk-pixbuf-2.0-0
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
install -d -m 0700 /var/lib/anvildroid-controller /run/anvildroid
systemctl daemon-reload
systemctl enable anvildroid-controller.service >/dev/null 2>&1 || true
systemctl restart anvildroid-controller.service
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
exit 0
EOF
chmod 0755 "$PKG/DEBIAN/postinst"
mkdir -p "$OUT"
dpkg-deb --build --root-owner-group "$PKG" "$OUT/anvildroid-controller_${VER}_amd64.deb" >/dev/null
rm -rf "$PKG"
dpkg-deb --info "$OUT/anvildroid-controller_${VER}_amd64.deb"
sha256sum "$OUT/anvildroid-controller_${VER}_amd64.deb"
