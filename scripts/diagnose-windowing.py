#!/usr/bin/env python3
"""Read-only window diagnostics; run with sudo while the affected runtime runs."""
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import argparse
import sys
import time


PROPERTIES = ('waydroid.background_start', 'waydroid.active_apps',
              'persist.waydroid.multi_windows', 'waydroid.xdg_runtime_dir',
              'waydroid.wayland_display', 'ro.hardware.egl',
              'ro.hardware.gralloc', 'ro.hardware.vulkan',
              'debug.hwui.renderer', 'debug.renderengine.backend',
              'debug.angle.feature_overrides_disabled', 'sys.boot_completed')


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                errors='replace', timeout=8)
        return {'exit_code': result.returncode, 'stdout': result.stdout[-16000:],
                'stderr': result.stderr[-2000:]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'error': str(error)}


def selected_props(text):
    return [line for line in text.splitlines()
            if line.split('=', 1)[0].strip() in PROPERTIES]


def read(path):
    try:
        with path.open('r', errors='replace') as stream:
            return stream.read(65536)
    except OSError as error:
        return 'Unavailable: ' + str(error)


def tail(path):
    try:
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 16384))
            return stream.read(16384).decode(errors='replace')
    except OSError as error:
        return 'Unavailable: ' + str(error)


def diagnose(instance):
    result = {'instance': str(instance),
              'compatibility': read(instance / 'support/compatibility.json'),
              'configured_properties': selected_props(read(instance / 'support/waydroid.prop'))}
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(instance / 'worker.sock'))
            pid, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 0 or pid <= 1:
                raise RuntimeError('Worker is not a root-owned runtime process')
            client.sendall(b'{"op":"status"}\n')
            data = b''
            while b'\n' not in data and len(data) <= 65536:
                part = client.recv(4096)
                if not part:
                    break
                data += part
            state = json.loads(data)
            if state.get('runtime_id') != instance.name:
                raise RuntimeError('Worker runtime identity mismatch')
            result['worker'] = {key: state.get(key) for key in
                ('state', 'error', 'generation', 'display_mode', 'presentation_mode',
                 'desktop_bridge', 'gpu_node', 'software_renderer', 'graphics_backend',
                 'nvidia_renderer', 'shutdown_warning', 'cleanup_complete')}
            if state.get('state') != 'Running':
                return result
            root = Path('/proc') / str(pid) / 'root' / str(instance).lstrip('/')
            guest = root / 'android' / instance.name / 'rootfs'
            result['boot_properties'] = selected_props(read(guest / 'vendor/waydroid.prop'))
            result['composer_init'] = [line for line in read(guest / 'system/etc/init/init.waydroid.rc').splitlines()
                if any(key in line for key in ('hwcomposer', 'LD_PRELOAD', 'ANVILDROID_'))]
            # LXC's abstract Unix control socket belongs to the worker's
            # network namespace, too. Mount/PID alone gives ECONNREFUSED.
            lxc = ['nsenter', '-t', str(pid), '-m', '-p', '-n', '--']
            location = ['-P', str(instance / 'android'), '-n', instance.name]
            result['container_state'] = command(lxc + ['lxc-info'] + location + ['-sH'])
            prefix = lxc + ['lxc-attach'] + location + [
                '--set-var', 'PATH=/system/bin:/system/xbin:/vendor/bin', '--']
            result['live_properties'] = command(prefix + ['/system/bin/sh', '-c',
                '\n'.join('echo ' + key + '=$(/system/bin/getprop ' + key + ')' for key in PROPERTIES)])
            result['loaded_bridge'] = command(prefix + ['/system/bin/sh', '-c',
                'for p in $(pidof android.hardware.graphics.composer@2.1-service); do '
                'echo composer_pid=$p; grep libanvildroid /proc/$p/maps; done'])
            result['android_processes'] = command(prefix + ['/system/bin/sh', '-c',
                'for name in surfaceflinger system_server com.android.settings; do '
                'echo "$name=$(pidof "$name")"; done'])
            result['activity_log'] = command(prefix + ['/system/bin/logcat', '-b', 'main',
                '-b', 'system', '-d', '-t', '100', '-s', 'ActivityManager:V',
                'ActivityTaskManager:V', 'AndroidRuntime:V', 'SurfaceFlinger:W', 'lmkd:V'])
            result['window_log'] = command(prefix + ['/system/bin/sh', '-c',
                "logcat -b all -d -t 1500 2>/dev/null | "
                "grep -Ei 'anvildroid|multi.window|wl_subcompositor|cannot link executable|"
                "cannot load.*libanvildroid|failed to open wayland|VK_ERROR_DEVICE_LOST|"
                "fatal signal|abort message|backtrace|FATAL EXCEPTION|hwcomposer' | tail -n 100"])
            result['crash_log'] = command(prefix + ['/system/bin/logcat', '-b', 'crash', '-d', '-t', '100'])
    except (OSError, ValueError, RuntimeError) as error:
        result['worker_error'] = str(error)
    finally:
        # These survive worker/container exit. Keep generation and timestamps
        # so an earlier failure is not mistaken for the current attempt.
        result['collected_at'] = time.time()
        result['saved_worker_result'] = read(instance / 'worker-result.json')
        result['logs'] = {}
        for name in ('worker.log', 'nvidia-renderer.log'):
            path = instance / name
            try:
                result['logs'][name] = {'modified_at': path.stat().st_mtime, 'tail': tail(path)}
            except OSError:
                pass
    return result


