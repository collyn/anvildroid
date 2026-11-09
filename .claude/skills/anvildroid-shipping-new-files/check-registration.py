#!/usr/bin/env python3
"""Check that controller modules are registered everywhere they must ship,
and that new source/test files are not silently swallowed by .gitignore.

Run from the repository root: python3 .claude/skills/anvildroid-shipping-new-files/check-registration.py
Exit code 0 = OK, 1 = problems found.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
problems = []


def tuple_assign(path, name):
    tree = ast.parse((ROOT / path).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise SystemExit(f'{name} not found in {path}')


def stage_loops():
    """Return the module sets of the `for f in ... .py ...; do` loops in stage-package.sh."""
    text = (ROOT / 'scripts/stage-package.sh').read_text()
    loops = []
    for match in re.finditer(r'^for f in ([^;]+);', text, re.M):
        names = match.group(1).split()
        if names and all(n.endswith('.py') for n in names):
            loops.append(set(names))
    return loops


modules = tuple_assign('scripts/install-runtime-controller.py', 'MODULES')
setup_modules = tuple_assign('scripts/setup-waydroid.py', 'SETUP_MODULES')

# Every shipped service module on disk must be in the installer MODULES.
on_disk = {p.name for p in (ROOT / 'services').glob('*.py')
           if not p.name.endswith('-test.py') and (p.name.startswith('runtime-') or p.name == 'notification-relay.py')}
for name in sorted(on_disk - modules):
    problems.append(f'services/{name} is not in MODULES (scripts/install-runtime-controller.py)')
for name in sorted(modules - on_disk):
    problems.append(f'MODULES lists {name} but services/{name} does not exist')

loops = stage_loops()
full_loop = max(loops, key=len) if loops else set()
stage_text = (ROOT / 'scripts/stage-package.sh').read_text()
for name in sorted(modules - full_loop):
    if f'$ROOT/services/{name}"' in stage_text:
        continue  # staged by its own explicit install line
    problems.append(f'{name} is in MODULES but missing from the main module loop in scripts/stage-package.sh (deb/rpm/pkg)')
for name in sorted(full_loop - modules):
    problems.append(f'scripts/stage-package.sh stages {name} but MODULES does not list it')
setup_loop = min(loops, key=len) if len(loops) > 1 else set()
for name in sorted(setup_modules - setup_loop):
    problems.append(f'SETUP_MODULES entry {name} missing from the services/ setup loop in scripts/stage-package.sh')

# Feature modules (listed in test-runtime-features.sh) must also be in both verifiers.
features_src = (ROOT / 'scripts/test-runtime-features.sh').read_text()
match = re.search(r'features = (\{[^}]+\})', features_src)
features = ast.literal_eval(match.group(1)) if match else set()
verify_pkg = (ROOT / 'scripts/verify-package.py').read_text()
verify_app = (ROOT / 'scripts/verify-appimage.py').read_text()
for name in sorted(features):
    if f"'{name}'" not in verify_pkg:
        problems.append(f'feature module {name} missing from scripts/verify-package.py required list')
    if f"'services/{name}'" not in verify_app:
        problems.append(f'feature module {name} missing from scripts/verify-appimage.py REQUIRED')

# Source-like files that exist but are ignored by .gitignore (would never be committed).
SOURCE = re.compile(r'\.(py|cjs|js|c|h|inc|cpp|rs|sh|rc|java|xml|json|toml|md)$')
ignored = subprocess.run(
    ['git', 'ls-files', '--others', '--ignored', '--exclude-standard', '--',
     'services', 'tests', 'scripts', 'native', 'crates', 'packaging'],
    cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
skip = ('/target', '__pycache__', 'crates/anvildroid-gui/gen/', 'native/arm-probe/', 'native/csd/')
for path in ignored:
    if SOURCE.search(path) and not any(s in path for s in skip):
        problems.append(f'{path} exists but is gitignored; add a `!/{path}` exception to .gitignore if it should be committed')

if problems:
    print('Registration problems:')
    for p in problems:
        print(f'  - {p}')
    sys.exit(1)
print(f'OK: {len(modules)} modules registered consistently; no ignored source files.')
