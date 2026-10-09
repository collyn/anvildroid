#!/bin/sh
# Pass --live to additionally exercise the caller's Wayland compositor.
set -eu
cd "$(dirname "$0")/../.."
mkdir -p target/native
cc -std=c11 -Wall -Wextra -Werror native/bridge/android-window-test.c \
  native/ime/text-state.c -ldl -pthread -o target/native/test-android-window
target/native/test-android-window
if [ "${1:-}" = --live ]; then
  wayland-scanner private-code /usr/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml target/native/android-test-xdg.c
  wayland-scanner private-code /usr/share/wayland-protocols/stable/viewporter/viewporter.xml target/native/android-test-viewporter.c
  cc -std=c11 -Wall -Wextra -Werror native/bridge/android-window-live-test.c \
    target/native/android-test-xdg.c target/native/android-test-viewporter.c \
    native/ime/text-state.c -ldl -lwayland-client -pthread -o target/native/test-android-window-live
  timeout 20 target/native/test-android-window-live
fi
