#!/bin/sh
# Build a GUI AppImage and the matching portable controller/CLI test bundle.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root"
if [ "$#" -ne 1 ]; then
  echo 'Usage: sh scripts/package-appimage.sh RELEASE_NAME' >&2
  exit 2
fi
case "$1" in
  ''|.|..|*[!a-zA-Z0-9._-]*) echo 'Invalid release name' >&2; exit 2 ;;
esac
name=$1
arch=$(uname -m)
if [ "$arch" != x86_64 ]; then
  echo 'This test bundle contains an x86_64 Android bridge; build on x86_64.' >&2
  exit 1
fi
dest="$root/target/releases/$name"
if [ -e "$dest" ]; then
  echo "Release already exists: $dest" >&2
  exit 1
fi

linuxdeploy=${LINUXDEPLOY:-$(command -v linuxdeploy || true)}
if [ -z "$linuxdeploy" ] || [ ! -x "$linuxdeploy" ]; then
  echo 'linuxdeploy is required (set LINUXDEPLOY to its executable path).' >&2
  exit 1
fi
cache=${XDG_CACHE_HOME:-$HOME/.cache}
gtk_plugin=${LINUXDEPLOY_GTK_PLUGIN:-$cache/tauri/linuxdeploy-plugin-gtk.sh}
if [ ! -r "$gtk_plugin" ]; then
  echo 'Tauri GTK plugin missing; set LINUXDEPLOY_GTK_PLUGIN to linuxdeploy-plugin-gtk.sh.' >&2
  exit 1
