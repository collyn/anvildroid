#!/usr/bin/python3
"""Idempotent first-run administrator bootstrap for the AppImage.

Installs or repairs the runtime tools, then starts the controller. Android
images are not installed here: users download or import them in Images.
"""
import os
import subprocess
import sys
from pathlib import Path


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
    # No-op when Waydroid, the host tools and its LXC templates are present.
    print('Checking Android runtime tools…', flush=True)
    code = subprocess.run(['/usr/bin/python3', str(setup), '--run-init']).returncode
    if code:
        print('Runtime tools setup failed; existing data was preserved.', file=sys.stderr)
        return code
    print('Installing and starting the AnvilDroid controller…', flush=True)
    return subprocess.run(['/usr/bin/python3', str(controller)]).returncode


if __name__ == '__main__':
    raise SystemExit(main())
