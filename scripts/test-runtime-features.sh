#!/bin/sh
# Inert backend/frontend checks; no Android attachment, mounts or installation.
# Run sudo python3 services/shares-test.py --mount-test separately for real mounts.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
for suite in compatibility shares default-shares default-binder default-native-pool default-stop device components diagnostics features overlay portable-packaging; do
  python3 "services/$suite-test.py"
done
for suite in compatibility shares runtime-actions; do
  node "crates/anvildroid-gui/$suite-check.cjs"
done
node --test tests/test_*.cjs
python3 -m unittest discover -s tests -p 'test_runtime_keymap*.py'
python3 -m unittest discover -s tests -p 'test_runtime_arm*.py'
python3 tests/acceptance/test-controls-harness.py
python3 tests/acceptance/test-default-harness.py
native_checks=$(mktemp -d /tmp/anvil-feature-native.XXXXXX)
trap 'rm -rf "$native_checks"' EXIT HUP INT TERM
for suite in keymap-model-v3 keymap-capture keymap-editor-ui keymap-full-ui keymap-advanced keymap-storage scrcpy-mapping keymap-overlay keymap-hints boot-canvas; do
  cc -Wall -Wextra -Werror "tests/$suite.c" native/ime/text-state.c -ldl -pthread -o "$native_checks/$suite"
  "$native_checks/$suite"
  printf 'Native %s: PASS\n' "$suite"
done
cc -Wall -Wextra -Werror -rdynamic native/bridge/absolute-pointer-test.c native/ime/text-state.c -ldl -pthread -o "$native_checks/absolute-pointer"
"$native_checks/absolute-pointer"
printf 'Native absolute-pointer: PASS\n'
python3 - <<'PY'
import ast
from pathlib import Path
import tempfile

root = Path.cwd()
features = {'runtime-features.py', 'runtime-compatibility.py', 'runtime-shares.py', 'runtime-default-shares.py',
            'runtime-device.py', 'runtime-components.py', 'runtime-diagnostics.py'}
installer = ast.parse((root / 'scripts/install-runtime-controller.py').read_text())
modules = next(ast.literal_eval(node.value) for node in installer.body
               if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MODULES' for t in node.targets))
assert features <= set(modules), 'Installer omits runtime feature modules'
stage = (root / 'scripts/stage-package.sh').read_text()
verify = (root / 'scripts/verify-package.py').read_text()
appimage = (root / 'scripts/verify-appimage.py').read_text()
for name in features:
    assert name in stage and name in verify and 'services/' + name in appimage, name
# Mirror the explicit controller install list in an isolated temporary directory;
# import features with no checkout sys.path dependency and no service execution.
with tempfile.TemporaryDirectory(prefix='anvil-feature-stage-') as directory:
    destination = Path(directory)
    for name in modules:
        payload = (root / 'services' / name).read_bytes()
        compile(payload, name, 'exec')
        (destination / name).write_bytes(payload)
    import importlib.util
    spec = importlib.util.spec_from_file_location('staged_runtime_features', destination / 'runtime-features.py')
    staged = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(staged)
    assert set(staged.FIELDS) >= {'device_info', 'device_set', 'shares_get', 'components_get', 'diagnostics_collect', 'compatibility_get'}
print('Feature packaging lists, isolated complete module staging/import: PASS')
PY
