#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
mkdir -p target/native
for name in window-state maximize-recreation; do
  cc -Wall -Wextra -Werror "native/bridge/$name-test.c" \
    native/ime/text-state.c -ldl -pthread -o "target/native/test-$name"
  "target/native/test-$name"
done
echo 'Window state persistence, double-click and maximize recreation: PASS'