def changed_snapshots(snapshots, previous):
    """Omitted fields retain their previous value, separately for each instance."""
    changes = []
    for snapshot in snapshots:
        instance = snapshot['instance']
        old = previous.get(instance, {})
        delta = {key: value for key, value in snapshot.items()
                 if key in ('instance', 'collected_at') or old.get(key) != value}
        removed = sorted(old.keys() - snapshot.keys())
        if removed:
            delta['removed_fields'] = removed
        changes.append(delta)
        previous[instance] = snapshot
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', type=int, default=0, metavar='SECONDS',
                        help='Collect repeated snapshots around a manual app launch (maximum 300 seconds).')
    args = parser.parse_args()
    if not 0 <= args.watch <= 300:
        parser.error('--watch must be between 0 and 300 seconds')
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo; this script only reads runtime configuration and status.')
    store = Path('/var/lib/anvildroid-controller/instances')
    started = time.time()
    report = {'diagnostic_version': 4,
              'package': command(['dpkg-query', '-W', '-f=${Version}', 'anvildroid-controller']),
              'system_waydroid': command(['lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-sH']),
              'runtimes': [diagnose(p) for p in sorted(store.glob('r-*'))
                           if re.fullmatch(r'r-[0-9a-f]{32}', p.name)]}
    if args.watch:
        print('Initial snapshot captured. Open Settings now; leave the failed runtime as-is until capture finishes.', file=sys.stderr)
        deadline = time.monotonic() + args.watch
        report['samples'] = []
        report['sample_format'] = ('Changes since the previous snapshot for each instance; '
                                   'omitted fields are unchanged; removed_fields lists deleted keys.')
        previous = {snapshot['instance']: snapshot for snapshot in report['runtimes']}
        try:
            while time.monotonic() < deadline:
                time.sleep(min(2, max(0, deadline - time.monotonic())))
                snapshots = [diagnose(p) for p in sorted(store.glob('r-*'))
                    if re.fullmatch(r'r-[0-9a-f]{32}', p.name)]
                report['samples'].append(changed_snapshots(snapshots, previous))
        except KeyboardInterrupt:
            report['interrupted'] = True
        report['kernel_gpu_log'] = command(['journalctl', '-k', '-b', '--no-pager', '-n', '100',
            '--since', '@' + str(int(started)), '--grep', 'NVRM|Xid|nvidia|GPU.*fault'])
        print('Capture finished.', file=sys.stderr)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
