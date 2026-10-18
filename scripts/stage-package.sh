#!/bin/sh
# Stage files shared by Debian, RPM and Arch package builders.
set -eu
umask 022
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
DEST=${1:?staging directory required}
rm -rf "$DEST"
mkdir -p "$DEST/usr/local/lib/anvildroid-controller/provision/vendor/lib64" \
  "$DEST/usr/local/lib/anvildroid-controller/provision/vendor/bin" \
  "$DEST/usr/local/lib/anvildroid-controller/provision/system/etc/init" \
  "$DEST/usr/local/lib/anvildroid-controller/provision/system/etc" \
  "$DEST/usr/local/lib/anvildroid-controller/provision" \
  "$DEST/usr/bin" "$DEST/usr/share/applications" \
  "$DEST/usr/share/licenses/anvildroid-controller" "$DEST/usr/lib/systemd/system" \
  "$DEST/usr/local/lib/anvildroid-controller/scripts" "$DEST/usr/local/lib/anvildroid-controller/services"
install -m 0644 "$ROOT/LICENSE-MIT" "$DEST/usr/share/licenses/anvildroid-controller/LICENSE-MIT"
install -m 0755 "$ROOT/target/release/anvildroid" "$DEST/usr/bin/anvildroid"
install -m 0755 "$ROOT/target/release/anvildroid-gui" "$DEST/usr/bin/anvildroid-gui"
sh "$ROOT/scripts/stage-image-tools.sh" "$DEST/usr/local/lib/anvildroid-controller/tools"
for size in 32 48 64 128 256; do
  mkdir -p "$DEST/usr/share/icons/hicolor/${size}x${size}/apps"
  install -m 0644 "$ROOT/crates/anvildroid-gui/icons/icon-${size}.png" \
    "$DEST/usr/share/icons/hicolor/${size}x${size}/apps/org.anvildroid.gui.png"
done
install -m 0755 "$ROOT/scripts/setup-waydroid.py" "$DEST/usr/local/lib/anvildroid-controller/scripts/setup-waydroid.py"
install -m 0755 "$ROOT/scripts/patch-waydroid.py" "$DEST/usr/local/lib/anvildroid-controller/scripts/patch-waydroid.py"
install -m 0755 "$ROOT/scripts/install-nvidia-payload.py" "$DEST/usr/local/lib/anvildroid-controller/scripts/install-nvidia-payload.py"
install -m 0755 "$ROOT/scripts/prepare-nvidia-fork.py" "$DEST/usr/local/lib/anvildroid-controller/scripts/prepare-nvidia-fork.py"
install -m 0755 "$ROOT/scripts/install-runtime-apparmor.py" "$DEST/usr/local/lib/anvildroid-controller/scripts/install-runtime-apparmor.py"
python3 "$ROOT/scripts/build-nvidia-payload.py" --stage-parent "$DEST/usr/local/lib/anvildroid-controller"
python3 "$ROOT/scripts/build-x11-payload.py" --stage-parent "$DEST/usr/local/lib/anvildroid-controller"
mkdir -p "$DEST/usr/share/doc/anvildroid-controller"
python3 "$ROOT/native/input/build-scrcpy-diagnostic.py" --verify
install -m 0644 "$ROOT/target/input-diagnostic/scrcpy-build.json" "$DEST/usr/local/lib/anvildroid-controller/scrcpy-build.json"
install -m 0644 "$ROOT/target/input-diagnostic/scrcpy-LICENSE" "$DEST/usr/local/lib/anvildroid-controller/scrcpy-LICENSE"
install -m 0644 "$ROOT/target/input-diagnostic/scrcpy-LICENSE" "$DEST/usr/share/doc/anvildroid-controller/scrcpy-LICENSE"
install -m 0644 "$ROOT/native/menu-font-OFL.txt" "$DEST/usr/share/doc/anvildroid-controller/menu-font-OFL.txt"
for f in runtime-host.py runtime-arm.py runtime-arm-source.py runtime-extract.py runtime-download.py runtime-image-format.py runtime-existing.py runtime-keymap.py runtime-resources.py runtime-gpu.py runtime-nvidia.py runtime-linux.py runtime-storage.py; do install -m 0644 "$ROOT/services/$f" "$DEST/usr/local/lib/anvildroid-controller/services/$f"; done
install -m 0644 "$ROOT/packaging/appimage/org.anvildroid.gui.desktop" "$DEST/usr/share/applications/org.anvildroid.gui.desktop"
for f in runtime-system.py runtime-controller.py runtime-existing.py runtime-keymap.py runtime-host.py runtime-images.py runtime-android.py runtime-worker.py runtime-x11.py runtime-linux.py runtime-image-format.py runtime-adb.py runtime-network.py runtime-storage.py runtime-labels.py runtime-transfer.py runtime-catalog.py runtime-download.py runtime-extract.py runtime-provision.py runtime-arm.py runtime-arm-source.py runtime-gpu.py runtime-nvidia.py runtime-resources.py runtime-desktop.py runtime-workarea.py runtime-features.py runtime-compatibility.py runtime-shares.py runtime-default-shares.py runtime-device.py runtime-components.py runtime-diagnostics.py; do
  install -m 0644 "$ROOT/services/$f" "$DEST/usr/local/lib/anvildroid-controller/$f"
