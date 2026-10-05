#!/bin/sh
# Build a relocatable AnvilDroid test bundle. This does not touch Waydroid.
set -eu
cd "$(dirname "$0")/.."
if [ "$#" != 1 ]; then
  echo 'Usage: sh scripts/package-portable.sh RELEASE_NAME' >&2
  exit 1
fi
case "$1" in ''|*[!a-zA-Z0-9._-]*|.|..) echo 'Invalid release name' >&2; exit 1;; esac
dest="$(pwd)/target/releases/$1"
[ ! -e "$dest" ] || { echo "Release already exists: $dest" >&2; exit 1; }
cargo build --release -p anvildroid-gui -p anvildroid-cli
# Refuse incomplete bundles before creating a staging directory. Cargo does
# not build the Java IME/shutdown helpers, and cached native outputs may be old.
gui_bin="${CARGO_TARGET_DIR:-target}/release/anvildroid-gui"
cli_bin="${CARGO_TARGET_DIR:-target}/release/anvildroid"
for f in "$gui_bin" "$cli_bin" target/native/libanvildroid-path-guard.so target/native/libanvildroid-runtime-window.so target/native/libanvildroid-window.so target/native/overlay-key.p12 target/ime/AnvilDroidIme.apk target/shutdown/anvildroid-shutdown.jar; do
  [ -s "$f" ] || { echo "Missing or empty release artifact: $f" >&2; exit 1; }
done
cmp -s target/native/libanvildroid-window.so target/native/libanvildroid-runtime-window.so || {
  echo 'Native bridge artifacts differ; run sh scripts/build-runtime-desktop.sh and retry.' >&2
  exit 1
}
stage="$(mktemp -d "$(pwd)/target/.portable.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/bin" "$stage/scripts" "$stage/services" "$stage/packaging/systemd" "$stage/target/native" "$stage/target/ime" "$stage/target/shutdown" "$stage/native"
cp "$gui_bin" "$cli_bin" "$stage/bin/"
cp scripts/install-portable.sh scripts/install-runtime-controller.py scripts/start-runtime-controller.py scripts/setup-waydroid.py "$stage/scripts/"
cp packaging/systemd/anvildroid-controller.service "$stage/packaging/systemd/"
cp services/*.py "$stage/services/"
cp native/anvildroid-*.rc native/anvildroid-*.sh "$stage/native/"
cp -a native/overlay "$stage/native/"
for f in target/native/libanvildroid-path-guard.so target/native/libanvildroid-runtime-window.so target/native/overlay-key.p12 target/ime/AnvilDroidIme.apk target/shutdown/anvildroid-shutdown.jar; do
  cp "$f" "$stage/$f"
done
cp target/native/libanvildroid-window.so "$stage/target/native/"
cp README.md "$stage/README.md"
printf '%s\n' 'AnvilDroid portable release' "version=$(python3 scripts/release-version.py)" "arch=$(uname -m)" "built=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$stage/RELEASE.txt"
(cd "$stage" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
mkdir -p "$(dirname "$dest")"
mv "$stage" "$dest"
trap - EXIT
echo "Packaged portable release: $dest"
echo "Install GUI: sh $dest/scripts/install-portable.sh $dest"
