#!/usr/bin/python3
"""Idempotent first-run administrator bootstrap for the AppImage."""
import os
import subprocess
import sys
from pathlib import Path


def support_complete():
    return all(Path(path).exists() for path in (
        '/var/lib/waydroid/images/system.img',
        '/var/lib/waydroid/images/vendor.img',
        '/var/lib/waydroid/waydroid.prop',
        '/var/lib/waydroid/lxc/waydroid/config',
        '/var/lib/waydroid/lxc/waydroid/waydroid.seccomp',
        '/var/lib/waydroid/overlay'))


def main():
    if os.geteuid() != 0:
        print('Bootstrap must run through pkexec as administrator.', file=sys.stderr)
        return 126
    root = Path(__file__).resolve().parent.parent
    setup = root / 'scripts' / 'setup-waydroid.py'
    controller = root / 'scripts' / 'start-runtime-controller.py'
    if not setup.is_file() or not controller.is_file():
        print('The AppImage is missing its bootstrap payload.', file=sys.stderr)
        return 2
    flavor = os.environ.get('ANVILDROID_FLAVOR', 'VANILLA')
    if flavor not in ('VANILLA', 'GAPPS'):
        print('ANVILDROID_FLAVOR must be VANILLA or GAPPS.', file=sys.stderr)
        return 2
    if not support_complete() or not Path('/usr/bin/waydroid').exists():
        print('Setting up Waydroid and verified Android images…', flush=True)
        code = subprocess.run(['/usr/bin/python3', str(setup), '--run-init', '--flavor', flavor]).returncode
        if code:
            print('Waydroid setup failed; existing data was preserved.', file=sys.stderr)
            return code
    print('Installing and starting the AnvilDroid controller…', flush=True)
    return subprocess.run(['/usr/bin/python3', str(controller)]).returncode


if __name__ == '__main__':
    raise SystemExit(main())
