#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
mkdir -p target/native
for name in window-state maximize-recreation content-clip hidl-pool; do
  cc -Wall -Wextra -Werror "native/bridge/$name-test.c" \
    native/ime/text-state.c -ldl -pthread -o "target/native/test-$name"
  "target/native/test-$name"
done
echo 'Window state, maximize recreation, content clipping and HIDL pool: PASS'
