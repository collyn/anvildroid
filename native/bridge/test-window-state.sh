#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
mkdir -p target/native
cc -Wall -Wextra -Werror native/bridge/task-watch-test.c -o target/native/test-task-watch
target/native/test-task-watch
for name in nvidia-present clipboard-adapter absolute-pointer ime-adapter custom-size boot-canvas-capacity display-size window-state maximize-recreation content-clip hidl-pool csd-menu csd-resize csd-cursor keymap-font resize-mode window-fit window-profile-fit open-size-policy profile-identity window-transition media-layer dmabuf-metadata; do
  cc -Wall -Wextra -Werror -rdynamic "native/bridge/$name-test.c" \
    native/ime/text-state.c -ldl -pthread -o "target/native/test-$name"
  "target/native/test-$name"
done
python3 native/bridge/display-size-shell-test.py
python3 native/bridge/single-display-properties-test.py
echo 'Window state, maximize recreation, content clipping, HIDL pool, header menu and resize letterboxing/profiles: PASS'
