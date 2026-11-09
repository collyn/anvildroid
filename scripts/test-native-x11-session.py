#!/usr/bin/env python3
"""Exercise two app windows on an explicitly selected native X11 test runtime.

Requires a running desktop runtime and xdotool/wmctrl/ImageMagick. With --start,
starts the selected stopped runtime and checks that boot leaves no app windows.
Only Settings, Calculator and that runtime's Full UI are launched/closed. Never
starts Weston, changes the display mode or controls unrelated windows.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('controller_client', ROOT / 'scripts/runtime-controller-client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def run(*args, acceptable=(0,)):
    result = subprocess.run(args, text=True, capture_output=True, timeout=10)
    if result.returncode not in acceptable:
        raise RuntimeError(' '.join(args) + ': ' + result.stderr[-1000:])
    return result.stdout


def wait_for(label, check, timeout=20):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise RuntimeError('Timed out: ' + label)
        time.sleep(.25)


def main(args):
    assert re.fullmatch(r'r-[0-9a-f]{32}', args.runtime), 'Invalid runtime ID'
    assert os.environ.get('XDG_SESSION_TYPE') == 'x11', 'An X11 session is required'
    prefix = 'waydroid.anvildroid.' + args.runtime + '.'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    def request(op, **fields):
        response = client.request(Path('/run/anvildroid/control.sock'), dict(op=op, id=args.runtime, **fields))
        if not response['ok']:
            raise RuntimeError(str(response['error']))
        return response['result']

    def windows(package=None):
        expression = '^' + re.escape(prefix + (package or '')) + ('$' if package else '')
        return run('xdotool', 'search', '--onlyvisible', '--classname', expression,
                   acceptable=(0, 1)).split()

    def action(name, package):
        request('app_action', action=name, package=package)
        def complete():
            job = request('app_job')
            if job['status'] == 'Failed':
                raise RuntimeError(str(job))
            return job['status'] == 'Succeeded'
        wait_for(name + ' ' + package, complete, 45)

    def alive():
        assert request('inspect')['state'] == 'Running', 'An app action stopped its runtime'

    def capture(window, label):
        time.sleep(1)
        run('import', '-window', window, str(output / (label + '.png')))

    before = request('inspect')
    if args.start:
        assert before['state'] in ('Stopped', 'Prepared', 'Error'), 'Use --start only with a stopped runtime'
        request('start', display='x11:' + os.environ['DISPLAY'])
        wait_for('Android boot', lambda: request('inspect')['state'] == 'Running', 180)
        time.sleep(2)
        assert not windows(), 'Start opened app windows before any launch action'
        print('PASS Start completes without opening an app window', flush=True)
    alive()
    app_result = request('apps')
    inventory = app_result['apps']
    packages = {app['package'] for app in inventory}
    assert {'com.android.settings', 'com.android.calculator2'} <= packages
    assert sum(bool(app.get('icon_data')) for app in inventory) > 1
    print(f'PASS real launcher inventory: {len(inventory)} apps with icons', flush=True)
    action('launch', 'com.android.settings')
    settings = wait_for('Settings window', lambda: windows('com.android.settings'))[0]
    action('launch', 'com.android.calculator2')
    calculator = wait_for('Calculator window', lambda: windows('com.android.calculator2'))[0]
    assert calculator != settings
    capture(settings, 'settings')
    capture(calculator, 'calculator')
    run('wmctrl', '-i', '-r', settings, '-b', 'add,maximized_vert,maximized_horz')
    wait_for('maximized state', lambda: '_NET_WM_STATE_MAXIMIZED_HORZ' in
             run('xprop', '-id', settings, '_NET_WM_STATE'))
    capture(settings, 'settings-maximized')
    run('wmctrl', '-i', '-r', settings, '-b', 'remove,maximized_vert,maximized_horz')
    wait_for('restored state', lambda: '_NET_WM_STATE_MAXIMIZED_HORZ' not in
             run('xprop', '-id', settings, '_NET_WM_STATE'))
    run('wmctrl', '-i', '-r', settings, '-e', '0,160,100,642,738')
    capture(settings, 'settings-restored')
    run('wmctrl', '-i', '-a', settings)
    run('xdotool', 'mousemove', '--window', settings, '18', '16', 'click', '1')
    capture(settings, 'settings-menu')
    run('xdotool', 'mousemove', '--window', settings, '18', '16', 'click', '1')
    run('wmctrl', '-i', '-c', calculator)
    wait_for('Calculator close', lambda: not windows('com.android.calculator2'))
    assert windows('com.android.settings'), 'Closing Calculator also closed Settings'
    alive()
    action('launch', 'com.android.calculator2')
    assert wait_for('Calculator reopen', lambda: windows('com.android.calculator2'))
    print('PASS independent native app identities, close/reopen and runtime lifetime', flush=True)
    print('CAPTURE resize/maximize/restore and CSD menu for visual verification', flush=True)
    if args.full_ui:
        action('full_ui', 'org.anvildroid.desktop')
        full = wait_for('Full UI', lambda: windows('Waydroid'))[0]
        capture(full, 'full-ui')
        run('wmctrl', '-i', '-c', full)
        wait_for('Full UI close', lambda: not windows('Waydroid'))
        alive()
        action('launch', 'com.android.settings')
        assert wait_for('app mode restored', lambda: windows('com.android.settings'))
        print('PASS Full UI enter/close and return to app-window mode', flush=True)
    report = dict(runtime=args.runtime, generation=app_result.get('generation'),
                  apps=len(inventory), screenshots=str(output), state=request('inspect')['state'])
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--full-ui', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'target/x11-session-check')
    main(parser.parse_args())
