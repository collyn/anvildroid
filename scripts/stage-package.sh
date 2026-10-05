#!/bin/sh
# Stage files shared by Debian, RPM and Arch package builders.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
DEST=${1:?staging directory required}
rm -rf "$DEST"
mkdir -p "$DEST/usr/local/lib/anvildroid-controller/provision/vendor/lib64" \
  "$DEST/usr/local/lib/anvildroid-controller/provision/vendor/bin" \
  "$DEST/usr/local/lib/anvildroid-controller/provision/system/etc/init" \
  "$DEST/usr/local/lib/anvildroid-controller/provision/system/etc" \
  "$DEST/usr/local/lib/anvildroid-controller/provision" \
  "$DEST/usr/bin" "$DEST/usr/share/applications" \
  "$DEST/usr/share/licenses/anvildroid-controller" "$DEST/usr/lib/systemd/system"
install -m 0644 "$ROOT/LICENSE-MIT" "$DEST/usr/share/licenses/anvildroid-controller/LICENSE-MIT"
install -m 0755 "$ROOT/target/release/anvildroid-gui" "$DEST/usr/bin/anvildroid-gui"
for size in 32 48 64 128 256; do
  mkdir -p "$DEST/usr/share/icons/hicolor/${size}x${size}/apps"
  install -m 0644 "$ROOT/crates/anvildroid-gui/icons/icon-${size}.png" \
    "$DEST/usr/share/icons/hicolor/${size}x${size}/apps/org.anvildroid.gui.png"
done
install -m 0644 "$ROOT/packaging/appimage/org.anvildroid.gui.desktop" "$DEST/usr/share/applications/org.anvildroid.gui.desktop"
for f in runtime-controller.py runtime-existing.py runtime-host.py runtime-images.py runtime-android.py runtime-worker.py runtime-linux.py runtime-image-format.py runtime-network.py runtime-storage.py runtime-labels.py runtime-transfer.py runtime-catalog.py runtime-download.py runtime-extract.py runtime-provision.py runtime-arm.py runtime-arm-source.py runtime-gpu.py runtime-resources.py runtime-desktop.py runtime-workarea.py; do
  install -m 0644 "$ROOT/services/$f" "$DEST/usr/local/lib/anvildroid-controller/$f"
done
install -m 0755 "$ROOT/target/native/libanvildroid-runtime-window.so" "$DEST/usr/local/lib/anvildroid-controller/libanvildroid-window.so"
install -m 0644 "$ROOT/services/runtime-path-guard.py" "$DEST/usr/local/lib/anvildroid-controller/runtime-path-guard.py"
install -m 0644 "$ROOT/target/native/libanvildroid-path-guard.so" "$DEST/usr/local/lib/anvildroid-controller/libanvildroid-path-guard.so"
install -m 0644 "$ROOT/target/ime/AnvilDroidIme.apk" "$DEST/usr/local/lib/anvildroid-controller/AnvilDroidIme.apk"
install -m 0644 "$ROOT/target/shutdown/anvildroid-shutdown.jar" "$DEST/usr/local/lib/anvildroid-controller/anvildroid-shutdown.jar"
cp -a "$ROOT/native/overlay/." "$DEST/usr/local/lib/anvildroid-controller/provision/"
for f in anvildroid-apps.rc anvildroid-tasks.rc anvildroid-apps.sh anvildroid-tasks.sh; do
  install -m 0755 "$ROOT/native/$f" "$DEST/usr/local/lib/anvildroid-controller/provision/$f"
done
install -m 0644 "$ROOT/target/native/overlay-key.p12" "$DEST/usr/local/lib/anvildroid-controller/provision/overlay-key.p12"
install -m 0644 "$ROOT/packaging/systemd/anvildroid-controller.service" "$DEST/usr/lib/systemd/system/anvildroid-controller.service"
