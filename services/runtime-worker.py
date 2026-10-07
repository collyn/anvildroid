#!/usr/bin/python3
"""One persistent headless Android instance; internal root-only worker protocol."""
import argparse
import base64
import fcntl
import importlib.util
import json
import re
import os
from pathlib import Path
import signal
import stat
import hashlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
_format_spec = importlib.util.spec_from_file_location('runtime_image_format', Path(__file__).with_name('runtime-image-format.py'))
image_format = importlib.util.module_from_spec(_format_spec)
_format_spec.loader.exec_module(image_format)



spec = importlib.util.spec_from_file_location('runtime_desktop', Path(__file__).with_name('runtime-desktop.py'))
desktop_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop_module)

spec = importlib.util.spec_from_file_location('runtime_workarea', Path(__file__).with_name('runtime-workarea.py'))
workarea = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workarea)


spec = importlib.util.spec_from_file_location('runtime_resources', Path(__file__).with_name('runtime-resources.py'))
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)

spec = importlib.util.spec_from_file_location('runtime_transfer', Path(__file__).with_name('runtime-transfer.py'))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)

spec = importlib.util.spec_from_file_location('runtime_linux', Path(__file__).with_name('runtime-linux.py'))
linux = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linux)
run, require = linux.run, linux.require

spec = importlib.util.spec_from_file_location('runtime_gpu', Path(__file__).with_name('runtime-gpu.py'))
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)

spec = importlib.util.spec_from_file_location('runtime_arm', Path(__file__).with_name('runtime-arm.py'))
arm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arm)

spec = importlib.util.spec_from_file_location('runtime_path_guard', Path(__file__).with_name('runtime-path-guard.py'))
path_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(path_guard)

spec = importlib.util.spec_from_file_location('runtime_network', Path(__file__).with_name('runtime-network.py'))
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


def atomic(path, value):
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def prepare_overlay_upper(path):
    # overlayfs takes the merged root's permissions from upperdir. The root
    # service's private umask must not make Android / or /vendor root-only.
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o755)


def ensure_binder_filesystem():
    """Load Binder and mount BinderFS when the controller came from systemd."""
    if shutil.which('modprobe'):
        result = subprocess.run(['modprobe', 'binder_linux'], capture_output=True, text=True)
        if result.returncode and 'binder' not in Path('/proc/filesystems').read_text(errors='replace'):
            detail = (result.stderr or result.stdout).strip()[-300:]
            raise RuntimeError('Binder kernel module is unavailable; load binder_linux before starting the runtime.' +
                               (f' ({detail})' if detail else ''))
    filesystems = Path('/proc/filesystems').read_text(errors='replace')
    if not any(line.split()[-1:] == ['binder'] for line in filesystems.splitlines()):
        raise RuntimeError('BinderFS is unavailable in the running kernel; enable CONFIG_ANDROID_BINDERFS.')
    mountinfo = Path('/proc/self/mountinfo').read_text(errors='replace')
    if not any(' - binder ' in line and ' /dev/binderfs ' in line for line in mountinfo.splitlines()):
        binderfs = Path('/dev/binderfs')
        binderfs.mkdir(mode=0o755, exist_ok=True)
        result = subprocess.run(['mount', '-t', 'binder', 'binder', str(binderfs)], capture_output=True, text=True)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()[-300:]
            raise RuntimeError('Cannot mount BinderFS; check kernel Binder support and privileges.' +
                               (f' ({detail})' if detail else ''))
    return Path('/dev/binderfs')


_label_cache = {}
_catalog_cache = {}


def launcher_catalog(instance, identifier, packages):
    key = str(instance)
    cached = _catalog_cache.get(key)
    if cached and cached[1] == packages and time.monotonic() - cached[0] < 15:
        return cached[2]
    def lxc(tool, name, *args, **kwargs):
        kwargs.pop('bounded_output', None)
        return linux.run_bounded(tool, '-P', instance / 'android', '-n', name, *args,
                                 output_limit=1024 * 1024, full_output=True, **kwargs)
    try:
        environment = shutdown_environment(lxc, identifier)
        output = lxc('lxc-attach', identifier, *environment, '--set-var',
                     'CLASSPATH=/data/local/tmp/anvildroid-shutdown.jar', '--',
                     '/system/bin/app_process', '/system/bin', 'org.anvildroid.runtime.RuntimeCatalog',
                     timeout=2)
        catalog = json.loads(output)
        require(isinstance(catalog, dict) and len(catalog) <= 10000, 'Invalid app catalog')
        result = {}
        remaining = 600000
        for package in packages:
            entry = catalog.get(package)
            if not isinstance(entry, dict): continue
            app = {}
            label = entry.get('label')
            if isinstance(label, str) and 0 < len(label.strip()) <= 512:
                app['label'] = label.strip()
            icon = entry.get('icon_data')
            if isinstance(icon, str) and 0 < len(icon) <= min(48000, remaining):
                try:
                    png = base64.b64decode(icon, validate=True)
                    if (len(png) >= 33 and png[:16] == b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                            and struct.unpack('>II', png[16:24]) == (96, 96)):
                        app['icon_data'] = icon
                        remaining -= len(icon)
                except ValueError:
                    pass
            result[package] = app
        _catalog_cache[key] = (time.monotonic(), set(packages), result)
        return result
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        return {}  # Older images/helpers retain the Binder/cache fallback.


def launcher_labels(instance, identifier):
    key = str(instance)
    cached = _label_cache.get(key)
    if cached and time.monotonic() - cached[0] < 60: return cached[1]
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('runtime-labels.py')),
                                 str(instance / 'android' / identifier / 'binder/binder')],
                                capture_output=True, text=True, timeout=.7)
        labels = json.loads(result.stdout) if result.returncode == 0 else {}
        if not isinstance(labels, dict) or len(labels) > 10000: labels = {}
        labels = {package: label for package, label in labels.items()
                  if isinstance(package,str) and isinstance(label,str) and 0 < len(label) <= 512}
    except (OSError, ValueError, subprocess.TimeoutExpired): labels = {}
    _label_cache[key] = (time.monotonic(), labels)
    return labels


def app_execution_states(instance, identifier):
    """One bounded ActivityManager snapshot, not the container's running flag.

    packageList includes custom-named/shared processes; ps process-name matching
    would incorrectly mark those packages stopped. Cached processes are reported
    as background, not proof of an open window or a responsive application.
    """
    try:
        output = linux.run_bounded('lxc-attach', '-P', instance / 'android', '-n', identifier,
                                  '--', '/system/bin/dumpsys', 'activity', 'processes',
                                  timeout=1.5, output_limit=2 * 1024 * 1024,
                                  full_output=True, operation='App process snapshot')
        require('ACTIVITY MANAGER RUNNING PROCESSES' in output, 'Invalid process snapshot')
        states = {}
        seen = False
        for block in re.split(r'(?m)^\s*\*APP\* ', output)[1:]:
            match = re.search(r'(?m)^\s*packageList=\{([^}]+)\}', block)
            if not match:
                continue
            seen = True
            foreground = bool(re.search(r'(?m)^\s*foregroundActivities=true\b', block))
            for package in match.group(1).split(','):
                package = package.strip()
                require(re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', package), 'Invalid process package')
                if foreground or package not in states:
                    states[package] = 'foreground' if foreground else 'background'
        require(seen, 'Incomplete process snapshot')
        return states
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None  # Unknown is not stopped, and must not hide the inventory.


def list_apps(instance, identifier, single_display=False):
    if single_display:
        return [dict(package='org.anvildroid.desktop', label='Android',
                     launchable=True, desktop_entry=True)]
    # Fixed argv inside this worker's private namespaces; no shell or caller command.
    output = run('lxc-attach', '-P', instance / 'android', '-n', identifier,
                 '--', '/system/bin/cmd', 'package', 'query-activities', '--components',
                 '--user', '0', '-a', 'android.intent.action.MAIN',
                 '-c', 'android.intent.category.LAUNCHER',
                 timeout=2, umask=0o022)
    packages = set()
    for line in output.splitlines():
        if line == 'No activities found':
            require(output.strip() == line, 'Android returned an invalid launcher list')
            continue
        match = re.fullmatch(r'([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+', line)
        require(match is not None, 'Android returned an invalid launcher list')
        packages.add(match.group(1))
    require(len(packages) <= 10000, 'Android launcher list too large')
    catalog = launcher_catalog(instance, identifier, packages) if packages else {}
    labels = launcher_labels(instance, identifier) if packages - catalog.keys() else {}
    return [dict(package=package, launchable=True,
                 **catalog.get(package, {'label': labels[package]} if package in labels else {}))
            for package in sorted(packages)]


GOOGLE_PACKAGES = ('com.android.vending', 'com.google.android.gms', 'com.google.android.gsf')


def google_services(instance, identifier):
    # One bounded, read-only query includes services that have no launcher activity.
    output = run('lxc-attach', '-P', instance / 'android', '-n', identifier, '--',
                 '/system/bin/pm', 'list', 'packages', '--user', '0', timeout=2, umask=0o022)
    installed = set()
    for line in output.splitlines():
        match = re.fullmatch(r'package:([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)', line)
        require(match is not None, 'Android returned an invalid package inventory')
        installed.add(match.group(1))
    require(bool(installed) and len(installed) <= 10000, 'Android package inventory unavailable')
    android_id = None
    id_error = None
    if 'com.google.android.gsf' in installed:
        try:
            value = run('lxc-attach', '-P', instance / 'android', '-n', identifier, '--',
                        '/system/bin/sh', '-c',
                        "for db in /data/data/com.google.android.gsf/databases/gservices.db; do [ -f \"$db\" ] && sqlite3 -readonly \"$db\" \"select value from main where name='android_id';\"; done",
                        timeout=2, umask=0o022).strip()
            if re.fullmatch(r'[0-9]{1,20}', value): android_id = value
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            pass  # GSF may not have initialized its ID yet; package detection still works.
        if android_id is None:
            try:
                android_id = linux.read_gsf_id(instance / 'android' / identifier / 'data')
                if android_id is None: id_error = 'GSF has not created an Android ID. Open Play Store, wait, then check again.'
            except RuntimeError as error:
                id_error = str(error)
    return {'packages': {package: package in installed for package in GOOGLE_PACKAGES},
            'gsf_android_id': android_id, 'gsf_id_error': id_error}


def show_android_ui(instance, identifier):
    """Keep installer/verification activities in their own desktop task windows.

    A full-UI Waydroid window exposes the entire reserved Android canvas and
    can put centered security dialogs off-screen. active_apps is a mode
    selector here, not a request to launch or approve any application.
    """
    def android(*args):
        return run('lxc-attach', '-P', instance / 'android', '-n', identifier, '--',
                   '/system/bin/' + args[0], *args[1:], timeout=8, umask=0o022)
    active = android('getprop', 'waydroid.active_apps').strip()
    if active in ('', 'none', 'Waydroid'):
        android('setprop', 'waydroid.active_apps', 'org.anvildroid.install')
    # Do not change immersive policy, verification settings, or click a dialog.


def install_apk(instance, identifier, fd, desktop=False):
    size = transfer.apk_size(fd)
    before = os.fstat(fd)
    with tempfile.TemporaryFile(dir=instance) as snapshot:
        offset = 0
        while offset < size:
            block = os.pread(fd, min(1024 * 1024, size - offset), offset)
            require(bool(block), 'APK changed while reading; retry')
            snapshot.write(block)
            offset += len(block)
        after = os.fstat(fd)
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'APK changed while reading; retry')
        snapshot.seek(0)
        if desktop:
            show_android_ui(instance, identifier)
        # No positional path selects stdin; a bare dash is parsed as an option on Android 13.
        try:
            output = linux.run_bounded('lxc-attach', '-P', instance / 'android', '-n', identifier,
                                       '--', '/system/bin/pm', 'install', '-r', '--user', '0', '-S', size,
                                       stdin=snapshot, timeout=300, umask=0o022, operation='APK installation')
        except RuntimeError as error:
            if 'command timed out' in str(error):
                raise RuntimeError('Android did not confirm installation within 5 minutes. It may still be optimizing the app or waiting for verification. Check the Android window and installed app list before retrying; the installation outcome is unknown.') from error
            raise
    require(any(line.strip() == 'Success' for line in output.splitlines())
            and not re.search(r'Failure|Error|INSTALL_FAILED', output), 'APK installation failed: ' + (output[:900] or 'Android returned no success result'))
    _label_cache.pop(str(instance), None)
    _catalog_cache.pop(str(instance), None)
    return {'action': 'install', 'success': True, 'message': 'APK installed successfully in this runtime'}


