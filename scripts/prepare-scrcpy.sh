#!/bin/sh
# Build the matching control server from an immutable upstream source revision.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
SDK_ROOT=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-/usr/lib/android-sdk}}
SOURCE="$ROOT/target/tools/scrcpy-source"
REVISION=f01231dff8294fe2c99045a4f9a14b233a71bb86
for file in "$SDK_ROOT/platforms/android-35/android.jar" "$SDK_ROOT/platforms/android-35/framework.aidl" "$SDK_ROOT/build-tools/35.0.0/d8" "$SDK_ROOT/build-tools/35.0.0/aidl"; do
  test -r "$file" || { echo "Missing scrcpy build input: $file (install Android API 35 and build-tools 35.0.0)" >&2; exit 1; }
done
if [ ! -d "$SOURCE/.git" ]; then
  mkdir -p "$SOURCE"
  git -C "$SOURCE" init -q
  git -C "$SOURCE" remote add origin https://github.com/Genymobile/scrcpy.git
fi
if ! git -C "$SOURCE" cat-file -e "$REVISION^{commit}" 2>/dev/null; then
  git -C "$SOURCE" fetch --depth=1 origin "$REVISION"
fi
git -C "$SOURCE" checkout --detach "$REVISION"
python3 "$ROOT/native/input/build-scrcpy-diagnostic.py" "$SOURCE" \
  "$SDK_ROOT/platforms/android-35" "$SDK_ROOT/build-tools/35.0.0"