fi
for tool in cargo file pkg-config sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Required tool missing: $tool" >&2; exit 1; }
done
pkg-config --exists gtk+-3.0 webkit2gtk-4.1 librsvg-2.0 || {
  echo 'GTK3, WebKitGTK 4.1 and librsvg development files are required to bundle the GUI.' >&2
  exit 1
}
webkit_libdir=$(pkg-config --variable=libdir webkit2gtk-4.1)
case "$webkit_libdir" in
  /usr/lib/*) ;;
  *) echo "Unsupported WebKitGTK library location: $webkit_libdir" >&2; exit 1 ;;
esac
webkit_helpers="$webkit_libdir/webkit2gtk-4.1"
for helper in WebKitWebProcess WebKitNetworkProcess WebKitGPUProcess injected-bundle/libwebkit2gtkinjectedbundle.so; do
  [ -s "$webkit_helpers/$helper" ] || { echo "Missing WebKit helper: $webkit_helpers/$helper" >&2; exit 1; }
done

cargo build --release -p anvildroid-gui -p anvildroid-cli
# Java payloads are not Cargo outputs. Rebuild them rather than shipping stale
# helpers from whichever developer run happened to populate target/.
sh scripts/build-ime.sh
sh scripts/build-shutdown.sh
target_dir=${CARGO_TARGET_DIR:-"$root/target"}
gui="$target_dir/release/anvildroid-gui"
cli="$target_dir/release/anvildroid"
[ -x "$gui" ] || { echo "Missing GUI binary: $gui" >&2; exit 1; }
for artifact in "$cli" target/native/libanvildroid-path-guard.so target/native/libanvildroid-runtime-window.so target/native/libanvildroid-window.so target/native/overlay-key.p12 target/ime/AnvilDroidIme.apk target/shutdown/anvildroid-shutdown.jar; do
  [ -s "$artifact" ] || { echo "Missing portable artifact: $artifact" >&2; exit 1; }
done
cmp -s target/native/libanvildroid-window.so target/native/libanvildroid-runtime-window.so || {
  echo 'Native bridge artifacts differ; rebuild runtime desktop first.' >&2
  exit 1
}
python3 -m py_compile scripts/bootstrap-appimage.py scripts/start-runtime-controller.py scripts/setup-waydroid.py scripts/install-runtime-controller.py || {
  echo 'Controller launcher scripts failed to compile' >&2
  exit 1
}
stage=$(mktemp -d "$root/target/.appimage.XXXXXX")
trap 'rm -r -- "$stage"' EXIT HUP INT TERM
mkdir -p "$stage/tools"
cp "$gtk_plugin" "$stage/tools/linuxdeploy-plugin-gtk.sh"
chmod 755 "$stage/tools/linuxdeploy-plugin-gtk.sh"
cp "$root/crates/anvildroid-gui/icons/icon.png" "$stage/tools/org.anvildroid.gui.png"
cp "$root/packaging/appimage/AppRun" "$stage/tools/AppRun"
chmod 755 "$stage/tools/AppRun"
appdir="$stage/AnvilDroid.AppDir"
image_name="AnvilDroid-$name-$arch.AppImage"
# WebKitGTK loads these processes and injected bundle by path at runtime.
relative_helpers=${webkit_helpers#/usr/}
mkdir -p "$appdir/usr/$relative_helpers"
cp -a "$webkit_helpers/." "$appdir/usr/$relative_helpers/"
# WebKit initializes these elements even for a GUI without video playback.
# Keep plugin discovery inside the bundle to avoid mixing host library ABIs.
mkdir -p "$appdir/usr/lib/gstreamer-1.0" "$appdir/usr/libexec"
for plugin in libgstapp.so libgstcoreelements.so; do
  cp "$webkit_libdir/gstreamer-1.0/$plugin" "$appdir/usr/lib/gstreamer-1.0/"
done
cp "$webkit_libdir/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner" "$appdir/usr/libexec/"
# The GTK plugin can pull in 32-bit libraries when the host has lib32 packages;
# pin the architecture so appimagetool bundles only the intended ABI.
# linuxdeploy's generic blacklist assumes a full desktop installation. These
# text/font/runtime libraries belong to our GTK stack, not the host GPU driver.
set --
for library in libfribidi.so.0 libfreetype.so.6 libfontconfig.so.1 libexpat.so.1 libharfbuzz.so.0 libz.so.1 libgpg-error.so.0 libstdc++.so.6 libgmp.so.10 libcom_err.so.2; do
  path="$webkit_libdir/$library"
  [ -s "$path" ] || { echo "Missing GUI dependency: $path" >&2; exit 1; }
  set -- "$@" --library "$path"
done
PATH="$stage/tools:$PATH" ARCH="$arch" "$linuxdeploy" \
  --appdir "$appdir" \
  "$@" \
  --executable "$gui" \
  --executable "$cli" \
  --deploy-deps-only "$appdir/usr/libexec/gst-plugin-scanner" \
  --desktop-file "$root/packaging/appimage/org.anvildroid.gui.desktop" \
  --icon-file "$stage/tools/org.anvildroid.gui.png" \
  --plugin gtk

# linuxdeploy regenerates its own AppRun wrapper on every run (it sources
# apprun-hooks/*.sh and execs AppRun.wrapped) and ignores --custom-apprun.
# Make the wrapped target our WebKit-aware launcher instead of a symlink.
rm -f "$appdir/AppRun.wrapped"
cp "$stage/tools/AppRun" "$appdir/AppRun.wrapped"
chmod 755 "$appdir/AppRun.wrapped"

# Bundle the controller payload at the AppDir root, mirroring the portable
# release layout so the pkexec helper's ROOT derivation works from the mount.
mkdir -p "$appdir/scripts" "$appdir/services" "$appdir/target/native" \
  "$appdir/target/ime" "$appdir/target/shutdown" "$appdir/native" \
  "$appdir/packaging/systemd"
cp scripts/bootstrap-appimage.py scripts/install-runtime-controller.py scripts/start-runtime-controller.py scripts/setup-waydroid.py "$appdir/scripts/"
cp services/*.py "$appdir/services/"
cp native/anvildroid-*.rc native/anvildroid-*.sh "$appdir/native/"
cp -a native/overlay "$appdir/native/"
cp target/native/libanvildroid-path-guard.so target/native/libanvildroid-runtime-window.so target/native/libanvildroid-window.so \
  target/native/overlay-key.p12 "$appdir/target/native/"
cp target/ime/AnvilDroidIme.apk "$appdir/target/ime/"
cp target/shutdown/anvildroid-shutdown.jar "$appdir/target/shutdown/"
cp packaging/systemd/anvildroid-controller.service "$appdir/packaging/systemd/"

# The WebKit library contains two absolute helper paths. Replace each prefix
# with an equally long path relative to AppRun's working directory (usr/).
webkit_lib="$appdir/usr/lib/libwebkit2gtk-4.1.so.0"
[ -s "$webkit_lib" ] || { echo 'WebKitGTK library was not bundled' >&2; exit 1; }
old_path="$webkit_helpers"
new_path="././/${relative_helpers}"
if [ "${#old_path}" -ne "${#new_path}" ]; then
  echo 'Cannot safely rewrite WebKitGTK helper paths on this build host' >&2
  exit 1
fi
OLD_WEBKIT_PATH="$old_path" NEW_WEBKIT_PATH="$new_path" \
  perl -0pi -e 'BEGIN { $a=$ENV{OLD_WEBKIT_PATH}; $b=$ENV{NEW_WEBKIT_PATH}; die "length mismatch" unless length($a)==length($b) } s/\Q$a\E/$b/g' "$webkit_lib"
if strings "$webkit_lib" | grep -F "$old_path" >/dev/null; then
  echo 'WebKitGTK still references host-only helper paths' >&2
  exit 1
fi
LDAI_OUTPUT="$stage/$image_name" PATH="$stage/tools:$PATH" ARCH="$arch" "$linuxdeploy" \
  --appdir "$appdir" --output appimage
[ -s "$stage/$image_name" ] || { echo 'AppImage output is missing or empty' >&2; exit 1; }
chmod 755 "$stage/$image_name"

mkdir -p "$stage/inspect"
(cd "$stage/inspect" && "$stage/$image_name" --appimage-extract >/dev/null)
cmp -s "$stage/tools/AppRun" "$stage/inspect/squashfs-root/AppRun.wrapped" || {
  echo 'AppImage did not retain its WebKit-aware launcher' >&2; exit 1;
}
grep -q 'linuxdeploy-plugin-gtk.sh' "$stage/inspect/squashfs-root/AppRun" || {
  echo 'AppImage AppRun does not source the GTK hook' >&2; exit 1;
}
if strings "$stage/inspect/squashfs-root/usr/lib/libwebkit2gtk-4.1.so.0" | grep -F "$old_path" >/dev/null; then
  echo 'AppImage still contains host-only WebKit helper paths' >&2; exit 1
fi
for helper in WebKitWebProcess WebKitNetworkProcess WebKitGPUProcess injected-bundle/libwebkit2gtkinjectedbundle.so; do
  [ -s "$stage/inspect/squashfs-root/usr/$relative_helpers/$helper" ] || {
    echo "AppImage is missing WebKit helper: $helper" >&2
    exit 1
  }
done
squash="$stage/inspect/squashfs-root"
for bundled in scripts/bootstrap-appimage.py scripts/start-runtime-controller.py scripts/setup-waydroid.py scripts/install-runtime-controller.py \
    target/native/libanvildroid-runtime-window.so target/native/overlay-key.p12 \
    target/ime/AnvilDroidIme.apk target/shutdown/anvildroid-shutdown.jar \
    native/anvildroid-apps.sh native/overlay/AndroidManifest.xml \
    packaging/systemd/anvildroid-controller.service; do
  [ -s "$squash/$bundled" ] || {
    echo "AppImage is missing controller payload: $bundled" >&2
    exit 1
  }
done
for module in services/*.py; do
  [ -s "$squash/${module}" ] || {
    echo "AppImage is missing controller module: $module" >&2
    exit 1
  }
done

if [ -n "${ANVILDROID_GLIBC_MAX:-}" ]; then
  python3 scripts/verify-appimage.py "$squash" --glibc-max "$ANVILDROID_GLIBC_MAX"
else
  python3 scripts/verify-appimage.py "$squash"
fi

# Portable files are an optional alternative. The AppImage itself contains the
# complete GUI/CLI/controller payload and can be copied on its own.
sh scripts/package-portable.sh "$name"
mv "$stage/$image_name" "$dest/$image_name"
(cd "$dest" && sha256sum "$image_name" >> SHA256SUMS)
echo "AppImage: $dest/$image_name"
echo "Copy just $dest/$image_name to another supported x86_64 Linux machine."
echo "Launch GUI: $dest/$image_name"
echo "First run: use the built-in Runtime setup (Internet and administrator access required)."
