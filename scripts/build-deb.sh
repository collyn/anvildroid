#!/bin/sh
# Build every payload consumed by packaging/deb/build.sh, then create the package.
set -eu

usage() {
    echo "Usage: $0 [VERSION]"
    echo "Default version: workspace.package.version in Cargo.toml."
    echo "Output: target/releases/anvildroid-controller_VERSION_amd64.deb"
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac
if [ "$#" -gt 1 ]; then usage >&2; exit 2; fi

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
VERSION=${1:-$(python3 scripts/release-version.py --debian)}
case "$VERSION" in
    ''|*[!0-9A-Za-z.+~-]*) echo "Invalid version: $VERSION" >&2; exit 2 ;;
esac

stage=prerequisites
trap 'status=$?; if [ "$status" -ne 0 ]; then echo "Build failed during: $stage" >&2; fi' 0
for tool in cargo rustc python3 clang ld.lld javac java jar keytool aapt apksigner dpkg dpkg-deb sha256sum flock; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "Missing build tool: $tool" >&2
        exit 1
    }
done
dpkg --validate-version "$VERSION"
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
    echo "This package build requires an x86_64 Linux host." >&2
    exit 1
fi
for file in /usr/lib/android-sdk/platforms/android-23/android.jar target/tools/r8-8.3.37.jar; do
    if [ ! -r "$file" ]; then
        echo "Missing build input: $file (see README.md)" >&2
        exit 1
    fi
done
for relative in system/etc/init/init.waydroid.rc system/framework/framework-res.apk; do
    if [ ! -r "/var/lib/waydroid/rootfs/$relative" ] && [ ! -r "target/native/${relative##*/}" ]; then
        echo "Missing Waydroid build input: $relative; provide target/native/${relative##*/} first." >&2
        exit 1
    fi
done

mkdir -p target
exec 9>target/build-deb.lock
flock -n 9 || { echo "Another .deb build is running." >&2; exit 1; }

stage='shared runtime payload'
echo "[1/2] Building $stage"
sh scripts/build-runtime.sh

stage='Debian package'
echo "[2/2] Packaging version $VERSION"
sh packaging/deb/build.sh "$VERSION"
printf '\nBuild complete: %s/target/releases/anvildroid-controller_%s_amd64.deb\n' "$ROOT" "$VERSION"
