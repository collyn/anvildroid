#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p target/native
# Refresh the signed resize-safe Android overlay shipped with the desktop
# bridge before compiling the CSD-enabled native library.
sh scripts/build-native.sh
# Both launch paths must ship the same SSD/CSD implementation.
cp target/native/libanvildroid-window.so target/native/libanvildroid-runtime-window.so
clang --target=x86_64-linux-android33 -Danvildroid_stat=asta -Danvildroid_lstat=alsta \
  -Danvildroid_openat=aopnat -Danvildroid_open=aopn -Danvildroid_open_2=__aopn_2 \
  -ffreestanding -fPIC -fno-stack-protector -O2 -Wall -Wextra -Werror \
  -nostdlib -shared -fuse-ld=lld -Wl,-soname,libanvildroid-path-guard.so \
  native/compat/path-guard.c -o target/native/libanvildroid-path-guard.so
