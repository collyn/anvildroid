#!/usr/bin/env python3
"""Held-input bridge disconnect and full-runtime restart in disposable Android.

Run `before` against a running live-android fixture. It restarts only that
guest's HWC, observes actual Android cancellation/recovery, then Stops the
runtime with three contacts held. Start a new generation with live-android and
run `after` with the same evidence directory. No installed controller is used.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


isolation = module('probe-editor-isolation')
probe = isolation.probe
require = probe.require


def tail(before, after):
    require(after[:len(before)] == before, 'Android log buffer changed; evidence is incomplete')
    return after[len(before):]


def active(lines):
    events = [line for line in lines if 'AnvilTouchProbe:' in line and ' ACTION_' in line]
    require(events and not any('WARNING' in line for line in lines), 'No actual contacts or stale observer state')
    count = re.search(r' activeAfter=(\d+)(?:$|\s)', events[-1])
    require(count, 'Malformed contact count')
    return int(count[1])


class Session(isolation.Endpoint):
    def sync_pointer(self):
        if self.nested:
            self.run(self.x + ['mousemove', '1599', '999']); time.sleep(.15)
            self.run(self.x + ['mousemove', '0', '0']); time.sleep(.15)

    def attach(self):
        self.sync_pointer()
        deadline = time.monotonic() + 35
        while True:
            try:
                windows = self.run(self.x + ['search', '--onlyvisible', '--class',
                    'weston' if self.nested else r'\.org\.anvildroid\.touchprobe$']).splitlines()
                require(len(windows) == 1, 'Ambiguous observer window')
                self.window = windows[0]; self.offset = (0, 0)
                if self.nested:
                    require(self.window == self.transport['x11_window'], 'Unexpected compositor window')
                    self.offset = probe.scene_rectangle(self.scene(), self.m['id'])
                self.run(self.x + ['windowfocus', '--sync', self.window] + self.move(220, 320) +
                         (['click', '1'] if self.nested else []))
                time.sleep(.3)
                logs = self.logs()
                focus = [line for line in logs if 'AnvilTouchProbe:' in line and ' FOCUS=' in line]
                require(focus and ' FOCUS=true active=0' in focus[-1], 'Observer not focused/contact-free')
                return
            except (RuntimeError, subprocess.CalledProcessError):
                require(time.monotonic() < deadline, 'Observer did not become ready'); time.sleep(.3)

    def prepare(self, install=False):
        if install:
            fd = os.open(probe.REPO / 'target/touch-probe/AnvilDroidTouchProbe.apk', os.O_RDONLY)
            try:
                result = self.backend.status(self.m['id'], 'install',
                    dict(op='install', generation=self.m['generation']), apk_fd=fd)
                require(not result.get('app_error'), 'Probe installation failed')
            finally: os.close(fd)
        path = '/data/waydroid_tmp/anvildroid/keymaps'
        self.run(self.guest + ['/system/bin/mkdir', '-p', path])
        self.run(self.guest + ['/system/bin/chown', '1000:1000', path])
        self.run(self.guest + ['/system/bin/chmod', '0700', path])
        self.saved = probe.v3_profile(self.m['id'], [
            probe.v3_point(key=48, nx=49000, ny=45000, end_mode=2),
            probe.v3_point(key=17, nx=16000, ny=45000, kind=1, ex=28000, ey=45000, radius=9000, ramp_ms=80),
            probe.v3_point(kind=7, source=2),
        ])
        # The production graphical editor saves as Android system UID.
        self.run(self.guest[:-1] + ['--uid', '1000', '--gid', '1000', '--', '/system/bin/sh', '-c',
                 'cat > ' + path + '/' + probe.PACKAGE], input=self.saved.encode())
        for action in ('force_stop', 'launch'):
            self.request('app_action', action=action, package=probe.PACKAGE)
        self.run(self.guest + ['/system/bin/cmd', 'statusbar', 'collapse'])
        self.attach()
        require(self.profile() == self.saved, 'Profile did not load under the correct runtime')

    def wait_active(self, before, count, timeout=8):
        deadline = time.monotonic() + timeout
        while True:
            rows = tail(before, self.logs())
            try:
                if active(rows) == count: return rows
            except RuntimeError: pass
            require(time.monotonic() < deadline, 'Expected actual active contact count ' + str(count)); time.sleep(.1)

    def hold(self):
        before = self.logs()
        self.run(self.x + ['keydown', 'b', 'keydown', 'w', 'mousedown', '1'])
        rows = self.wait_active(before, 3)
        require(' ACTION_POINTER_DOWN' in '\n'.join(rows), 'Multiple fingers not observed')
        return before

    def release(self):
        self.run(self.x + ['keyup', 'b', 'keyup', 'w', 'mouseup', '1'])

    def fresh(self, label):
        before = self.hold(); time.sleep(.3); self.release()
        rows = self.wait_active(before, 0)
        probe.touch_events(rows)
        require(sum(' ACTION_DOWN ' in line for line in rows) == 1, 'Unexpected fresh stream count')
        (self.output / (label + '.log')).write_text('\n'.join(rows) + '\n')


def before(session):
    s = session; s.prepare(install=True); results = []
    s.run(s.guest + ['/system/bin/logcat', '-G', '16M'])
    s.fresh('baseline-three-contacts'); results.append('baseline-three-contacts')
    checkpoint = s.hold()
    service = 'vendor.hwcomposer-2-1'
    require(s.run(s.guest + ['/system/bin/getprop', 'init.svc.' + service]).strip() == 'running', 'Unexpected HWC service')
    s.run(s.guest + ['/system/bin/stop', service])
    rows = s.wait_active(checkpoint, 0, timeout=15)
    probe.touch_events(rows, allow_framework_cancel=True)
    require(any(' ACTION_CANCEL ' in row for row in rows), 'Disconnect did not cancel actual Android contacts')
    (s.output / 'bridge-disconnect-held.log').write_text('\n'.join(rows) + '\n')
    time.sleep(.5)
    quiet = s.logs(); time.sleep(.5)
    probe.require_no_mapped_contact(tail(quiet, s.logs()), 'Input replayed while disconnected')
    results.append('bridge-disconnect-cancels-three-held-contacts')
    s.run(s.guest + ['/system/bin/start', service])
    # Relaunch uses the production app path after HWC and SurfaceFlinger recover.
    deadline = time.monotonic() + 75
    while True:
        try:
            s.request('app_action', action='force_stop', package=probe.PACKAGE)
            s.request('app_action', action='launch', package=probe.PACKAGE)
            s.attach(); break
        except RuntimeError:
            require(time.monotonic() < deadline, 'App failed to recover after bridge disconnect'); time.sleep(.5)
    checkpoint = s.logs(); time.sleep(.6)
    probe.require_no_mapped_contact(tail(checkpoint, s.logs()), 'Held input replayed after bridge reconnect')
    s.release(); time.sleep(.2)
    probe.require_no_mapped_contact(tail(checkpoint, s.logs()), 'Stale releases injected after bridge reconnect')
    results.append('bridge-reconnect-no-held-replay')
    s.fresh('bridge-reconnect-fresh-input'); results.append('bridge-reconnect-fresh-three-contacts')
    require(s.profile() == s.saved, 'Disconnect changed saved profile')
    checkpoint = s.hold()
    (s.output / 'runtime-stop-held.log').write_text('\n'.join(tail(checkpoint, s.logs())) + '\n')
    # Deliberately leave the physical buttons down. The whole private desktop
    # exits with the runtime; `after` must authenticate a different generation.
    old = dict(metadata=s.m, worker_start=s.identity[0], mount_namespace=s.identity[1],
               saved_profile=s.saved, scenarios=results, held_contacts=3)
    (s.output / 'before-restart.json').write_text(json.dumps(old, indent=2) + '\n')
    s.guard()
    result = s.backend.status(s.m['id'], 'stop')
    require(result.get('generation') == s.m['generation'], 'Stop targeted another generation')
    (s.output / 'stop-response.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(phase='before', passed=results, stopped_with_contacts=3)), flush=True)


def after(session):
    s = session; old = json.loads((s.output / 'before-restart.json').read_text())
    require(old['metadata']['id'] == s.m['id'] and old['metadata']['root'] == s.m['root'], 'Restart changed fixture')
    require(old['metadata']['generation'] != s.m['generation'] and old['mount_namespace'] != s.identity[1],
            'Require a new runtime generation and namespace')
    require(s.profile() == old['saved_profile'], 'Full restart lost saved profile')
    result = s.backend.status(s.m['id'], 'app_action', dict(op='app_action',
        generation=old['metadata']['generation'], action='launch', package=probe.PACKAGE))
    require(result.get('app_error') and result['generation'] == s.m['generation'], 'Old generation was not rejected')
    (s.output / 'stale-generation-rejection.json').write_text(json.dumps(result, indent=2) + '\n')
    s.request('app_action', action='launch', package=probe.PACKAGE); s.attach()
    checkpoint = s.logs(); time.sleep(.7)
    probe.require_no_mapped_contact(tail(checkpoint, s.logs()), 'Runtime restart replayed old contacts')
    s.release(); time.sleep(.2)
    probe.require_no_mapped_contact(tail(checkpoint, s.logs()), 'New desktop releases replayed old input')
    s.fresh('runtime-restart-fresh-input')
    require(s.profile() == old['saved_profile'], 'Recovery changed profile')
    results = old['scenarios'] + ['runtime-stop-with-three-held-contacts', 'new-generation-no-contact-replay',
        'stale-generation-rejected', 'runtime-restart-fresh-three-contacts', 'saved-profile-survives-restart']
    (s.output / 'summary.json').write_text(json.dumps(dict(passed=True, scenarios=results,
        previous_generation=old['metadata']['generation'], generation=s.m['generation'],
        backend='wayland' if s.nested else 'native-x11', package_sha256=s.m['package_sha256']), indent=2) + '\n')
    print(json.dumps(dict(passed=True, scenarios=len(results))), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=('before', 'after')); p.add_argument('metadata', type=Path)
    p.add_argument('worker_pid', type=int); p.add_argument('output', type=Path)
    a = p.parse_args(); require(os.geteuid() == 0, 'Root required')
    a.output.mkdir(parents=True, exist_ok=True)
    s = Session(a.metadata, a.worker_pid, a.output)
    try: (before if a.phase == 'before' else after)(s)
    except BaseException:
        try: s.release()
        except (OSError, RuntimeError): pass
        raise


if __name__ == '__main__': main()