def keymap_profiles(instance, identifier):
    script = ('n=0; for f in /data/waydroid_tmp/anvildroid/keymaps/*; do '
              '[ -f "$f" ] && [ ! -L "$f" ] || continue; '
              'n=$((n+1)); [ "$n" -le 128 ] || exit 2; '
              'printf "BEGIN\\n"; head -c 4096 "$f"; printf "END\\n"; done')
    output = linux.run_bounded('lxc-attach', '-P', instance/'android', '-n', identifier, '--uid','1000','--gid','1000','--',
                              '/system/bin/sh', '-c', script, timeout=4, operation='Keymap inventory', output_limit=512*1024, full_output=True)
    return parse_keymaps(output, identifier)


def parse_keymaps(output, identifier):
    require(len(output.encode()) <= 512*1024, 'Keymap inventory exceeds limit')
    profiles=[]; errors=0
    for block in output.split('BEGIN\n')[1:]:
        try:
            lines=block.splitlines(); header=lines[0].split()
            require(len(header)==5 and header[:2]==['ANVIL_KEYMAP','1'] and header[2]==identifier, 'Invalid keymap scope')
            package=header[3]; count=int(header[4])
            require(re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+',package) and len(package)<=240 and 0<=count<=64, 'Invalid keymap')
            require(len(lines)==count+2 and lines[-1]=='END', 'Incomplete keymap')
            bindings=[];keys=set()
            for index,line in enumerate(lines[1:-1]):
                key,x,y,hold=map(int,line.split())
                require(2<=key<=767 and key not in keys and 0<=x<=65535 and 0<=y<=65535 and hold in (0,1), 'Invalid binding')
                keys.add(key);bindings.append({'id':f'point-{index}','evdev':key,'nx':x,'ny':y,'kind':'hold' if hold else 'tap'})
            if count: profiles.append({'runtime':identifier,'package':package,'bindings':bindings,'source':'runtime','active':False})
        except (ValueError,RuntimeError,IndexError): errors+=1
    return {'runtime_id':identifier,'profiles':profiles,'invalid_count':errors}


def open_keymap(instance, identifier, generation, package, android):
    require(isinstance(generation,str) and re.fullmatch(r'[0-9a-f]{32}',generation), 'Missing runtime generation')
    require(isinstance(package,str) and len(package)<=240 and re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+',package), 'Invalid keymap package')
    tasks=[]
    for line in android('cmd','activity','stack','list').splitlines():
        match=re.search(r'taskId=(\d+): ([^ /]+)/\S+ .*visible=true\b',line)
        if match and match[2]==package: tasks.append(int(match[1]))
    require(len(tasks)==1, 'Open exactly one window of this app before editing keymap')
    require(len(package)<=240,'Package name exceeds editor limit')
    nonce=uuid.uuid4().hex
    command=f'1 {nonce} {generation} {tasks[0]} {package}'
    # Every interpolated value is validated above; paths and shell program are fixed.
    temporary=f'keymap-request-{nonce}.tmp'
    script=('set -C; umask 077; cd /data/waydroid_tmp/anvildroid || exit 1; '
            f'printf "%s\\n" "{command}" > {temporary} && mv {temporary} keymap-request')
    # Never perform a root write/chown through guest-controlled pathnames.
    run('lxc-attach','-P',instance/'android','-n',identifier,'--uid','1000','--gid','1000',
        '--','/system/bin/sh','-c',script,timeout=3,umask=0o077)
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        reply=android('sh','-c','head -c 160 /data/waydroid_tmp/anvildroid/keymap-reply 2>/dev/null || true').split()
        if len(reply)==2 and reply[0]==nonce:
            require(reply[1] in ('editing','saved','cancelled'),'Overlay refused: finish another edit, release resize, or check profile data')
            message=('Overlay attached. Click the app, then Add → place point → press key. Save or X/Escape. Editing only; no touch injection.'
                     if reply[1]=='editing' else 'Overlay '+reply[1]+'.')
            return {'action':'keymap_edit','package':package,'success':True,'message':message}
        time.sleep(.05)
    raise RuntimeError('Overlay did not acknowledge: update/restart this runtime and keep its app window open')


def app_exit_snapshot(instance, identifier, package):
    try:
        output = linux.run_bounded('lxc-attach', '-P', instance / 'android', '-n', identifier,
                                   '--', '/system/bin/dumpsys', 'activity', 'exit-info', package,
                                   timeout=1.5, output_limit=128 * 1024, full_output=True,
                                   operation='App exit status')
        require('ACTIVITY MANAGER PROCESS EXIT INFO' in output, 'Exit status unavailable')
        return parse_app_exits(output, package)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None


def parse_app_exits(output, package):
    entries = {}
    for block in re.split(r'ApplicationExitInfo #\d+:', output)[1:]:
        identity = re.search(r'timestamp=(\S+ \S+) pid=(\d+).*?\buser=(\d+)', block)
        reason = re.search(r'process=(\S+) reason=(\d+) \([^)]*\).*?status=(\d+)', block)
        if not identity or not reason or identity[3] != '0' or reason[1] != package:
            continue
        entries[(identity[1], identity[2])] = (int(reason[2]), int(reason[3]))
    return entries


def check_app_startup(instance, identifier, package, before):
    if before is None: return
    time.sleep(3)
    after = app_exit_snapshot(instance, identifier, package)
    if after is None: return
    for identity in after.keys() - before.keys():
        reason, status = after[identity]
        if reason in (4, 5, 6) or (reason == 2 and status in (4, 6, 7, 8, 11)):
            detail = {4: 'SIGILL', 6: 'SIGABRT', 7: 'SIGBUS', 8: 'SIGFPE', 11: 'SIGSEGV'}.get(status, 'Android crash')
            raise RuntimeError(f'{package} crashed during startup ({detail}). Android is still running. '
                               'This app may be incompatible with the runtime image or ARM translator; '
                               'check runtime diagnostics. Repeated launches will not repair it.')


def app_action(instance, identifier, action, package, desktop=False, generation=None, single_display=False):
    require(action in ('launch', 'force_stop', 'uninstall', 'keymap_edit', 'full_ui'), 'Unsupported app action')
    require(isinstance(package, str) and len(package) <= 255 and
            re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', package), 'Invalid package')
    require(not (desktop and single_display) or action == 'full_ui',
            'This image uses one Android window. Open Android to manage and launch its apps.')
    def android(*args):
        return run('lxc-attach', '-P', instance / 'android', '-n', identifier, '--',
                   '/system/bin/' + args[0], *args[1:], timeout=8, umask=0o022, include_stderr=True)
    def refresh_single_display():
        if single_display:
            # Match Waydroid CLI showFullUI: changing active_apps alone does
            # not invalidate HWC after its host window has been closed.
            try:
                android('cmd', 'statusbar', 'expand-notifications')
                time.sleep(0.5)
            except RuntimeError as error:
                # Some custom images omit StatusBarManagerService entirely.
                # Refreshing it is only a compositor nudge, never a boot
                # requirement; do not turn a usable Android window into a
                # launch loop just because this optional service is absent.
                if "Can't find service: statusbar" not in str(error):
                    raise
            finally:
                try:
                    android('cmd', 'statusbar', 'collapse')
                except RuntimeError as error:
                    if "Can't find service: statusbar" not in str(error):
                        raise
    if action == 'full_ui':
        require(desktop, 'Waydroid window requires Desktop mode')
        require(package == 'org.anvildroid.desktop', 'Invalid desktop target')
        android('input', 'keyevent', '224')  # KEYCODE_WAKEUP, never toggle power.
        android('setprop', 'waydroid.active_apps', 'Waydroid')
        # Match Waydroid's app-manager path: a background session can keep
        # SurfaceFlinger layers alive without publishing a desktop window.
        android('setprop', 'waydroid.background_start', 'false')
        android('settings', 'put', 'secure', 'policy_control', 'null*')
        # `active_apps` alone is only a mode hint. Launching HOME creates the
        # actual Android task that Waydroid HWC publishes as its full window.
        android('am', 'start', '--user', '0', '-a',
                'android.intent.action.MAIN', '-c', 'android.intent.category.HOME')
        refresh_single_display()
        return {'action': action, 'package': package, 'message': ('Full Android window requested for this runtime.' if single_display else 'Full Android window requested for this runtime. Opening an individual app returns to app-window mode.')}
    # Only launcher apps are actionable here; framework/service packages are excluded.
    resolved = android('cmd', 'package', 'resolve-activity', '--brief', '--user', '0',
                       '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', '-p', package)
    components = [line for line in resolved.splitlines()
                  if re.fullmatch(re.escape(package) + r'/[A-Za-z0-9_.$]+', line)]
    require(len(components) == 1, 'No launcher activity for this package; action refused')
    if action=='keymap_edit':
        require(desktop,'Keymap overlay requires Desktop mode')
        return open_keymap(instance,identifier,generation,package,android)
    if action == 'uninstall':
        third_party = android('pm', 'list', 'packages', '-3', '--user', '0')
        require('package:' + package in third_party.splitlines(), 'Cannot uninstall a system or protected app')
        output = linux.run_bounded('lxc-attach', '-P', instance / 'android', '-n', identifier,
                                   '--', '/system/bin/pm', 'uninstall', '--user', '0', package,
                                   timeout=15, umask=0o022, operation='APK uninstall')
        require(output.strip() == 'Success', 'Android could not uninstall app: ' + output[-512:])
        remaining = android('pm', 'list', 'packages', '--user', '0', package)
        require('package:' + package not in remaining.splitlines(), 'Android still reports the app installed; refresh before retrying')
        _label_cache.pop(str(instance), None)
        _catalog_cache.pop(str(instance), None)
        return {'action': action, 'package': package, 'message': 'App uninstalled from this runtime. Android confirmed removal.'}
    if action == 'launch':
        if desktop:
            android('input', 'keyevent', '224')
        # This compatibility path has no usable task/layer metadata. Its HWC
        # can present the complete framebuffer, but app-window mode hides it.
        android('setprop', 'waydroid.active_apps', 'Waydroid' if single_display else package)
        android('setprop', 'waydroid.background_start', 'false')
    if action == 'launch':
        exits_before = app_exit_snapshot(instance, identifier, package)
        window_args = []
        if desktop and not single_display:
            sdk = android('getprop', 'ro.build.version.sdk').strip()
            if sdk.isdecimal() and int(sdk) >= 36:
                # Android 16 otherwise launches fullscreen tasks, whose bounds
                # cannot be changed by the host-window resize bridge.
                window_args = ['--windowingMode', '5']
        output = android('am', 'start', '--user', '0', *window_args, '-n', components[0])
        # Waydroid's launcher applies immersive policy after starting the
        # activity. Without this, newer custom images keep the task in the
        # Android display stack while HWC reports no published app window.
        policy = 'immersive.full=*' if android('getprop', 'persist.waydroid.multi_windows').strip() == 'true' else 'immersive.status=*'
        android('settings', 'put', 'secure', 'policy_control', policy)
        refresh_single_display()
    else:
        output = android('am', 'force-stop', '--user', '0', package)
    # Preserve the beginning of ActivityManager's diagnostic; it contains the
    # actual exception (the old tail-only slice often started mid stack trace).
    diagnostic = output.strip()
    require(not re.search(r'(?im)^(?:error|exception|securityexception|java\.).*', diagnostic),
            'Android rejected app action: ' + diagnostic[:2048])
    if action == 'launch':
        check_app_startup(instance, identifier, package, exits_before)
    return {'action': action, 'package': package, 'message': ('Android accepted launch in desktop preview.' if desktop else 'Android accepted launch (headless; no desktop window).') if action == 'launch' else 'Android accepted force-stop.'}


def pin_desktop_socket(path, uid):
    path = Path(path)
    require(uid > 0 and path.parent == Path('/run/user') / str(uid) and
            re.fullmatch(r'(?:wayland|anvildroid-gnome)-[A-Za-z0-9_.-]+', path.name) and path.resolve() == path,
            'Desktop socket must be inside the selected user runtime directory')
    require(path.parent.stat().st_uid == uid, 'Desktop directory owner mismatch')
    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == uid, 'Desktop socket owner/type mismatch')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            probe.connect(str(path))
            _, peer, _ = struct.unpack('3i', probe.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            current = path.stat()
            require(peer == uid and (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), 'Desktop compositor identity changed')
            protocols = desktop_module.registry_protocols(probe)
            desktop_module.require_window_controls(protocols)
            current = path.stat()
            require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), 'Desktop compositor identity changed during protocol probe')
        return fd
    except Exception:
        os.close(fd)
        raise


def pin_audio_socket(uid):
    """Pin only the selected desktop user's Pulse socket before /run is hidden."""
    require(uid > 0, 'Audio requires a desktop owner')
    directory = Path('/run/user') / str(uid)
    path = directory / 'pulse/native'
    if not path.exists():
        return None
    require(path.resolve() == path and directory.stat().st_uid == uid and
            path.parent.stat().st_uid == uid, 'Audio socket directory owner/path mismatch')
    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == uid, 'Audio socket owner/type mismatch')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            probe.connect(str(path))
            _, peer, _ = struct.unpack('3i', probe.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            current = path.stat()
            require(peer == uid and (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino),
                    'Audio server identity changed')
        return fd
    except Exception:
        os.close(fd)
        raise


def android_overlay_options(lower, upper, work):
    # Bionic Android 13 realpath stores ino_t in 32 bits in 32-bit HALs.
    # Overlay xino synthesizes high inode bits and makes real files fail ENOENT.
    return f'lowerdir={lower},upperdir={upper},workdir={work},xino=off'


def nested_weston(xdg, width, height, display, authority):
    """argv + env for a nested Weston inside the user's X session; socket lands in xdg."""
    args = ['weston', '--backend=x11', '--renderer=gl', '--socket=wayland-probe',
            '--idle-time=0', '--no-config', f'--width={width}', f'--height={height}']
    env = {k: v for k, v in os.environ.items()
           if k not in ('DBUS_SESSION_BUS_ADDRESS', 'WAYLAND_DISPLAY', 'PULSE_SERVER', 'DISPLAY', 'XAUTHORITY')}
    env['XDG_RUNTIME_DIR'] = str(xdg)
    env['DISPLAY'] = display  # from the validated token, never inherited
    if authority is not None:
        env['XAUTHORITY'] = str(authority)
    return args, env


def start_weston(args, env, log, xdg, compositors, *, nested=False):
    """Track only the current compositor; retain it for cleanup even on failure."""
    args = list(args)
    process = subprocess.Popen(args, env=env, stdout=log, stderr=log)
    compositors.append(process)
    for _ in range(100):
        # Check exit before the socket: a failed renderer may leave a stale socket.
        if process.poll() is not None:
            if nested and '--renderer=gl' in args:
                for name in ('wayland-probe', 'wayland-probe.lock'):
                    (xdg / name).unlink(missing_ok=True)
                args[args.index('--renderer=gl')] = '--renderer=pixman'
                process = subprocess.Popen(args, env=env, stdout=log, stderr=log)
                compositors[-1] = process
                continue
            raise RuntimeError('Nested Weston exited' if nested else 'Headless Weston exited')
        if (xdg / 'wayland-probe').is_socket():
            return process
        time.sleep(.05)
    raise RuntimeError('Nested Weston socket missing' if nested else 'Headless Weston socket missing')


def configure_host_desktop(instance, shell, desktop):
    """Keep Android 16's desktop organizer from competing with Waydroid HWC."""
    sdk = shell('getprop', 'ro.build.version.sdk').strip()
    if not sdk.isdecimal() or int(sdk) < 36:
        return
    name = 'com.android.shell:AnvilDroidHostDesktop'
    marker = instance / 'host-desktop-overlay'
    changed = False
    if desktop:
        resource = 'android:bool/config_isDesktopModeSupported'
        current = shell('cmd', 'overlay', 'lookup', '--user', '0', 'android', resource).strip()
        if current != 'false':
            shell('cmd', 'overlay', 'fabricate', '--target', 'android', '--name',
                  'AnvilDroidHostDesktop', resource, '0x12', '0')
            shell('cmd', 'overlay', 'enable', '--user', '0', name)
            require(shell('cmd', 'overlay', 'lookup', '--user', '0', 'android', resource).strip() == 'false',
                    'Could not configure Android 16 for host-managed app windows')
            marker.write_text(name)
            changed = True
        elif name in shell('cmd', 'overlay', 'list', '--user', '0', 'android'):
            marker.write_text(name)
    elif marker.exists():
        shell('cmd', 'overlay', 'disable', '--user', '0', name)
        marker.unlink()
        changed = True
    if changed:
        # SystemUI caches its desktop organizer at process startup. This runs
        # before the runtime becomes available for app launch, once per mode.
        try:
            pids = shell('pidof', 'com.android.systemui').split()
        except RuntimeError:
            pids = []
        for pid in pids:
            if pid.isdecimal() and int(pid) > 1:
                shell('kill', '-TERM', pid)


def configure_desktop_ime(instance, work, shell, desktop):
    component = 'org.anvildroid.ime/.BridgeIme'
    previous_path = instance / 'desktop-previous-ime'
    if desktop:
        apk = instance / 'desktop-ime.apk'
        digest = hashlib.sha256(apk.read_bytes()).hexdigest()
        version = instance / 'desktop-ime.sha256'
        installed = shell('pm', 'path', 'org.anvildroid.ime').startswith('package:')
        # A previous PackageInstaller call can finish after lxc-attach has
        # timed out. If Android already exposes the package, trust that
        # completed install and record the marker instead of reinstalling on
        # every boot.
        if installed and (not version.exists() or version.read_text() != digest):
            version.write_text(digest)
        if not installed:
            target = work / 'data/local/tmp/anvildroid-desktop-ime.apk'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(apk, target)
            target.chmod(0o644)
            try:
                result = shell('pm', 'install', '-r', '/data/local/tmp/anvildroid-desktop-ime.apk', timeout=30)
                require('Success' in result, 'Could not install desktop IME: ' + result)
            except subprocess.TimeoutExpired as error:
                # PackageInstaller may still be reading the APK after the
                # shell client exits. Continue boot; the keyboard remains
                # available and the next boot will reuse a completed install.
                raise RuntimeError('Desktop IME installation did not finish within 30 seconds; Android keyboard remains available.') from error
            else:
                target.unlink(missing_ok=True)
            version.write_text(digest)
        current = shell('settings', 'get', 'secure', 'default_input_method')
        if current != component:
            require(re.fullmatch(r'[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+', current), 'Cannot record the previous Android keyboard')
            previous_path.write_text(current)
        # Package installation returns before InputMethodManager consumes PACKAGE_ADDED.
        deadline = time.monotonic() + 20
        while component not in shell('ime', 'list', '-a', '-s').splitlines():
            require(time.monotonic() < deadline, 'Desktop IME was installed but Android did not register it')
            time.sleep(.25)
        shell('ime', 'enable', component)
        shell('ime', 'set', component)
        require(shell('settings', 'get', 'secure', 'default_input_method') == component, 'Desktop IME could not be selected')
    elif previous_path.exists():
        previous = previous_path.read_text()
        require(re.fullmatch(r'[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+', previous), 'Invalid previous keyboard record')
        if shell('settings', 'get', 'secure', 'default_input_method') == component:
            shell('ime', 'set', previous)
            require(shell('settings', 'get', 'secure', 'default_input_method') == previous, 'Previous Android keyboard could not be restored')
        previous_path.unlink()


def shutdown_environment(lxc, identifier):
    data = lxc('lxc-attach', identifier, '--clear-env', '--', '/system/bin/head', '-c', '16385',
               '/data/system/environ/classpath', timeout=2, bounded_output=True)
    require(len(data.encode()) <= 16384, 'Android classpath exceeds limit')
    values = [line.removeprefix('export BOOTCLASSPATH ') for line in data.splitlines() if line.startswith('export BOOTCLASSPATH ')]
    require(len(values) == 1 and all(re.fullmatch(r'/(?:apex|system)/[A-Za-z0-9_./-]+\.jar', path) and '..' not in path.split('/') for path in values[0].split(':')), 'Invalid Android boot classpath')
    environment = {
        'PATH': '/product/bin:/apex/com.android.runtime/bin:/apex/com.android.art/bin:/system_ext/bin:/system/bin:/system/xbin:/odm/bin:/vendor/bin:/vendor/xbin',
        'ANDROID_ROOT': '/system', 'ANDROID_DATA': '/data', 'ANDROID_STORAGE': '/storage',
        'ANDROID_ART_ROOT': '/apex/com.android.art', 'ANDROID_I18N_ROOT': '/apex/com.android.i18n',
        'ANDROID_TZDATA_ROOT': '/apex/com.android.tzdata', 'ANDROID_RUNTIME_ROOT': '/apex/com.android.runtime',
        'BOOTCLASSPATH': values[0]}
    dex = [line.removeprefix('export DEX2OATBOOTCLASSPATH ') for line in data.splitlines()
           if line.startswith('export DEX2OATBOOTCLASSPATH ')]
    if dex:
        require(len(dex) == 1 and dex[0] and all(re.fullmatch(r'/(?:apex|system)/[A-Za-z0-9_./-]+\.jar', path)
                and '..' not in path.split('/') for path in dex[0].split(':')), 'Invalid Android compiler classpath')
        environment['DEX2OATBOOTCLASSPATH'] = dex[0]
    return ['--clear-env'] + [argument for key, value in environment.items() for argument in ('--set-var', key + '=' + value)]


def stop_android(lxc, identifier, boot_completed):
    """Quiesce Android services; container teardown remains controller-owned."""
    status_error = None
    try:
        if lxc('lxc-info', identifier, '-sH', timeout=2, bounded_output=True) == 'STOPPED':
            return {'shutdown_forced': False}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        status_error = str(error)
    ready = False
    reason = 'Android state query failed: ' + status_error if status_error else 'Android did not finish booting'
    if boot_completed and status_error is None:
        try:
            phase = 'classpath'
            environment = shutdown_environment(lxc, identifier)
            phase = 'shutdown broadcast'
            broadcast = lxc('lxc-attach', identifier, *environment, '--', '/system/bin/cmd', 'activity',
                            'broadcast', '--user', 'all', '-a', 'android.intent.action.ACTION_SHUTDOWN',
                            '--receiver-foreground', '--receiver-registered-only', timeout=20, bounded_output=True)
            require('Broadcast completed:' in broadcast and not re.search(r'(?im)^(error|exception)', broadcast), 'Android shutdown broadcast failed')
            phase = 'framework flush'
            output = lxc('lxc-attach', identifier, *environment, '--set-var',
                         'CLASSPATH=/data/local/tmp/anvildroid-shutdown.jar', '--', '/system/bin/app_process',
                         '/system/bin', 'org.anvildroid.runtime.RuntimeShutdown', timeout=12, bounded_output=True)
            require('ANVILDROID_SHUTDOWN_READY' in output.splitlines(), 'Android state flush did not complete')
            lxc('lxc-attach', identifier, '--', '/system/bin/sync', timeout=3, bounded_output=True)
            ready = True
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            reason = 'Android ' + phase + ' failed: ' + str(error)[-240:]
    # Power-off/reboot syscalls are inappropriate for a Waydroid container.
    # After Android acknowledges its flush, terminate its namespace normally.
    print('Android shutdown flush:', 'ready' if ready else reason, flush=True)
    try:
        lxc('lxc-stop', identifier, '-k', '--nowait', '--nolock', timeout=5, bounded_output=True)
    except (RuntimeError, subprocess.TimeoutExpired):
        # Android can exit between the state check above and lxc-stop (for
        # example after a graphics-service crash). Treat an already stopped
        # container as success; surfacing lxc-stop's ENOT running error turns
        # a recoverable cleanup race into a permanent "Action failed" state.
        if lxc('lxc-info', identifier, '-sH', timeout=2, bounded_output=True) != 'STOPPED':
            raise
    deadline = time.monotonic() + 15
    while True:
        try:
            if lxc('lxc-info', identifier, '-sH', timeout=2, bounded_output=True) == 'STOPPED': break
        except (RuntimeError, subprocess.TimeoutExpired):
            pass  # A stalled monitor is not proof that the container exited.
        require(time.monotonic() < deadline, 'Container did not stop after Android shutdown')
        time.sleep(.1)
    result = {'shutdown_forced': not ready, 'shutdown_method': 'framework_flush' if ready else 'forced_cleanup'}
    if not ready:
        result['shutdown_warning'] = reason + '; forced cleanup was required. Recent unsaved app changes may be lost.'
    return result


def boot(instance, identifier, generation, state, stopping, done, desktop=None, host_net_fd=None):
    root = instance / 'android'
    root.mkdir(mode=0o700, exist_ok=True)
    mounts, names, compositors, logfiles = [], [], [], []
    boot_completed = False
    uplink = network.Uplink() if host_net_fd is not None else None
    def loc(name): return root / name
    def lxc(tool, name, *args, timeout=20, include_stderr=False, bounded_output=False):
        # LXC creates Android-visible device directories (including /dev/dri).
        # Keep controller files private but do not pass its 0077 mask to Android.
        if bounded_output:
            return linux.run_bounded(tool, '-P', root, '-n', name, *args, timeout=timeout, umask=0o022)
        return run(tool, '-P', root, '-n', name, *args, umask=0o022, timeout=timeout, include_stderr=include_stderr)
    def mount(*args):
        run('mount', *args)
        mounts.append(Path(args[-1]))
    def shell(*args, timeout=20): return lxc('lxc-attach', identifier, '--', '/system/bin/' + args[0], *args[1:], timeout=timeout)
    def provision_android_user():
        """Finish Android's first-run gate so desktop activities stay open.

        Custom images often ship without Setup Wizard state. ActivityManager
        accepts the launch but immediately finishes every activity while these
        flags remain unset, which looks like an app crash to the desktop user.
        Only write the flags when needed; existing runtimes with completed
        setup keep their original state.
        """
        user_ready = shell('settings', '--user', '0', 'get', 'secure', 'user_setup_complete').strip()
        if user_ready != '1':
            shell('settings', '--user', '0', 'put', 'secure', 'user_setup_complete', '1')
        device_ready = shell('settings', 'get', 'global', 'device_provisioned').strip()
        if device_ready != '1':
            shell('settings', 'put', 'global', 'device_provisioned', '1')
        # Desktop runtimes have no physical lock screen. Fresh custom images
        # can leave Keyguard showing forever, keeping every Android window
        # behind NotificationShade even though ActivityManager reports a
        # successful launch.
        shell('settings', '--user', '0', 'put', 'secure', 'lockscreen.disabled', '1')
        try:
            shell('locksettings', 'clear', '--user', '0')
            shell('wm', 'dismiss-keyguard')
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
        # Some GApps images keep Setup Wizard alive after boot and write the
        # secure flag back to 0 while desktop apps are launching. Once the
        # runtime is provisioned, stop and disable those optional receivers.
        for package in ('com.google.android.setupwizard', 'com.android.provision'):
            try:
                if shell('pm', 'path', package).strip():
                    shell('am', 'force-stop', package)
                    shell('pm', 'disable-user', '--user', '0', package)
            except (RuntimeError, subprocess.TimeoutExpired):
                # Setup Wizard is optional across Waydroid images.
                pass
        require(shell('settings', '--user', '0', 'get', 'secure', 'user_setup_complete').strip() == '1',
                'Android user setup could not be completed')
        require(shell('settings', 'get', 'global', 'device_provisioned').strip() == '1',
                'Android device provisioning could not be completed')
    try:
        state['resource_limits'] = resources.verify(identifier, generation, resources.load(instance))
        gpu_node = gpu.resolve(instance)
        state['gpu_node'] = gpu_node
        run('mount', '--make-rprivate', '/')
        run('ip', 'link', 'add', 'anvilandroid', 'type', 'bridge')
        run('ip', 'addr', 'add', '192.0.2.1/24', 'dev', 'anvilandroid')
        run('ip', 'link', 'set', 'anvilandroid', 'up')
        if uplink:
            # Start before the private /run mount hides the host DNS resolver files.
            try: uplink.start(host_net_fd)
            finally: os.close(host_net_fd); host_net_fd = None
        desktop_pin = root / 'desktop-socket'
        audio_pin = root / 'audio-socket'
        audio_available = False
        workarea_props = []
        workarea_info = {}
        x11 = {'display': None, 'authority': None}
        if desktop:
            fd = pin_audio_socket(desktop[1])
            if fd is not None:
                try:
                    audio_pin.touch(mode=0o600, exist_ok=True)
                    subprocess.run(['mount', '--no-canonicalize', '--bind', f'/proc/self/fd/{fd}', str(audio_pin)],
                                   pass_fds=(fd,), check=True, capture_output=True)
                    mounts.append(audio_pin)
                    audio_available = True
                finally:
                    os.close(fd)
            state['audio'] = {'available': audio_available, 'backend': 'pulse',
                              'detail': 'Desktop audio socket pinned' if audio_available else 'Desktop PulseAudio socket unavailable'}
            if desktop[0].startswith(desktop_module.X11_TOKEN_PREFIX):
                x11_display = desktop[0][len(desktop_module.X11_TOKEN_PREFIX):]
                require(desktop_module.parse_x11_display(x11_display) is not None, 'Invalid X11 display token')
                authority = desktop_module.x11_authority(desktop[1])
                if authority is not None:
                    # Copy before the private /run mount hides the source.
                    pinned = instance / 'x11-authority'
                    pinned.write_bytes(authority.read_bytes())
                    os.chmod(pinned, 0o600)
                    x11['authority'] = pinned  # instance dir is root 0700: safe
                x11['display'] = x11_display
            else:
                fd = pin_desktop_socket(Path('/run/user') / str(desktop[1]) / desktop[0], desktop[1])
                try:
                    desktop_pin.touch(mode=0o600, exist_ok=True)
                    subprocess.run(['mount', '--no-canonicalize', '--bind', f'/proc/self/fd/{fd}', str(desktop_pin)], pass_fds=(fd,), check=True, capture_output=True)
                    mounts.append(desktop_pin)
                finally: os.close(fd)
            # Probe the compositor workarea before the private /run mount hides
            # the session bus and Wayland socket; never fails the boot.
            workarea_props, workarea_info = workarea.boot_props([], desktop[0], desktop[1])
        mount('-t', 'tmpfs', 'tmpfs', '/run')
        sysview = root / 'sysview'
        sysview.mkdir(exist_ok=True)
        mount('-t', 'sysfs', '-o', 'ro', 'sysfs', sysview)
        for relative in ('class/net', 'devices/virtual/net'):
            mount('--bind', sysview / relative, '/sys/' + relative)
        patches = instance / 'support/overlay'
        compatibility = {}
        try:
            compatibility = json.loads((instance / 'support/compatibility.json').read_text())
        except (OSError, ValueError):
            pass
        native_wayland = compatibility.get('desktop_bridge') == 'native-wayland'
        software_compatibility = compatibility.get('software_renderer') == 'angle-pastel'
        # Custom images frequently ship a vendor composer with a different
        # ABI. Keep the untouched stock path only when the image does not
        # advertise the native Wayland contract; native-wayland images need
        # the bridge preload even when they also use ANGLE/Pastel.
        custom_stock_hwc = (compatibility.get('flavor') == 'CUSTOM'
                            and software_compatibility
                            and not native_wayland)
        if not custom_stock_hwc and not native_wayland:
            try:
                custom_stock_hwc = not any(line.startswith('waydroid.system_ota=')
                                           for line in (instance / 'support/waydroid.prop').read_text().splitlines())
            except OSError:
                pass
        if custom_stock_hwc:
            native_wayland = False
            # A custom image may ship a stock Waydroid composer together with
            # ANGLE/Pastel. Keep that software path enabled: forcing the
            # vendor GBM/DRM allocator here makes composer abort when the
            # desktop window is created on unsupported host graphics stacks.
        single_display = compatibility.get('presentation_mode',
            'android-display' if native_wayland or custom_stock_hwc else 'app-windows') == 'android-display'
        # The stock fallback disables multi-window and removes our bridge.
        # SurfaceFlinger metadata alone must not advertise app windows on it.
        single_display = single_display or custom_stock_hwc
        state['presentation_mode'] = 'android-display' if single_display else 'app-windows'
        if single_display:
            state['capabilities'] = [c for c in state['capabilities'] if c not in ('keymap_editor', 'uninstall_app')]
        software_rendering = gpu_node is None
        state['gpu_node'] = 'software' if software_rendering else gpu_node
        arm_layer = arm.active_layer(instance)
        arm_props = arm.runtime_properties(instance)
        system_patches = f'{arm_layer}:{patches}' if arm_layer else str(patches)
        for index, name in enumerate((identifier,), 2):
            work = loc(name)
            for sub in ('rootfs', 'system-lower', 'vendor-lower', 'system-upper', 'system-work',
                        'vendor-upper', 'vendor-work', 'data', 'binder', 'xdg'):
                (work / sub).mkdir(parents=True, exist_ok=True)
            for sub in ('system-upper', 'vendor-upper'):
                prepare_overlay_upper(work / sub)
            if software_compatibility:
                # WayDroidATV vendor init reads this file after boot and can
                # override waydroid.prop. Keep both sources in sync when
                # switching between hardware and software rendering.
                settings = work / 'data/misc/waydroid_settings'
                settings.parent.mkdir(parents=True, exist_ok=True)
                existing = settings.read_text(errors='replace').splitlines() if settings.exists() else []
                settings.write_text('\n'.join(gpu.properties(existing, gpu_node)) + '\n')
            fs = work / 'rootfs'
            mount(*image_format.mount_options(instance / 'prepared/system.img'), instance / 'prepared/system.img', work / 'system-lower')
            mount(*image_format.mount_options(instance / 'prepared/vendor.img'), instance / 'prepared/vendor.img', work / 'vendor-lower')
            mount('-t', 'overlay', 'overlay', '-o', android_overlay_options(f'{system_patches}:{work}/system-lower', work / 'system-upper', work / 'system-work'), fs)
            mount('-t', 'overlay', 'overlay', '-o', android_overlay_options(f'{patches}/vendor:{work}/vendor-lower', work / 'vendor-upper', work / 'vendor-work'), fs / 'vendor')
            if 'presentation_mode' not in compatibility and not custom_stock_hwc:
                single_display = b'vendor.waydroid.display@' not in (fs / 'system/bin/surfaceflinger').read_bytes()
                state['presentation_mode'] = 'android-display' if single_display else 'app-windows'
                if single_display:
                    state['capabilities'] = [c for c in state['capabilities'] if c not in ('keymap_editor', 'uninstall_app')]
            allocator_rc = Path('etc/init/android.hardware.graphics.allocator@2.0-service.rc')
            allocator_init = fs / 'vendor' / allocator_rc
            if allocator_init.exists() and 'ANVILDROID_GRALLOC_METADATA' in allocator_init.read_text():
                # Undo the previous boot's optional adapter before selecting
                # compatibility for this boot (including headless mode).
                allocator_init.write_text((work / 'vendor-lower' / allocator_rc).read_text())
            guard = path_guard.prepare(instance, fs, Path(__file__).parent) if arm_props is None or arm_props.get('ro.dalvik.vm.native.bridge') == 'libndk_translation.so' else None
            if guard:
                bridge, helper = guard
                target = fs / 'system/lib64' / path_guard.LIBRARY
                require(not target.is_symlink(), 'Invalid pathname helper mount target')
                target.touch(mode=0o644, exist_ok=True)
                mount('--bind', helper, target)
                mount('--bind', bridge, fs / 'system/lib64/libndk_translation.so')
                state['arm_path_guard'] = 'axion-android16'
            props = gpu.properties((instance / 'support/waydroid.prop').read_text().splitlines(), gpu_node)
            # Android 15/LineageOS 22 custom images can abort SurfaceFlinger
            # while priming the desktop hole-punch shader on GBM buffers:
            # "output buffer not gpu writeable". The cache is optional; skip
            # this shader so framework boot can complete and desktop windows
            # still render normally after startup.
            shader_cache_keys = (
                'solid_layers', 'clipped_layers', 'image_dimmed_layers',
                'shadow_layers', 'hole_punch', 'transparent_image_dimmed_layers',
                'image_layers', 'clipped_dimmed_image_layers', 'pip_image_layers',
                'solid_dimmed_layers', 'edge_extension_shader')
            props = [p for p in props
                     if not any(p.startswith('debug.sf.prime_shader_cache.' + key + '=')
                                for key in shader_cache_keys)
                     and not p.startswith('ro.surface_flinger.prime_shader_cache.ultrahdr=')]
            props += ['debug.sf.prime_shader_cache.' + key + '=false' for key in shader_cache_keys]
            props.append('ro.surface_flinger.prime_shader_cache.ultrahdr=false')
            props += ['service.sf.prime_shader_cache=0', 'debug.sf.prime_shader_cache=false']
            if arm_props is not None:
                arm_keys = set(arm.PROPERTIES) | {'ro.enable.native.bridge.exec', 'ro.vendor.enable.native.bridge.exec', 'ro.vendor.enable.native.bridge.exec64'}
                props = [p for p in props if p.split('=', 1)[0] not in arm_keys]
                props += [key + '=' + value for key, value in arm_props.items()]
            props = [p for p in props if not p.startswith(('waydroid.host_data_path=', 'waydroid.wayland_display=', 'waydroid.pulse_runtime_path='))]
            props += ['waydroid.host_data_path=/anvil-data', 'waydroid.wayland_display=wayland-probe', 'waydroid.pulse_runtime_path=/run/xdg/pulse']
            init = fs / 'system/etc/init/init.waydroid.rc'
            # Regenerate from prepared support, not the previous boot's upper
            # overlay, where a stock launch may have removed LD_PRELOAD.
            config_text = re.sub(r'(?m)^    setenv ANVILDROID_RUNTIME_(?:ID|GENERATION) .*\n?', '', (patches / 'system/etc/init/init.waydroid.rc').read_text())
            if desktop:
                # The bridge also carries the Android 15 HIDL RPC-pool shim.
                # Native Wayland images need it even when their window
                # protocol is kept unchanged; without LD_PRELOAD their HWC
                # aborts with "Binder threadpool cannot be shrunk".
                if custom_stock_hwc:
                    # Keep the stock HWC/RPC path untouched, but still add
                    # the small Wayland-only adapter. It requests server-side
                    # decorations and provides the fixed Android canvas/F11
                    # behavior without the full HWC metadata interposer.
                    window_library = fs / 'vendor/lib64/libanvildroid-android-window.so'
                    require(not window_library.is_symlink(), 'Invalid window adapter mount target')
                    window_library.touch(mode=0o644, exist_ok=True)
                    mount('--bind', Path(__file__).parent / 'libanvildroid-android-window.so', window_library)
                    config_text = re.sub(r'(?m)^    setenv LD_PRELOAD .*\n?', '', config_text)
                    composer_service = re.compile(r'(?m)^(service vendor\.hwcomposer-2-1 .*\n)')
                    require(len(composer_service.findall(config_text)) == 1, 'Missing stock composer service')
                    config_text = composer_service.sub(
                        r'\1    setenv LD_PRELOAD /vendor/lib64/libanvildroid-android-window.so\n'
                        '    setenv ANVILDROID_ANDROID_WINDOW 1\n', config_text)
                    state['desktop_bridge'] = 'stock-android-window'
                elif native_wayland:
                    # Keep native Wayland buffer handling. The metadata
                    # adapter is restricted to the exact software HAL pair
                    # whose private HWC entry ABI has been verified.
                    mount('--bind', Path(__file__).parent / 'libanvildroid-hwc-shim.so',
                          fs / 'vendor/lib64/libanvildroid-hwc-shim.so')
                    window_env = ('\n    setenv ANVILDROID_ANDROID_WINDOW 1'
                                  if single_display else '')
                    config_text = re.sub(
                        r'(?m)^    setenv LD_PRELOAD .*\n?',
                        '    setenv LD_PRELOAD /vendor/lib64/libanvildroid-hwc-shim.so'
                        + window_env + '\n', config_text)
                    state['desktop_bridge'] = 'stock-rpc-shim'
                    metadata_pair = {
                        'hwcomposer.waydroid.so': '4109935a746069db0eee6268ec9ed2e87688a29647b4a9bc4f4a582102c3dc64',
                        'gralloc.default.so': 'c8cc2f7f514c7a0f1c49b7dc6ce4b070bae0f8d8ff64b51d5213fa144a2975d3',
                    }
                    if software_rendering and all(
                        hashlib.sha256((fs / 'vendor/lib64/hw' / name).read_bytes()).hexdigest() == digest
                        for name, digest in metadata_pair.items()
                    ):
                        preload = '    setenv LD_PRELOAD /vendor/lib64/libanvildroid-hwc-shim.so\n'
                        config_text = config_text.replace(preload, preload + '    setenv ANVILDROID_GRALLOC_METADATA 1\n')
                        allocator_text = (work / 'vendor-lower' / allocator_rc).read_text()
                        service = 'service vendor.gralloc-2-0 /vendor/bin/hw/android.hardware.graphics.allocator@2.0-service\n'
                        require(allocator_text.count(service) == 1, 'Unsupported software allocator service')
                        (fs / 'vendor' / allocator_rc).write_text(allocator_text.replace(
                            service, service + preload + '    setenv ANVILDROID_GRALLOC_METADATA 1\n'))
                        state['desktop_bridge'] = 'stock-rpc-metadata-shim'
                else:
                    mount('--bind', instance / 'desktop-bridge.so', fs / 'vendor/lib64/libanvildroid-window.so')
                companion=instance/'desktop-tasks.sh'
                if companion.exists():
                    info=companion.lstat()
                    require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'Unsafe pinned task companion')
                    mount('--bind',companion,fs/'vendor/bin/anvildroid-tasks.sh')
                props = [p for p in props if not p.startswith(('persist.waydroid.multi_windows=', 'waydroid.host.uid='))]
                # Stock custom composers commonly implement only Waydroid's
                # single-display path. Enabling multi-window makes them
                # recreate the HWC layer and abort when the first app opens.
                props += [('persist.waydroid.multi_windows=false' if (single_display or software_compatibility)
                           else 'persist.waydroid.multi_windows=true'),
                          f'waydroid.host.uid={desktop[1]}']
                props += workarea_props
                state['workarea'] = workarea_info
                marker = '    setenv LD_PRELOAD /vendor/lib64/libanvildroid-window.so'
                if not custom_stock_hwc and not native_wayland:
                    require(config_text.count(marker) == 1, 'Desktop preview requires the AnvilDroid native bridge')
                    config_text = config_text.replace(marker, marker + '\n    setenv ANVILDROID_RUNTIME_ID ' + identifier + '\n    setenv ANVILDROID_RUNTIME_GENERATION ' + generation)
            init.write_text(config_text)
            (fs / 'vendor/waydroid.prop').write_text('\n'.join(props) + '\n')
            ensure_binder_filesystem()
            mount('-t', 'binder', 'binder', work / 'binder')
            with (work / 'binder/binder-control').open('rb') as control:
                for device in ('binder', 'vndbinder', 'hwbinder'):
                    fcntl.ioctl(control, 0xC1086201, struct.pack('256sII', device.encode(), 0, 0))
                    (work / 'binder' / device).chmod(0o666)
            xdg = Path('/run/anvil-xdg')
            xdg.mkdir(mode=0o700, exist_ok=True)
            xdg.chmod(0o700)
            for stale in ('wayland-probe', 'wayland-probe.lock'):
                (xdg / stale).unlink(missing_ok=True)
            if desktop and not x11['display']:
                (xdg / 'wayland-probe').touch(mode=0o600)
                mount('--bind', desktop_pin, xdg / 'wayland-probe')
            else:
                log = (work / 'weston.log').open('w')
                logfiles.append(log)
                if x11['display']:
                    width, height = workarea_info.get('capacity') or (1920, 1080)
                    args, env = nested_weston(xdg, width, height, x11['display'], x11['authority'])
                else:
                    args = ['weston', '--backend=headless', '--renderer=gl',
                            '--socket=wayland-probe', '--idle-time=0', '--no-config', '--width=800', '--height=600']
                    env = dict({k: v for k, v in os.environ.items() if k not in ('DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'WAYLAND_DISPLAY', 'PULSE_SERVER')}, XDG_RUNTIME_DIR=str(xdg))
                start_weston(args, env, log, xdg, compositors, nested=bool(x11['display']))
            if audio_available:
                pulse = xdg / 'pulse'
                pulse.mkdir(mode=0o755, exist_ok=True)
                pulse.chmod(0o755)
                (pulse / 'native').touch(mode=0o600, exist_ok=True)
                mount('--bind', audio_pin, pulse / 'native')
            xdg.chmod(0o777)
            if not desktop or x11['display']: (xdg / 'wayland-probe').chmod(0o777)
            original = (instance / 'support/config').read_text().splitlines()
            keep = ('lxc.arch', 'lxc.apparmor.profile', 'lxc.cap.keep', 'lxc.mount.auto', 'lxc.no_new_privs', 'lxc.seccomp.allow_nesting')
            # Android init writes its per-uid cgroup dirs at boot; the host
            # config mounts the cgroup read-only, which is fatal for newer
            # Waydroid images in a cgroup-v2-only container.
            # Do not request a writable cgroup mount here. systemd owns the
            # unified hierarchy and LXC cannot delegate its controllers from
            # this service cgroup ("Device or resource busy"). Android can
            # boot with the host cgroup hidden; Binder and graphics mounts are
            # provided explicitly below.
            # Drop the inherited lxc.mount.auto line entirely. Even a
            # read-only replacement makes LXC inspect/delegate the unified
            # hierarchy; systemd-owned cgroups reject that operation with
            # __cgfsng_delegate_controllers("Device or resource busy").
            config = [line for line in original
                      if line.startswith(keep) and not line.startswith('lxc.mount.auto')]
            config.append('lxc.mount.auto = sys:ro proc')
            # Do not make startup depend on writing kernel.pid_max inside the
            # container. Several hosts expose a private PID namespace but
            # keep that sysctl read-only; the attempt then only produces a
            # misleading start failure.  32-bit translation remains optional.
            private_pid_limit = False
            config += [f'lxc.rootfs.path = dir:{fs}', f'lxc.uts.name = anvil-{name}',
                       'lxc.autodev = 0', 'lxc.console.path = none',
                       # Allow Android's uid-mapped processes to open the
                       # private DRM render node. Device cgroup filtering
                       # otherwise returns EACCES even when the node is 0666.
                       f'lxc.console.logfile = {instance}/console.log',
                       # The container inherits the host kernel cmdline, which carries
                       # no androidboot.selinux flag; without permissive, newer Waydroid
                       # images enforce their policy mid-boot and deny every service exec.
                       'lxc.init.cmd = /init androidboot.selinux=permissive',
                       'lxc.tty.max = 0', 'lxc.pty.max = 10',
                       f'lxc.seccomp.profile = {instance}/support/waydroid.seccomp',
                       'lxc.net.0.type = veth', 'lxc.net.0.link = anvilandroid',
                       'lxc.net.0.flags = up', 'lxc.net.0.name = eth0',
                       'lxc.mount.entry = tmpfs dev tmpfs nosuid 0 0',
                       'lxc.mount.entry = none dev/pts devpts defaults,mode=644,ptmxmode=666,create=dir 0 0']
            if not uplink: config.append(f'lxc.net.0.ipv4.address = 192.0.2.{index}/24')
            # loop-control + loop nodes let apexd mount APEX payloads; without
            # them /system/bin/linker64's /apex target never appears and every
            # /system/bin exec fails with EACCES.
            # Android's ueventd discovers every DRM node exposed by sysfs and
            # recreates them even when LXC only bind-mounts one render node.
            # That lets SurfaceFlinger silently pick another GPU. Build a
            # read-only private /dev/dri directory so only the selected node
            # is visible inside this container.
            gpu_dri = work / 'gpu-dri'
            if gpu_node:
                gpu_dri.mkdir(mode=0o755, exist_ok=True)
                # mkdir obeys the controller's 0077 umask. Android graphics
                # services need directory traversal as well as node access.
                gpu_dri.chmod(0o755)
                for stale in gpu_dri.iterdir():
                    stale.unlink()
            for device in ('null', 'zero', 'full', 'fuse', 'tty', 'loop-control',
                           'loop0', 'loop1', 'loop2', 'loop3', 'loop4', 'loop5', 'loop6', 'loop7',
                           *((gpu_node.removeprefix('/dev/'),) if gpu_node else ())):
                if Path('/dev', device).exists():
                    source = Path('/dev', device)
                    # Android's graphics services run as uid 1000 and do not
                    # have the host's render group. A direct bind preserves
                    # 0660 root:render and makes minigbm fail with EACCES,
                    # followed by gbm_mesa_bo_import crashes. Give the
                    # container a private 0666 device node with the same
                    # major/minor instead of changing host /dev permissions.
                    if Path(device).name.startswith('renderD'):
                        private = work / ('gpu-' + Path(device).name)
                        st = source.stat()
                        try:
                            private.unlink()
                        except FileNotFoundError:
                            pass
                        os.mknod(private, stat.S_IFCHR | 0o666,
                                 os.makedev(os.major(st.st_rdev), os.minor(st.st_rdev)))
                        # The controller uses umask 077; mknod applies it to
                        # the mode, so set the intended container-visible
                        # permissions explicitly.
                        private.chmod(0o666)
                        source = private
                        private_dri = gpu_dri / Path(device).name
                        try:
                            private_dri.unlink()
                        except FileNotFoundError:
                            pass
                        os.link(source, private_dri)
                        continue
                    options = 'bind,create=file'
                    config.append(f'lxc.mount.entry = {source} dev/{device} none {options} 0 0')
            if gpu_node:
                # The runtime storage is commonly mounted nodev. Explicitly
                # re-enable device interpretation for this private directory
                # or Mesa falls back to llvmpipe even though the node exists.
                config.append(f'lxc.mount.entry = {gpu_dri} dev/dri none bind,ro,dev,create=dir 0 0')
            for device in ('binder', 'vndbinder', 'hwbinder'):
                config.append(f'lxc.mount.entry = {work}/binder/{device} dev/{device} none bind,create=file 0 0')
            # WayDroid-ATV and newer gralloc implementations allocate video
            # buffers through DMA-BUF heaps rather than ION. The host kernel
            # may expose the heap nodes as root-only (for example 0600), so
            # mirror the character devices into the private container with
            # Android-readable permissions instead of changing host /dev.
            host_heap = Path('/dev/dma_heap')
            heap_source = work / 'dma-heap'
            heaps = []
            if host_heap.is_dir():
                heap_source.mkdir(mode=0o755, exist_ok=True)
                heap_source.chmod(0o755)
                for source in sorted(host_heap.iterdir()):
                    try:
                        info = source.stat()
                    except OSError:
                        continue
                    if not stat.S_ISCHR(info.st_mode) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', source.name):
                        continue
                    private = heap_source / source.name
                    try:
                        private.unlink()
                    except FileNotFoundError:
                        pass
                    os.mknod(private, stat.S_IFCHR | 0o666,
                             os.makedev(os.major(info.st_rdev), os.minor(info.st_rdev)))
                    private.chmod(0o666)
                    heaps.append(source.name)
                if heaps:
                    config.append(f'lxc.mount.entry = {heap_source} dev/dma_heap none bind,dev,create=dir 0 0')
            state['dma_buf_heaps'] = heaps
            for directory in ('mnt_extra', 'tmp', 'var', 'run'):
                config.append(f'lxc.mount.entry = tmpfs {directory} tmpfs nodev,create=dir 0 0')
            config += [f'lxc.mount.entry = {xdg} run/xdg none bind,create=dir 0 0',
                       f'lxc.mount.entry = {work}/data data none bind,create=dir 0 0',
                       f'lxc.mount.entry = {work}/data anvil-data none bind,create=dir 0 0']
            if desktop and not x11['display']:
                config.append(f'lxc.mount.entry = {desktop_pin} run/xdg/wayland-probe none bind,create=file 0 0')
            if audio_available:
                config.append(f'lxc.mount.entry = {audio_pin} run/xdg/pulse/native none bind,create=file 0 0')
            (work / 'config').write_text('\n'.join(config) + '\n')
            names.append(name)
            lxc('lxc-start', name, '-d', '-l', 'ERROR', '-o', work / 'lxc.log')
            init_pid = int(lxc('lxc-info', name, '-pH'))
            resources.verify_container(init_pid, identifier, generation)
            if private_pid_limit:
                try:
                    linux.limit_android_pids(init_pid)
                except (OSError, RuntimeError) as error:
                    # Some distributions expose a private PID namespace but
                    # mount its kernel sysctl read-only. This limit protects
                    # optional 32-bit translation processes; it must not make
                    # the whole 64-bit runtime impossible to start.
                    print('Android private PID limit unavailable:', error, flush=True)
        # Newer custom Android builds can spend several minutes compiling
        # framework/apex data on first boot.
        deadline = time.monotonic() + 300
        boot_wait = 'Waiting for sys.boot_completed=1'
        while not stopping.is_set():
            try:
                # lxc-attach can return success with empty output after the
                # init process has already exited. Check the container state
                # before probing properties so a dead container fails fast
                # instead of waiting for the full Android boot timeout.
                if lxc('lxc-info', identifier, '-sH', timeout=2, bounded_output=True) != 'RUNNING':
                    raise RuntimeError('Android container exited before sys.boot_completed')
                boot_property = shell('getprop', 'sys.boot_completed')
                dev_boot_property = shell('getprop', 'dev.bootcomplete')
                if boot_property == '1' or dev_boot_property == '1':
                    require('waydroidplatform' in shell('service', 'list'), 'Platform service missing')
                    provision_android_user()
                    android_id = shell('settings', 'get', 'secure', 'android_id')
                    require(android_id not in ('', 'null'), 'Android identity unavailable')
                    break
                boot_wait = 'sys.boot_completed=' + repr(boot_property[:128]) + ', dev.bootcomplete=' + repr(dev_boot_property[:128])
            except RuntimeError as error:
                boot_wait = str(error)[-512:]
                status = lxc('lxc-info', identifier, '-sH')
                if status != 'RUNNING':
                    log = loc(identifier) / 'lxc.log'
                    detail = ''
                    try:
                        detail = log.read_text(errors='replace')[-1200:].strip()
                    except OSError:
                        pass
                    require(False, 'Android container exited' + (': ' + detail if detail else ''))
            state['boot_wait'] = boot_wait
            require(time.monotonic() < deadline, 'Android boot timed out after 300s: ' + boot_wait)
            stopping.wait(1)
        if not stopping.is_set():
            helper = instance / 'shutdown.jar'
            if helper.exists():
                target = loc(identifier) / 'data/local/tmp/anvildroid-shutdown.jar'
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(helper, target)
                target.chmod(0o644)
            configure_host_desktop(instance, shell, bool(desktop))
            state['boot_wait'] = 'Configuring desktop keyboard'
            try:
                configure_desktop_ime(instance, loc(identifier), shell, bool(desktop))
                state['desktop_ime'] = 'Ready' if desktop else 'Disabled'
                state.pop('desktop_ime_warning', None)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                state['desktop_ime'] = 'Unavailable'
                state['desktop_ime_warning'] = str(error)[-1024:]
                print('Desktop IME setup warning: ' + state['desktop_ime_warning'], flush=True)
            state.update(state='Running', error=None, android_id=android_id)
            state.pop('boot_wait', None)
            state['network'] = 'private_uplink' if uplink else 'isolated'
            boot_completed = True
        while not stopping.wait(1):
            if uplink: uplink.check()
            if desktop:
                watchdog_target = desktop_pin if not x11['display'] else Path('/run/anvil-xdg') / 'wayland-probe'
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(1)
                    try: probe.connect(str(watchdog_target))
                    except OSError as error: raise RuntimeError('Desktop session disconnected. Stop / clean up, then start again from your desktop.') from error
            require(lxc('lxc-info', identifier, '-sH') == 'RUNNING', 'Android container exited')
            if compositors: require(compositors[0].poll() is None, 'Desktop compositor exited' if desktop else 'Headless compositor exited')
    except Exception as error:
        state.update(state='Error', error=str(error)[-1024:])
    finally:
        errors = []
        for name in names:
            try:
                state.update(stop_android(lxc, name, boot_completed))
                require(lxc('lxc-info', name, '-sH') == 'STOPPED', 'Container did not stop')
            except Exception as error: errors.append(str(error))
        for process in compositors:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if uplink:
            try: uplink.close()
            except Exception as error: errors.append(str(error))
        if host_net_fd is not None: os.close(host_net_fd)
        for log in logfiles: log.close()
        for path in reversed(mounts):
            try:
                run('umount', path)
            except Exception as error:
                # A stopped LXC monitor can briefly retain a rootfs mount
                # while its last Android child exits. Detach it lazily so a
                # normal stop/restart is not reported as a permanent error.
                try:
                    run('umount', '-l', path)
                except Exception:
                    errors.append(str(error))
        if errors: state.update(state='Error', error='; '.join(errors)[-1024:])
        elif state['state'] != 'Error': state.update(state='Stopped', error=None)
        state['cleanup_complete'] = not errors
        atomic(instance / 'worker-result.json', dict(state))
        done.set()


def main(instance, identifier, generation, original_mnt, original_net, desktop=None, host_net_fd=None):
    require(os.geteuid() == 0, 'Root worker required')
    require(linux.ns('mnt') != original_mnt and linux.ns('net') != original_net, 'Private namespaces required')
    require(instance.is_absolute() and instance.resolve() == instance and instance.name == identifier, 'Invalid worker path')
    metadata = instance.stat()
    require(metadata.st_uid == 0 and not metadata.st_mode & 0o077, 'Unsafe worker directory')
    lock = os.open(instance / 'worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    endpoint = instance / 'worker.sock'
    endpoint.unlink(missing_ok=True)
    state = dict(runtime_id=identifier, generation=generation, state='Starting', error=None, display_mode='desktop' if desktop else 'headless', capabilities=['graphics', 'apps', 'app_actions', 'uninstall_app', 'install_apk', 'google_services'])
    state['capabilities'].append('app_states')
    if desktop: state['capabilities'].extend(['keymap_editor', 'full_ui'])
    atomic(instance / 'worker-identity.json', dict(runtime_id=identifier, generation=generation))
    (instance / 'worker-result.json').unlink(missing_ok=True)
    stopping, done = threading.Event(), threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(endpoint))
    os.chmod(endpoint, 0o600)
    server.listen(4)
    server.settimeout(.5)
    thread = threading.Thread(target=boot, args=(instance, identifier, generation, state, stopping, done, desktop, host_net_fd))
    thread.start()
    try:
        while not done.is_set():
            try: connection, _ = server.accept()
            except socket.timeout: continue
            with connection:
                connection.settimeout(2)
                _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != 0: continue
                apk_fd = None
                try:
                    request, apk_fd = transfer.receive(connection, 1024)
                    if apk_fd is not None and (not isinstance(request, dict) or request.get('op') != 'install'):
                        raise ValueError('Unexpected file descriptor')
                    if request == {'op': 'stop'}:
                        if state['state'] != 'Error': state['state'] = 'Stopping'
                        stopping.set()
                    elif request == {'op': 'graphics'}:
                        response = dict(state)
                        try:
                            require(state['state'] == 'Running' and not stopping.is_set(), 'Start this runtime to inspect its renderer')
                            output = linux.run_bounded('lxc-attach', '-P', instance / 'android', '-n', identifier, '--',
                                         '/system/bin/sh', '-c', gpu.PROBE_COMMAND, timeout=2, umask=0o022, operation='GPU inspection')
                            response['graphics'] = gpu.probe_report(output)
                        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                            response['graphics_error'] = str(error)[-1024:]
                        connection.sendall(json.dumps(response).encode() + b'\n')
                        continue
                    elif request == {'op': 'google_services'}:
                        response = dict(state)
                        try:
                            require(state['state'] == 'Running' and not stopping.is_set(), 'Start this runtime before checking Google services')
                            response['google_services'] = google_services(instance, identifier)
                        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                            response['app_error'] = str(error)[-1024:]
                        connection.sendall(json.dumps(response).encode() + b'\n')
                        continue
                    elif request == {'op': 'app_states'}:
                        response = dict(state)
                        response['app_states'] = (app_execution_states(instance, identifier)
                                                  if state['state'] == 'Running' and not stopping.is_set() else None)
                        response['checked_at'] = time.time()
                        connection.sendall(json.dumps(response).encode() + b'\n')
                        continue
                    elif request == {'op': 'apps'}:
                        response = dict(state)
                        try:
                            require(state['state'] == 'Running' and not stopping.is_set(), 'Start Android before listing packages')
                            response['apps'] = list_apps(instance, identifier,
                                                         single_display=state.get('presentation_mode') == 'android-display')
                        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                            response['app_error'] = str(error)[-1024:]
                        connection.sendall(json.dumps(response).encode() + b'\n')
                        continue
                    elif request == {'op':'keymaps'}:
                        response=dict(state)
                        try:
                            require(desktop and state['state']=='Running','Desktop runtime must be running')
                            response['keymaps']=keymap_profiles(instance,identifier)
                        except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as error:
                            response['app_error']=str(error)[-1024:]
                        connection.sendall(json.dumps(response).encode()+b'\n')
                        continue
                    elif isinstance(request, dict) and request.get('op') == 'install':
                        response = dict(state)
                        try:
                            require(set(request) == {'op', 'generation'}, 'Invalid install request')
                            require(request['generation'] == generation, 'Worker generation changed; installation refused')
                            require(state['state'] == 'Running' and not stopping.is_set(), 'Android is not running')
                            response['app_result'] = install_apk(instance, identifier, apk_fd, desktop=bool(desktop))
                        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
                            response['app_error'] = str(error)[-1024:]
                        connection.sendall(json.dumps(response).encode() + b'\n')
                        continue
                    elif isinstance(request, dict) and request.get('op') == 'app_action':
                        response = dict(state)
                        try:
                            require(set(request) == {'op', 'generation', 'action', 'package'}, 'Invalid app action request')
                            require(request['generation'] == generation, 'Worker generation changed; action refused')
                            require(state['state'] == 'Running' and not stopping.is_set(), 'Android is not running')
                            response['app_result'] = app_action(instance, identifier, request['action'], request['package'], desktop=bool(desktop), generation=generation,
                                                               single_display=state.get('presentation_mode') == 'android-display')
                        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                            response['app_error'] = str(error)[-1024:]
                        connection.sendall(json.dumps(response).encode() + b'\n')
                        continue
                    elif request != {'op': 'status'}: continue
                    connection.sendall(json.dumps(dict(state)).encode() + b'\n')
                except (OSError, ValueError): pass
                finally:
                    if apk_fd is not None: os.close(apk_fd)
    finally:
        stopping.set()
        thread.join()
        server.close()
        endpoint.unlink(missing_ok=True)
        os.close(lock)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', type=Path, required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--generation', required=True)
    parser.add_argument('--namespaces', nargs=2, required=True)
    parser.add_argument('--desktop-display', type=str)
    parser.add_argument('--desktop-socket', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--desktop-uid', type=int)
    parser.add_argument('--host-net-fd', type=int)
    args = parser.parse_args()
    if args.desktop_socket is not None:
        require(args.desktop_display is None and args.desktop_uid is not None and
                args.desktop_socket.parent == Path('/run/user') / str(args.desktop_uid),
                'Legacy desktop socket must belong to the selected user')
        args.desktop_display = args.desktop_socket.name
    require((args.desktop_display is None) == (args.desktop_uid is None), 'Desktop display and UID must be provided together')
    main(args.instance, args.id, args.generation, *args.namespaces, desktop=(args.desktop_display, args.desktop_uid) if args.desktop_display else None, host_net_fd=args.host_net_fd)
