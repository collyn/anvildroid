#!/bin/sh
# Build the shared payload, then create the Arch package.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
VERSION=${1:-$(python3 "$ROOT/scripts/release-version.py")}
case "$VERSION" in ''|*[!0-9A-Za-z.+_-]*) echo "Invalid version: $VERSION" >&2; exit 2;; esac
command -v zstd >/dev/null || { echo 'Missing build tool: zstd' >&2; exit 1; }
sh "$ROOT/scripts/build-runtime.sh"
sh "$ROOT/packaging/pkg/build.sh" "$VERSION"
