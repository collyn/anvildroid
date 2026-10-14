#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
mkdir -p target/native
for name in window-state maximize-recreation content-clip hidl-pool csd-menu csd-resize csd-cursor keymap-font resize-mode window-fit window-profile-fit open-size-policy profile-identity window-transition media-layer; do
  cc -Wall -Wextra -Werror -rdynamic "native/bridge/$name-test.c" \
    native/ime/text-state.c -ldl -pthread -o "target/native/test-$name"
  "target/native/test-$name"
done
echo 'Window state, maximize recreation, content clipping, HIDL pool, header menu and resize letterboxing/profiles: PASS'
