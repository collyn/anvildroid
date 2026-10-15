#!/bin/sh
# Private AOSP tools used to compile/sign overlays against downloaded images.
set -eu
umask 022
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
SDK_ROOT=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
# Respect an explicitly selected SDK; otherwise also look in Android Studio's
# standard locations rather than assuming the distro SDK has Google's tools.
if [ -z "$SDK_ROOT" ]; then
  SDK_ROOT=/usr/lib/android-sdk
  for candidate in /usr/lib/android-sdk "${HOME:-}/Android/Sdk" "${HOME:-}/android-sdk" /opt/android-sdk "$ROOT/target/tools/android-sdk"; do
    if [ -s "$candidate/build-tools/35.0.0/aapt" ]; then
      SDK_ROOT=$candidate
      break
    fi
  done
fi
TOOLS="$SDK_ROOT/build-tools/35.0.0"
DEST=${1:?private tools destination or --check required}
for file in aapt lib64/libc++.so lib/apksigner.jar NOTICE.txt source.properties; do
  if [ ! -s "$TOOLS/$file" ]; then
    echo "Missing image tool input: $TOOLS/$file" >&2
    echo 'Package builds need Google Android SDK build-tools 35.0.0; distro aapt/apksigner alone are insufficient.' >&2
    echo 'Install with sdkmanager --sdk_root="/path/to/android-sdk" "build-tools;35.0.0"' >&2
    echo 'Then run ANDROID_HOME="/path/to/android-sdk" ./scripts/build-deb.sh (or your package build command).' >&2
    exit 1
  fi
done
if [ "$DEST" = --check ]; then
  echo "Android image tools ready: $TOOLS"
  exit 0
fi
mkdir -p "$DEST/lib64" "$DEST/lib"
install -m 0755 "$TOOLS/aapt" "$DEST/aapt"
install -m 0644 "$TOOLS/lib64/libc++.so" "$DEST/lib64/libc++.so"
install -m 0644 "$TOOLS/lib/apksigner.jar" "$DEST/lib/apksigner.jar"
install -m 0644 "$TOOLS/NOTICE.txt" "$DEST/NOTICE.txt"
install -m 0644 "$TOOLS/source.properties" "$DEST/source.properties"
cat > "$DEST/apksigner" <<'EOF'
#!/bin/sh
set -eu
exec /usr/bin/java -jar "$(dirname "$0")/lib/apksigner.jar" "$@"
EOF
chmod 0755 "$DEST/apksigner"
