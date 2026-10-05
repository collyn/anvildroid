#!/bin/sh
# Package already-built artifacts. Does not restart or modify the runtime.
set -eu
cd "$(dirname "$0")/.."
if [ "$#" != 1 ]; then
    echo 'Usage: sh scripts/package-release.sh RELEASE_NAME' >&2
    exit 1
fi
case "$1" in ''|*[!a-zA-Z0-9._-]*|.|..) echo 'Invalid release name' >&2; exit 1 ;; esac
dest="target/releases/$1"
if [ -e "$dest" ]; then echo 'Release already exists' >&2; exit 1; fi
mkdir -p target/releases
stage=$(mktemp -d target/releases/.bundle.XXXXXX)
python3 scripts/native-release.py package "$stage/native"
python3 scripts/desktop-setup.py package "$stage/desktop"
printf '%s\n' 'AnvilDroid local release format 1' \
    'native/: stopped-container overlay install' \
    'desktop/: running-session APK and KWin setup' > "$stage/RELEASE.txt"
mv -T "$stage" "$dest"
echo "Packaged native + desktop components: $dest"
