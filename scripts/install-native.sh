#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ "$#" != 1 ]; then
    echo 'Usage: sudo sh scripts/install-native.sh RELEASE_DIRECTORY' >&2
    echo 'Create one with: python3 scripts/native-release.py package target/releases/NAME' >&2
    exit 1
fi
exec python3 scripts/native-release.py install "$1"