done
install -m 0755 "$ROOT/target/native/libanvildroid-runtime-window.so" "$DEST/usr/local/lib/anvildroid-controller/libanvildroid-window.so"
install -m 0644 "$ROOT/target/native/libanvildroid-hwc-shim.so" "$DEST/usr/local/lib/anvildroid-controller/libanvildroid-hwc-shim.so"
install -m 0644 "$ROOT/target/native/libanvildroid-android-window.so" "$DEST/usr/local/lib/anvildroid-controller/libanvildroid-android-window.so"
install -m 0644 "$ROOT/services/runtime-path-guard.py" "$DEST/usr/local/lib/anvildroid-controller/runtime-path-guard.py"
install -m 0644 "$ROOT/target/native/libanvildroid-path-guard.so" "$DEST/usr/local/lib/anvildroid-controller/libanvildroid-path-guard.so"
install -m 0644 "$ROOT/target/ime/AnvilDroidIme.apk" "$DEST/usr/local/lib/anvildroid-controller/AnvilDroidIme.apk"
install -m 0644 "$ROOT/target/shutdown/anvildroid-shutdown.jar" "$DEST/usr/local/lib/anvildroid-controller/anvildroid-shutdown.jar"
install -m 0644 "$ROOT/target/input-diagnostic/scrcpy-server-cancel.jar" "$DEST/usr/local/lib/anvildroid-controller/scrcpy-server-cancel.jar"
cp -a "$ROOT/native/overlay" "$DEST/usr/local/lib/anvildroid-controller/provision/"
for f in anvildroid-apps.rc anvildroid-tasks.rc anvildroid-apps.sh anvildroid-tasks.sh anvildroid-keymap.rc anvildroid-keymap.sh; do
  install -m 0755 "$ROOT/native/$f" "$DEST/usr/local/lib/anvildroid-controller/provision/$f"
done
install -m 0644 "$ROOT/target/native/overlay-key.p12" "$DEST/usr/local/lib/anvildroid-controller/provision/overlay-key.p12"
install -D -m 0644 "$ROOT/target/native/init.waydroid.rc" "$DEST/usr/local/lib/anvildroid-controller/provision/system/etc/init/init.waydroid.rc"
install -D -m 0644 "$ROOT/target/native/AnvilDroidCaption.apk" "$DEST/usr/local/lib/anvildroid-controller/provision/vendor/overlay/AnvilDroidCaption/AnvilDroidCaption.apk"
install -m 0644 "$ROOT/target/native/framework-res.apk.sha256" "$DEST/usr/local/lib/anvildroid-controller/provision/framework-res.apk.sha256"
install -m 0644 "$ROOT/packaging/systemd/anvildroid-controller.service" "$DEST/usr/lib/systemd/system/anvildroid-controller.service"
install -D -m 0644 "$ROOT/packaging/systemd/50-anvildroid.conf" "$DEST/usr/lib/systemd/system/waydroid-container.service.d/50-anvildroid.conf"
# Normalize copied assets as well as newly created directories: cp -a keeps
# group-write permissions from the source checkout.
install -d -m 0755 "$DEST/var" "$DEST/var/lib" "$DEST/run" "$DEST/run/anvildroid"
install -d -m 0700 "$DEST/var/lib/anvildroid-controller"
chmod -R go-w "$DEST"
