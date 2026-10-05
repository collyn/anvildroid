#!/bin/sh
# Build shared GUI and Android payloads consumed by package builders.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
for tool in cargo clang ld.lld javac java jar keytool aapt apksigner; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Missing build tool: $tool" >&2; exit 1; }
done
for file in /usr/lib/android-sdk/platforms/android-23/android.jar target/tools/r8-8.3.37.jar; do
  test -r "$file" || { echo "Missing build input: $file" >&2; exit 1; }
done
mkdir -p target/native
if [ ! -r target/native/init.waydroid.rc ] && [ -r /var/lib/waydroid/rootfs/system/etc/init/init.waydroid.rc ]; then
  cp /var/lib/waydroid/rootfs/system/etc/init/init.waydroid.rc target/native/init.waydroid.rc
fi
if [ ! -r target/native/framework-res.apk ] && [ -r /var/lib/waydroid/rootfs/system/framework/framework-res.apk ]; then
  cp /var/lib/waydroid/rootfs/system/framework/framework-res.apk target/native/framework-res.apk
fi
for file in target/native/init.waydroid.rc target/native/framework-res.apk; do
  test -r "$file" || { echo "Missing build input: $file" >&2; exit 1; }
done
sha256sum target/native/framework-res.apk | awk '{print $1}' > target/native/framework-res.apk.sha256
unset CARGO_BUILD_TARGET
cargo build --release --locked -p anvildroid-gui --target-dir target
sh scripts/build-runtime-desktop.sh
sh scripts/build-ime.sh
sh scripts/build-shutdown.sh
