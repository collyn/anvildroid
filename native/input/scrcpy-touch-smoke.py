#!/usr/bin/env python3
"""Explicit diagnostic: send a short gesture sequence ONLY to focused Touch Probe.

sudo python3 -B native/input/scrcpy-touch-smoke.py RUNTIME SERVER_JAR
Dumpsys checks here are a diagnostic precondition, not production route fencing.
"""
import importlib.util
from pathlib import Path
import re
import sys
import time

from scrcpy_control import touch_packet

PACKAGE = 'org.anvildroid.touchprobe'


def run_probe(connection, lxc, identifier, scenario='gestures'):
    def android(*args):
        return lxc('lxc-attach', identifier, '--', '/system/bin/' + args[0],
                   *args[1:], timeout=3)

    def geometry():
        report = android('dumpsys', 'window', '-a')
        focus = re.search(r'mCurrentFocus=Window\{[^\n]+\}', report)
        if not focus or f'{PACKAGE}/{PACKAGE}.MainActivity' not in focus[0]:
            raise RuntimeError('Touch Probe must be the focused Android window')
        windows = re.split(r'\n  Window #\d+ ', report)
        blocks = [block for block in windows[1:]
                  if f'{PACKAGE}/{PACKAGE}.MainActivity' in block.splitlines()[0]]
        if len(blocks) != 1 or 'isVisible=true' not in blocks[0]:
            raise RuntimeError('Expected exactly one visible Touch Probe window')
        match = re.search(r'Frames:.*? frame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]', blocks[0])
        if not match or 'mDisplayId=0 ' not in blocks[0]:
            raise RuntimeError('Unverified probe frame/display')
        return tuple(map(int, match.groups()))

    bounds = geometry()
    sizes = re.findall(r'(?:Override|Physical) size: (\d+)x(\d+)', android('wm', 'size'))
    if not sizes:
        raise RuntimeError('Unverified Android display size')
    width, height = map(int, sizes[-1])
    left, top, right, bottom = bounds
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise RuntimeError('Probe outside display')
    x, y = left + (right-left)//3, top + (bottom-top)//2
    events = []
    before = android('logcat', '-d', '-s', 'AnvilTouchProbe:I', '*:S').splitlines()

    def send(action, pointer, px, py, delay=.08):
        connection.sendall(touch_packet(action, pointer, px, py, width, height))
        events.append([action, pointer, px, py])
        time.sleep(delay)

    if geometry() != bounds:
        raise RuntimeError('Probe moved before test')
    # Tap, hold + drag, then overlapping fingers released independently.
    send(0, 0, x, y)
    send(1, 0, x, y)
    send(0, 0, x, y, .3)
    send(2, 0, x+30, y+20)
    send(1, 0, x+30, y+20)
    send(0, 0, x, y)
    send(0, 1, x+60, y+40)
    send(2, 0, x+20, y)
    send(1, 1, x+60, y+40)
    send(1, 0, x+20, y)
    if scenario == 'cancel':
        if geometry() != bounds:
            raise RuntimeError('Probe moved before cancel test')
        send(0, 0, x, y)
        send(0, 1, x+60, y+40)
        # Deliberately exercise stock CANCEL only in the diagnostic app. The
        # production encoder rejects it because server pointer state survives.
        packet = bytearray(touch_packet(2, 0, x, y, width, height))
        packet[1] = 3
        connection.sendall(packet)
        events.append([3, 0, x, y])
        time.sleep(.1)
        send(0, 2, x+20, y+20)
        send(1, 2, x+20, y+20)
        send(0, 0, x, y)
        send(1, 0, x, y)
    elif scenario == 'disconnect':
        if geometry() != bounds:
            raise RuntimeError('Probe moved before disconnect test')
        send(0, 0, x, y)
        connection.shutdown(2)
        time.sleep(.5)
    time.sleep(.2)
    after = android('logcat', '-d', '-s', 'AnvilTouchProbe:I', '*:S').splitlines()
    # Preserve Android observations for review; never infer delivery from sendall.
    seen = set(before)
    observed = [line for line in after if line not in seen]
    return {'scenario': scenario, 'bounds': bounds, 'display': [width, height], 'sent': events,
            'android_observed': observed}


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] not in ('cancel', 'disconnect')):
        raise SystemExit(__doc__)
    spec = importlib.util.spec_from_file_location('handshake', Path(__file__).with_name('scrcpy-handshake.py'))
    handshake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handshake)
    scenario = sys.argv[3] if len(sys.argv) == 4 else 'gestures'
    handshake.main(*sys.argv[1:3], probe=lambda *args: run_probe(*args, scenario=scenario))
