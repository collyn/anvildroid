#!/bin/sh
# Build the AppImage inside an Ubuntu 24.04 container.
#
# The host's glibc (2.43) is newer than Ubuntu 24.04's (2.39), and
# linuxdeploy bundles the GTK/WebKit stack from the build system, so an
# AppImage built on the host fails on 24.04 with "GLIBC_2.4x not found".
# Building inside ubuntu:24.04 keeps the bundled libraries at the glibc 2.39
# baseline, which runs on 24.04 and anything newer.
#
# Usage: sh scripts/package-appimage-2404.sh RELEASE_NAME
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root"
if [ "$#" -ne 1 ]; then
  echo 'Usage: sh scripts/package-appimage-2404.sh RELEASE_NAME' >&2
  exit 2
fi
case "$1" in
  ''|.|..|*[!a-zA-Z0-9._-]*) echo 'Invalid release name' >&2; exit 2 ;;
esac
name=$1

command -v docker >/dev/null 2>&1 || { echo 'docker is required' >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo 'docker daemon is not available' >&2; exit 1; }
host_linuxdeploy=${LINUXDEPLOY:-$(command -v linuxdeploy || true)}
[ -x "$host_linuxdeploy" ] || {
  echo 'linuxdeploy is required on the host (set LINUXDEPLOY to its path).' >&2
  exit 1
}
gtk_plugin=${LINUXDEPLOY_GTK_PLUGIN:-${XDG_CACHE_HOME:-$HOME/.cache}/tauri/linuxdeploy-plugin-gtk.sh}
[ -r "$gtk_plugin" ] || {
  echo 'Tauri GTK plugin missing; set LINUXDEPLOY_GTK_PLUGIN to linuxdeploy-plugin-gtk.sh.' >&2
  exit 1
}

image=anvildroid-pack:24.04
docker image inspect "$image" >/dev/null 2>&1 || \
  docker build -t "$image" -f packaging/appimage/Dockerfile.2404 packaging/appimage

cargo_home=${CARGO_HOME:-$HOME/.cargo}
rustup_home=${RUSTUP_HOME:-$HOME/.rustup}
[ -d "$cargo_home" ] || { echo "Missing CARGO_HOME: $cargo_home" >&2; exit 1; }
[ -d "$rustup_home" ] || { echo "Missing RUSTUP_HOME: $rustup_home" >&2; exit 1; }

sdk_mount=
if [ -d /usr/lib/android-sdk ]; then
  # build-touch-probe.sh expects the SDK at this exact host-style path.
  sdk_mount='-v /usr/lib/android-sdk:/usr/lib/android-sdk:ro'
fi
# aapt's RUNPATH points here for libaapt.so.0 etc. (Debian android-sdk package).
aapt_libs=
if [ -d /usr/lib/x86_64-linux-gnu/android ]; then
  aapt_libs='-v /usr/lib/x86_64-linux-gnu/android:/usr/lib/x86_64-linux-gnu/android:ro'
fi
# aapt also dlopens 7z.so from the 7zip package.
sevenzip_mount=
if [ -d /usr/lib/7zip ]; then
  sevenzip_mount='-v /usr/lib/7zip:/usr/lib/7zip:ro'
fi
# apksigner resolves apksigner.jar (and its apksig.jar dependency) here.
java_share=
if [ -e /usr/share/java/apksigner.jar ]; then
  java_share='-v /usr/share/java:/usr/share/java:ro'
fi

docker run --rm \
  -u "$(id -u):$(id -g)" \
  -e HOME="$HOME" \
  -e CARGO_HOME="$cargo_home" \
  -e RUSTUP_HOME="$rustup_home" \
  -e CARGO_TARGET_DIR=/work/target-2404 \
  -e APPIMAGE_EXTRACT_AND_RUN=1 \
  -e ANVILDROID_GLIBC_MAX=2.39 \
  -e PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$cargo_home/bin" \
  -e LINUXDEPLOY=/opt/tools/linuxdeploy \
  -e LINUXDEPLOY_GTK_PLUGIN=/opt/tools/linuxdeploy-plugin-gtk.sh \
  -e LD_LIBRARY_PATH=/usr/lib/7zip:/usr/lib/android-sdk/build-tools/debian \
  -v "$root:/work" \
  -v "$host_linuxdeploy:/opt/tools/linuxdeploy:ro" \
  -v "$gtk_plugin:/opt/tools/linuxdeploy-plugin-gtk.sh:ro" \
  -v "$cargo_home:$cargo_home" \
  -v "$rustup_home:$rustup_home" \
  $sdk_mount \
  $aapt_libs \
  $sevenzip_mount \
  $java_share \
  -w /work \
  "$image" \
  sh scripts/package-appimage.sh "$name"
