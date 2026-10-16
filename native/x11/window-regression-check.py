#!/usr/bin/env python3
"""Check real Music/Settings fit and alternating CSD drags on one X11 runtime.

The selected runtime must already be running. Only its two app windows are
launched/moved/resized. Desktop/RustDesk services and other runtimes are untouched.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('client', ROOT / 'scripts/runtime-controller-client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def run(*args, acceptable=(0,)):
    result = subprocess.run(args, text=True, capture_output=True, timeout=10)
    if result.returncode not in acceptable:
        raise RuntimeError(' '.join(args) + ': ' + result.stderr)
    return result.stdout


def wait_for(label, check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.2)
    raise RuntimeError('Timed out: ' + label)


def geometry(window):
    return {key: int(value) for key, value in
            re.findall(r'^(X|Y|WIDTH|HEIGHT)=(-?\d+)$',
                       run('xdotool', 'getwindowgeometry', '--shell', window), re.M)}


def main(args):
    assert re.fullmatch(r'r-[0-9a-f]{32}', args.runtime)
    assert os.environ.get('XDG_SESSION_TYPE') == 'x11'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    def request(op, **fields):
        response = client.request(Path('/run/anvildroid/control.sock'),
                                  dict(op=op, id=args.runtime, **fields))
        assert response['ok'], response
        return response['result']

    assert request('inspect')['state'] == 'Running'
    packages = {app['package'] for app in request('apps')['apps']}
    music = next((name for name in ('org.lineageos.eleven', 'org.lineageos.twelve') if name in packages), None)
    assert music, 'This check requires the image Music app (Eleven or Twelve)'
    windows = {}
    for label, package in [('settings', 'com.android.settings'), ('music', music)]:
        request('app_action', action='launch', package=package)

        def completed():
            job = request('app_job')
            assert job['status'] != 'Failed', job
            return job['status'] == 'Succeeded'

        wait_for(label + ' launch', completed, 45)
        expression = '^' + re.escape('waydroid.anvildroid.' + args.runtime + '.' + package) + '$'
        windows[label] = wait_for(label + ' native window', lambda: run(
            'xdotool', 'search', '--onlyvisible', '--classname', expression,
            acceptable=(0, 1)).split())[0]

    def capture_fit(label, suffix):
        time.sleep(1.5)
        path = output / (label + '-' + suffix + '.png')
        run('import', '-window', windows[label], str(path))
        # Both apps paint a solid toolbar background at y=44. A contain fit
        # left over from a stale 824px task makes either edge black instead.
        with Image.open(path).convert('RGB') as picture:
            row = [picture.getpixel((x, 44)) for x in (3, picture.width // 2, picture.width - 4)]
            assert max(row[0]) > 8 and max(row[2]) > 8, (path, row)
        return geometry(windows[label])

    # Inspect the initial open before a manual resize can hide its regression.
    initial = {label: capture_fit(label, 'initial') for label in windows}
    for label, x in [('music', 100), ('settings', 650)]:
        window = windows[label]
        run('wmctrl', '-i', '-r', window, '-b', 'remove,maximized_vert,maximized_horz')
        run('wmctrl', '-i', '-r', window, '-e', f'0,{x},100,-1,-1')
    time.sleep(.6)
    drags = []
    for index in range(6):
        label = ('music', 'settings')[index % 2]
        other = 'settings' if label == 'music' else 'music'
        before, stationary = geometry(windows[label]), geometry(windows[other])
        run('xdotool', 'mousemove', '--sync', '--window', windows[label], '170', '16')
        time.sleep(.1)
        run('xdotool', 'mousedown', '1')
        try:
            time.sleep(.15)
            run('xdotool', 'mousemove', '--sync', str(before['X'] + 210), str(before['Y'] + 36))
            time.sleep(.15)
        finally:
            run('xdotool', 'mouseup', '1')
        time.sleep(.55)
        after = geometry(windows[label])
        assert (after['X'] - before['X'], after['Y'] - before['Y']) == (40, 20), (label, before, after)
        assert geometry(windows[other]) == stationary, 'Dragging ' + label + ' moved ' + other
        drags.append(dict(app=label, before=before, after=after))
    print('PASS six alternating real WM/CSD drags move only the selected app', flush=True)
    for label in windows:
        run('wmctrl', '-i', '-r', windows[label], '-e', '0,-1,-1,578,738')
        capture_fit(label, 'resized')
        run('wmctrl', '-i', '-r', windows[label], '-b', 'add,maximized_vert,maximized_horz')
        wait_for(label + ' maximize', lambda: '_NET_WM_STATE_MAXIMIZED_HORZ' in run(
            'xprop', '-id', windows[label], '_NET_WM_STATE'))
        capture_fit(label, 'maximized')
        run('wmctrl', '-i', '-r', windows[label], '-b', 'remove,maximized_vert,maximized_horz')
        capture_fit(label, 'restored')
    assert request('inspect')['state'] == 'Running'
    report = dict(runtime=args.runtime, initial=initial, drags=drags, state='Running')
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS content reaches both native window edges at open/resize/maximize/restore', flush=True)
    print(output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime')
    parser.add_argument('--output', type=Path, default=ROOT / 'target/x11-window-regression')
    main(parser.parse_args())
