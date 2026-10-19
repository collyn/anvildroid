"""Input acceptance adapter used only inside default-android's private PID1.

No host endpoint argument or standalone entry point: the parent authenticates
the disposable stock installation and pins these helper bytes in preflight.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


recovery = module('probe-controls-recovery')
probe = recovery.probe
require = probe.require
HELPERS = ('default-controls.py', 'probe-controls-recovery.py', 'probe-editor-isolation.py', 'probe-android-controls.py')


def start_display(root, value, processes):
    require(os.getpid() == 1 and os.readlink('/proc/self/ns/mnt') != value['host_namespaces']['mnt'],
            'Default controls require the isolated PID1 fixture')
    home = Path(value['home']); runtime = home / 'runtime'
    authority = runtime / 'controls-authority'; authority.touch(mode=0o600)
    os.chown(authority, value['owner_uid'], value['owner_gid'])
    subprocess.run(['xauth', '-f', str(authority), 'add', ':77', '.', secrets.token_hex(16)], check=True, timeout=5)
    # xauth atomically replaces the file under its caller UID.
    os.chown(authority, value['owner_uid'], value['owner_gid']); authority.chmod(0o600)
    Path('/tmp/.X11-unix').mkdir(mode=0o1777, exist_ok=True)
    env = dict(os.environ, DISPLAY=':77', XAUTHORITY=str(authority), XDG_RUNTIME_DIR=str(runtime),
               HOME=str(home), LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe',
               __EGL_VENDOR_LIBRARY_FILENAMES='/usr/share/glvnd/egl_vendor.d/50_mesa.json',
               __GLX_VENDOR_LIBRARY_NAME='mesa')
    def spawn(label, command, **extra):
        with (root / (label + '.log')).open('ab') as log:
            child = subprocess.Popen(command, env=env, stdout=log, stderr=log, **extra)
        processes.append(child); return child
    xvfb = spawn('controls-xvfb', ['Xvfb', ':77', '-nolock', '-screen', '0', '1700x1100x24',
                 '-nolisten', 'tcp', '-noreset', '-auth', str(authority)])
    deadline = time.monotonic() + 10
    while not Path('/tmp/.X11-unix/X77').is_socket():
        require(xvfb.poll() is None and time.monotonic() < deadline, 'Private Default Xvfb failed'); time.sleep(.1)
    if value.get('controls_backend', 'wayland') == 'native-x11':
        spec = importlib.util.spec_from_file_location('installed_x11',
            '/usr/local/lib/anvildroid-controller/runtime-x11.py')
        adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
        paths, _ = adapter.load_payload('/usr/local/lib/anvildroid-controller/native-x11')
        weston = spawn('controls-native-x11', [str(paths['anvildroid-x11-host']),
            '--socket', str(runtime / 'fixture-wayland')],
            user=value['owner_uid'], group=value['owner_gid'], extra_groups=[])
    else:
        weston = spawn('controls-weston', ['weston', '--no-config', '--backend=x11', '--renderer=gl',
            '--socket=fixture-wayland', '--width=1600', '--height=1000', '--idle-time=0', '--debug'],
            user=value['owner_uid'], group=value['owner_gid'], extra_groups=[])
    deadline = time.monotonic() + 15
    while not (runtime / 'fixture-wayland').is_socket():
        require(weston.poll() is None and time.monotonic() < deadline, 'Private Default Weston failed'); time.sleep(.1)
    return weston


class Session(recovery.Session):
    def __init__(self, root, value, request, weston):
        require(os.getpid() == 1 and root.parent == Path('/tmp') and root.name.startswith('anvil-default-') and
                root.resolve() == root and root.stat().st_uid == 0 and not root.stat().st_mode & 0o077,
                'Default controls require a private disposable installation')
        require(all(os.readlink('/proc/self/ns/' + name) != identity for name, identity in value['host_namespaces'].items()),
                'Default controls reused a host namespace')
        self.value = value; self.controller = request; self.weston = weston
        self.guest = ['lxc-attach', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '--']
        pid = subprocess.run(['lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'],
                             check=True, capture_output=True, text=True, timeout=5).stdout.strip()
        require(pid.isdecimal() and int(pid) > 1, 'Missing fixture Android init')
        self.pid = int(pid); self.identity = self.state()
        require(self.identity[1] != os.readlink('/proc/self/ns/mnt'), 'Android has no private mount namespace')
        text = Path('/var/lib/waydroid/overlay/system/etc/init/init.waydroid.rc').read_text()
        generation = re.findall(r'^    setenv ANVILDROID_RUNTIME_GENERATION ([a-f0-9]{32})$', text, re.M)
        require(len(generation) == 1 and '    setenv ANVILDROID_RUNTIME_ID default\n' in text, 'Default bridge not scoped')
        self.m = dict(id='default', generation=generation[0], owner_uid=value['owner_uid'])
        self.output = root / 'controls'; self.output.mkdir(exist_ok=True)
        self.nested = value.get('controls_backend', 'wayland') == 'wayland'
        self.x = ['env', 'DISPLAY=:77', 'XAUTHORITY=' + value['home'] + '/runtime/controls-authority', 'xdotool']
        self.transport = {}
        if not self.nested: return
        deadline = time.monotonic() + 5
        while True:
            windows = subprocess.run(self.x + ['search', '--onlyvisible', '--class', 'weston'],
                                     capture_output=True, text=True, timeout=5).stdout.splitlines()
            if len(windows) == 1: break
            require(time.monotonic() < deadline, 'Private compositor output ambiguous'); time.sleep(.1)
        self.transport = dict(x11_window=windows[0]); self.window = windows[0]

    def guard(self):
        require(os.getpid() == 1 and self.state() == self.identity and self.weston.poll() is None,
                'Default fixture Android/compositor identity changed')

    def request(self, op, **fields):
        self.guard()
        if op == 'app_action' and fields.get('action') in ('launch', 'force_stop'):
            require(fields.get('package') == probe.PACKAGE, 'Fixture may operate only its diagnostic app')
            if fields['action'] == 'force_stop':
                self.run(self.guest + ['/system/bin/am', 'force-stop', probe.PACKAGE])
                deadline = time.monotonic() + 10
                while True:
                    if self.nested:
                        gone = ' of ' + probe.surface_app_id('default') + ',' not in self.scene()
                    else:
                        gone = not subprocess.run(self.x + ['search', '--onlyvisible', '--class',
                            r'\.org\.anvildroid\.touchprobe$'], capture_output=True, timeout=5).stdout.strip()
                    if gone: break
                    require(time.monotonic() < deadline, 'Stopped Default app surface remained mapped'); time.sleep(.1)
            else:
                # Use the installed stock app-launch API, including its intent
                # and task-reuse behavior. A hand-written `am start` differs.
                self.run(['python3', '-c',
                    'import argparse,sys\nfrom pathlib import Path\n'
                    'sys.path.insert(0,str(Path("/usr/bin/waydroid").resolve().parent))\n'
                    'import tools\nfrom tools.interfaces import IPlatform\n'
                    'args=argparse.Namespace(details_to_stdout=False)\n'
                    'tools.helpers.logging.add_verbose_log_level();tools.prep_args(args)\n'
                    'args.BINDER_DRIVER="anvil-fixture-binder"\n'
                    'platform=IPlatform.get_service(args)\n'
                    'assert platform is not None,"Private stock platform unavailable"\n'
                    'platform.setprop("waydroid.active_apps", "' + probe.PACKAGE + '")\n'
                    'platform.launchApp("' + probe.PACKAGE + '")\n'
                    'platform.settingsPutString(2,"policy_control","immersive.full=*")\n'])
            return {}
        return self.controller(op, **fields)

    def scene(self):
        return self.run(['env', 'XDG_RUNTIME_DIR=' + self.value['home'] + '/runtime',
                         'WAYLAND_DISPLAY=fixture-wayland', 'weston-debug', 'scene-graph'])

    def attach(self):
        # Restore/Activity recreation can replace the X window after the first
        # focus check. Rediscover it instead of acting on the obsolete handle.
        for attempt in range(3):
            try:
                return self.attach_settled()
            except subprocess.CalledProcessError:
                if attempt == 2: raise
                time.sleep(.2)

    def attach_settled(self):
        try:
            super().attach()
            # Activity recreation/resize continues after the first focus event.
            # Begin measurement only after the actual fitted content is stable.
            deadline = time.monotonic() + 15; previous = None; stable = time.monotonic()
            while True:
                require(time.monotonic() < deadline, 'Default app geometry did not settle')
                rows = self.logs()
                try: geometry = probe.editor_attachment(rows, 'default')
                except RuntimeError:
                    previous = None; stable = time.monotonic(); time.sleep(.15); continue
                sizes = [row for row in rows if 'AnvilTouchProbe:' in row and ' SIZE contentPx=' in row]
                snapshot = (geometry, sizes[-1] if sizes else None)
                if snapshot != previous: previous = snapshot; stable = time.monotonic()
                if time.monotonic() - stable >= 1: break
                time.sleep(.15)
            # Stock task resizing may recreate/move the host surface after
            # the initial focus click. Place the pointer inside the settled
            # content, rather than reusing coordinates from the old frame.
            if self.nested:
                self.offset = probe.scene_rectangle(self.scene(), 'default')
                frame = (0, 0)
            else:
                frame = (5, 33)
            ox, oy, width, height = geometry
            self.sync_pointer()
            self.run(self.x + ['windowfocus', '--sync', self.window] +
                     self.move(frame[0] + ox + width // 2, frame[1] + oy + height // 2) +
                     (['click', '1'] if self.nested else []))
            time.sleep(.3)
            focus = [row for row in self.logs() if 'AnvilTouchProbe:' in row and ' FOCUS=' in row]
            require(focus and ' FOCUS=true active=0' in focus[-1], 'Settled Default app lost focus')
        except BaseException:
            if self.nested: (self.output / 'failed-attach-scene.txt').write_text(self.scene())
            (self.output / 'failed-attach-android.log').write_text('\n'.join(self.logs()))
            (self.output / 'failed-attach-properties.txt').write_text(self.run(self.guest + ['/system/bin/getprop']))
            raise

    def wait_active(self, before, count, timeout=8):
        try:
            return super().wait_active(before, count, timeout)
        except RuntimeError:
            (self.output / 'failed-input.log').write_text('\n'.join(recovery.tail(before, self.logs())) + '\n')
            (self.output / 'failed-pointer.txt').write_text(self.run(self.x + ['getmouselocation', '--shell']))
            if self.nested: (self.output / 'failed-input-scene.txt').write_text(self.scene())
            raise

    def install(self, root):
        with (root / 'probe.apk').open('rb') as stream:
            self.run(self.guest + ['/system/bin/sh', '-c', 'cat > /data/local/tmp/controls-probe.apk'], input=stream.read())
        output = self.run(self.guest + ['/system/bin/pm', 'install', '-r', '/data/local/tmp/controls-probe.apk'])
        require('Success' in output, 'Default probe APK did not install')


def delivery(s):
    profile = ('ANVIL_KEYMAP 2 default ' + probe.PACKAGE + ' 7\n'
        '17 16000 40000 1 1 28000 40000 120 1\n'
        '19 10000 20000 1 2 50000 20000 300 1\n'
        '20 30000 30000 1 3 30000 30000 100 1\n'
        '34 40000 40000 1 4 50000 50000 120 1\n'
        '35 16000 16000 0 5 48000 48000 100 2\n'
        '36 32000 32000 1 6 48000 48000 120 1\n'
        '48 20000 45000 1 0 0 0 0 0\n')
    s.run(s.guest[:-1] + ['--uid', '1000', '--gid', '1000', '--', '/system/bin/sh', '-c',
        'cat > /data/waydroid_tmp/anvildroid/keymaps/' + probe.PACKAGE], input=profile.encode())
    for action in ('force_stop', 'launch'): s.request('app_action', action=action, package=probe.PACKAGE)
    s.attach(); results = []
    cases = [
        ('basic', ['keydown', 'b', 'sleep', '.2', 'keyup', 'b'], 1, False, False),
        ('wasd', ['keydown', 'w', 'keydown', 'd', 'sleep', '.2', 'keyup', 'd', 'keyup', 'w'], 1, True, False),
        ('swipe', ['keydown', 'r', 'sleep', '.4', 'keyup', 'r'], 1, True, False),
        ('repeat', ['keydown', 't'], 2, False, False),
        ('skill', ['keydown', 'g'] + s.move(300, 450) + ['sleep', '.2', 'keyup', 'g'], 1, True, False),
        ('macro', ['keydown', 'h'], 2, False, False),
        ('aim-fire', ['keydown', 'j'] + s.move(330, 480) + ['mousedown', '1', 'sleep', '.2', 'mouseup', '1', 'keyup', 'j'], 1, True, True),
        ('simultaneous', ['keydown', 'w', 'keydown', 'b', 'sleep', '.2', 'keyup', 'b', 'keyup', 'w'], 1, False, True),
    ]
    for label, sequence, minimum, motion, multi in cases:
        checkpoint = s.logs(); s.run(s.x + sequence)
        deadline = time.monotonic() + 5
        while True:
            rows = recovery.tail(checkpoint, s.logs())
            count = sum('AnvilTouchProbe:' in row and ' ACTION_DOWN ' in row for row in rows)
            if count >= minimum and probe.gesture_complete(rows): break
            if time.monotonic() >= deadline: break
            time.sleep(.1)
        if label in ('repeat', 'macro'):
            s.run(s.x + ['keyup', 't' if label == 'repeat' else 'h'])
            s.wait_active(checkpoint, 0)
        rows = recovery.tail(checkpoint, s.logs())
        (s.output / (label + '.log')).write_text('\n'.join(rows) + '\n')
        events = probe.touch_events(rows)
        downs = sum(' ACTION_DOWN ' in row for row in events)
        require(downs >= minimum and (label != 'macro' or downs == 2), label + ': missing/extra contacts')
        if motion: require(any(' ACTION_MOVE ' in row for row in events), label + ': no movement')
        if multi: require(any(' ACTION_POINTER_DOWN' in row for row in events), label + ': no simultaneous contacts')
        results.append(label)
    tree = s.run(['env', 'DISPLAY=:77', 'XAUTHORITY=' + s.value['home'] + '/runtime/controls-authority', 'xwininfo', '-root'])
    root = re.search(r'Window id: (0x[0-9a-f]+)', tree)[1]
    for held in (False, True):
        label = 'held-refocus' if held else 'blur'
        checkpoint = s.logs(); s.run(s.x + ['keydown', 'w']); s.wait_active(checkpoint, 1)
        s.run(s.x + ['windowfocus', '--sync', root]); s.wait_active(checkpoint, 0)
        probe.touch_events(recovery.tail(checkpoint, s.logs()))
        s.attach(); quiet = s.logs(); time.sleep(.3)
        if held: s.run(s.x + ['keydown', 'w']); time.sleep(.2)
        s.run(s.x + ['keyup', 'w']); time.sleep(.2)
        probe.require_no_mapped_contact(recovery.tail(quiet, s.logs()), 'Cancelled held key replayed')
        fresh = s.logs(); s.run(s.x + ['keydown', 'w', 'sleep', '.2', 'keyup', 'w']); s.wait_active(fresh, 0)
        probe.touch_events(recovery.tail(fresh, s.logs()))
        (s.output / (label + '.log')).write_text('\n'.join(recovery.tail(checkpoint, s.logs())) + '\n')
        results.append(label)
    # Actual existing editor Save migrates V2, then app replacement reloads it.
    s.nx = 20000; s.ny = 45000; s.editor('v'); saved = s.profile()
    require(saved.startswith('ANVIL_KEYMAP 3 default '), 'Default editor did not Save V3')
    require(saved.splitlines()[-1].split()[0] == '47', 'Default editor did not rebind the selected Basic control')
    (s.output / 'editor-saved-profile.txt').write_text(saved)
    for action in ('force_stop', 'launch'): s.request('app_action', action=action, package=probe.PACKAGE)
    s.attach(); checkpoint = s.logs(); s.run(s.x + ['key', 'v']); s.wait_active(checkpoint, 0)
    probe.touch_events(recovery.tail(checkpoint, s.logs()))
    require(s.profile() == saved, 'App replacement lost Default editor Save')
    results += ['editor-save-reload', 'app-replacement-fresh-input']
    return results


def window_lifecycle(s):
    saved = s.profile(); geometry = probe.editor_attachment(s.logs(), 'default')
    ox, oy, width, height = geometry
    s.run(s.x + s.move(10, height // 2)); time.sleep(.2)
    def pixels(label):
        if s.nested: s.offset = probe.scene_rectangle(s.scene(), 'default')
        frame = (0, 0) if s.nested else (5, 33)
        cx = s.offset[0] + frame[0] + ox + round(20000 * (width - 1) / 65535)
        cy = s.offset[1] + frame[1] + oy + round(45000 * (height - 1) / 65535)
        path = s.output / (label + '.rgb')
        s.run(['env', 'DISPLAY=:77', 'XAUTHORITY=' + s.value['home'] + '/runtime/controls-authority',
               'import', '-window', s.window, '-crop', f'60x30+{cx-30}+{cy-15}', '+repage', '-depth', '8', 'rgb:' + str(path)])
        data = path.read_bytes(); require(len(data) == 60 * 30 * 3, 'Incomplete marker pixel capture'); return data
    initial = pixels('marker-visible')
    checkpoint = s.logs(); s.run(s.x + ['keydown', 'w']); s.wait_active(checkpoint, 1)
    s.run(s.x + ['key', 'F4']); time.sleep(.25); hidden = pixels('marker-hidden')
    require(recovery.active(recovery.tail(checkpoint, s.logs())) == 1, 'Hiding markers ended playback')
    s.run(s.x + ['key', 'F4']); time.sleep(.25); restored = pixels('marker-restored')
    s.run(s.x + ['keyup', 'w']); s.wait_active(checkpoint, 0)
    probe.touch_events(recovery.tail(checkpoint, s.logs()))
    require(initial != hidden and restored == initial, 'Default markers did not visibly hide and restore')

    checkpoint = s.logs(); s.run(s.x + ['keydown', 'w']); s.wait_active(checkpoint, 1)
    s.controller('app_action', action='keymap_edit', package=probe.PACKAGE)
    s.wait_active(checkpoint, 0); probe.touch_events(recovery.tail(checkpoint, s.logs()))
    quiet = s.logs(); s.run(s.x + ['keyup', 'w', 'key', 'v']); time.sleep(.25)
    probe.require_no_mapped_contact(recovery.tail(quiet, s.logs()), 'Controller-opened Default editor leaked input')
    s.run(s.x + ['key', 'Escape']); time.sleep(.2)
    require(s.profile() == saved, 'Default editor Cancel changed saved bytes')

    normal = None
    def maximize():
        nonlocal normal
        if s.nested:
            s.offset = probe.scene_rectangle(s.scene(), 'default')
            s.run(s.x + s.move(180, -17) + ['click', '--repeat', '2', '--delay', '100', '1'])
        else:
            info = dict(line.split('=', 1) for line in s.run(s.x + ['getwindowgeometry', '--shell', s.window]).splitlines() if '=' in line)
            maximizing = normal is None
            size = ('1510', '938') if maximizing else normal
            normal = (info['WIDTH'], info['HEIGHT']) if maximizing else None
            state = '_NET_WM_STATE_MAXIMIZED_VERT, _NET_WM_STATE_MAXIMIZED_HORZ' if maximizing else ''
            s.run(s.x[:-1] + ['xprop', '-id', s.window, '-f', '_NET_WM_STATE', '32a', '-set', '_NET_WM_STATE', state])
            s.run(s.x + ['windowsize', s.window, *size])
    checkpoint = s.logs(); s.run(s.x + ['keydown', 'w']); s.wait_active(checkpoint, 1)
    before_geometry = probe.editor_attachment(s.logs(), 'default')
    maximize(); s.wait_active(checkpoint, 0)
    probe.touch_events(recovery.tail(checkpoint, s.logs()), allow_framework_cancel=True)
    s.attach(); s.run(s.x + ['keyup', 'w'])
    maximized_geometry = probe.editor_attachment(s.logs(), 'default')
    require(maximized_geometry != before_geometry, 'Default maximize did not change fitted geometry')
    fresh = s.logs(); s.run(s.x + ['key', 'v']); s.wait_active(fresh, 0)
    probe.touch_events(recovery.tail(fresh, s.logs()))
    maximize(); time.sleep(.5); s.attach()
    require(probe.editor_attachment(s.logs(), 'default') != maximized_geometry, 'Default restore did not change geometry')
    require(s.profile() == saved, 'Default resize changed saved profile')
    return ['marker-pixels-and-playback-continuity', 'controller-editor-cancel-and-suppression',
            'maximize-held-contact-cancellation', 'resize-fresh-input-and-restore']


def disconnect_hwc(s):
    service = 'vendor.hwcomposer-2-1'
    executable = '/vendor/bin/hw/android.hardware.graphics.composer@2.1-service'
    pid = s.run(s.guest + ['/system/bin/pidof', executable]).strip()
    require(pid.isdecimal() and int(pid) > 1, 'Ambiguous fixture HWC process')
    command = s.run(s.guest + ['/system/bin/cat', '/proc/' + pid + '/cmdline'])
    require(command.split('\0')[0] == executable, 'Unexpected fixture HWC executable')
    s.run(s.guest + ['/system/bin/stop', service])
    deadline = time.monotonic() + 3
    while s.run(s.guest + ['/system/bin/getprop', 'init.svc.' + service]).strip() != 'stopped':
        if time.monotonic() >= deadline:
            # Stock HWC may remain in its graceful stopping handler while
            # Binder is alive. Exercise an actual abrupt disconnect instead.
            require(s.run(s.guest + ['/system/bin/cat', '/proc/' + pid + '/cmdline']) == command,
                    'Fixture HWC process changed before crash injection')
            s.run(s.guest + ['/system/bin/kill', '-KILL', pid])
            break
        time.sleep(.1)
    deadline = time.monotonic() + 10
    while s.run(s.guest + ['/system/bin/getprop', 'init.svc.' + service]).strip() != 'stopped':
        require(time.monotonic() < deadline, 'Fixture HWC did not actually stop'); time.sleep(.1)


def exercise(root, value, request, start, stop, weston, record):
    start('controls'); request('refresh')
    s = Session(root, value, request, weston); s.install(root)
    # Match the supported stock per-app desktop mode, including userdata from
    # an earlier fixture boot that persisted the stock false default.
    s.run(s.guest + ['/system/bin/setprop', 'persist.waydroid.multi_windows', 'true'])
    require(s.run(s.guest + ['/system/bin/getprop', 'persist.waydroid.multi_windows']).strip() == 'true',
            'Default fixture did not enable stock multi-window mode')
    s.prepare()
    s.run(s.guest + ['/system/bin/logcat', '-G', '16M'])
    results = delivery(s)
    results += window_lifecycle(s)
    s.prepare(); s.fresh('v3-three-contacts'); results.append('v3-three-contacts')
    checkpoint = s.hold()
    disconnect_hwc(s)
    rows = s.wait_active(checkpoint, 0, timeout=15)
    probe.touch_events(rows, allow_framework_cancel=True)
    require(any(' ACTION_CANCEL ' in row for row in rows), 'Default disconnect did not cancel contacts')
    (s.output / 'disconnect.log').write_text('\n'.join(rows) + '\n')
    s.run(s.guest + ['/system/bin/start', 'vendor.hwcomposer-2-1'])
    # A deliberate Stop disables init's automatic onrestart action. Reproduce
    # the stock HWC contract explicitly when bringing the framework back.
    s.run(s.guest + ['/system/bin/setprop', 'ctl.restart', 'surfaceflinger'])
    deadline = time.monotonic() + 60
    while True:
        try:
            request('refresh')
            s.request('app_action', action='force_stop', package=probe.PACKAGE)
            s.request('app_action', action='launch', package=probe.PACKAGE); s.attach(); break
        except (RuntimeError, subprocess.CalledProcessError):
            require(time.monotonic() < deadline, 'Default reconnect failed'); time.sleep(1)
    checkpoint = s.logs(); s.release(); time.sleep(.3)
    probe.require_no_mapped_contact(recovery.tail(checkpoint, s.logs()), 'Default reconnect replayed contacts')
    s.fresh('reconnect-three-contacts'); results += ['disconnect-cancels', 'reconnect-fresh-input']
    old_generation = s.m['generation']; saved = s.profile(); s.hold()
    stop(); start('controls-restart'); request('refresh')
    fresh = Session(root, value, request, weston)
    require(fresh.m['generation'] != old_generation and fresh.identity != s.identity, 'Default generation did not change')
    require(fresh.profile() == saved, 'Default full restart lost saved map')
    fresh.request('app_action', action='launch', package=probe.PACKAGE); fresh.attach()
    checkpoint = fresh.logs(); fresh.release(); time.sleep(.3)
    probe.require_no_mapped_contact(recovery.tail(checkpoint, fresh.logs()), 'Default full restart replayed contacts')
    fresh.fresh('restart-three-contacts'); results += ['restart-no-replay', 'restart-fresh-input', 'profile-persistence']
    stop()
    summary = dict(passed=True, backend=value.get('controls_backend', 'wayland'), runtime='default', scenarios=results,
                   cursor_mode='subsurface' if s.nested else 'compositor',
                   package_sha256=value['package_sha256'], images=value['images'])
    (fresh.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    record('complete', clean_stock_stop=True, default_controls=summary)
