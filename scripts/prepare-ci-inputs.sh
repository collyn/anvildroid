#!/bin/sh
# Fetch pinned build inputs without installing or starting an Android runtime.
set -eu
cd "$(dirname "$0")/.."
mkdir -p target/tools target/native
r8=target/tools/r8-8.3.37.jar
curl --fail --location --retry 3 --max-time 300 \
  https://dl.google.com/dl/android/maven2/com/android/tools/r8/8.3.37/r8-8.3.37.jar -o "$r8"
printf '%s  %s\n' 59753e70a74f918389cc87f1b7d66b5c0862932559167425708ded159e3de439 "$r8" | sha256sum -c -

work=$(mktemp -d target/ci-inputs.XXXXXX)
trap 'rm -rf "$work"' EXIT
curl --fail --location --retry 3 --max-time 1800 \
  'https://sourceforge.net/projects/waydroid/files/images/system/lineage/waydroid_x86_64/lineage-20.0-20260927-VANILLA-waydroid_x86_64-system.zip/download' \
  -o "$work/system.zip"
printf '%s  %s\n' 053552725bf4ae25e2d3f7e6f1947203294aa9d73dc17fe1f3abe646e5d3bd8a "$work/system.zip" | sha256sum -c -
unzip -p "$work/system.zip" system.img > "$work/system.img"
for relative in system/etc/init/init.waydroid.rc system/framework/framework-res.apk; do
  name=${relative##*/}
  # debugfs reads ext4 directly, so CI needs neither mounts nor Binder.
  debugfs -R "dump /$relative $work/$name" "$work/system.img"
  test -s "$work/$name" || { echo "Missing build input: $relative" >&2; exit 1; }
  cp "$work/$name" "target/native/$name"
done
