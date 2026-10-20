"""Private GPU display transport. No DRM modesetting or host input devices.

Headless Weston renders with GL; rootful Xwayland supplies GPU GLAMOR and XTest.
Every caller must already own private mount/network/PID namespaces and :77.
CPU fallback is an error, not a successful GPU preflight.
"""
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import time


def require(value, message):
    if not value:
        raise RuntimeError(message)


def renderer_environment(node, mesa_prefix=None):
    device = Path('/sys/class/drm') / Path(node).name / 'device'
    pci = device.resolve().name
    require(re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', pci), 'Missing GPU PCI identity')
    vendor = (device / 'vendor').read_text().strip()
    require(vendor in ('0x1002', '0x8086'), 'This GPU display fixture requires AMD or Intel Mesa')
    env = {'DRI_PRIME': 'pci-' + pci.replace(':', '_').replace('.', '_'),
           '__GLX_VENDOR_LIBRARY_NAME': 'mesa',
           '__EGL_VENDOR_LIBRARY_FILENAMES': '/usr/share/glvnd/egl_vendor.d/50_mesa.json'}
    if mesa_prefix:
        prefix = Path(mesa_prefix)
        require(prefix.is_absolute() and prefix.resolve() == prefix, 'Use an absolute canonical Mesa prefix')
        for relative in ('share/glvnd/egl_vendor.d/50_mesa.json',
                         'lib/x86_64-linux-gnu/libEGL_mesa.so.0', 'lib/x86_64-linux-gnu/libGLX_mesa.so.0'):
            path = prefix / relative
            info = path.stat()
            require(info.st_uid == 0 and not info.st_mode & 0o022, 'Untrusted host Mesa library')
        env.update(__EGL_VENDOR_LIBRARY_FILENAMES=str(prefix / 'share/glvnd/egl_vendor.d/50_mesa.json'),
                   LD_LIBRARY_PATH=str(prefix / 'lib/x86_64-linux-gnu'),
                   LIBGL_DRIVERS_PATH=str(prefix / 'lib/x86_64-linux-gnu/dri'))
    return env


def hardware_renderer(text):
    require('direct rendering: Yes' in text and 'Accelerated: yes' in text,
            'GPU display is not direct and hardware accelerated')
    renderer = re.search(r'^OpenGL renderer string: (.+)$', text, re.M)
    require(renderer and not any(word in renderer[1].lower() for word in
            ('llvmpipe', 'softpipe', 'swiftshader', 'lavapipe', 'pastel', 'software')),
            'GPU display silently fell back to CPU rendering')
    return renderer[1]


def start_x11(root, owner_uid, generation, authority, size, renderer_env):
    require(os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'), 'Private mount namespace required')
    require(os.readlink('/proc/self/ns/net') != os.readlink('/proc/1/ns/net'), 'Private network namespace required')
    account = pwd.getpwuid(owner_uid)
    require(owner_uid > 0 and authority.parent == Path('/run/user') / str(owner_uid), 'Private user authority required')
    os.chmod('/tmp/.X11-unix', 0o1777)
    display = 'wayland-gpu-transport-' + generation
    env = {'PATH': os.environ['PATH'], 'HOME': str(authority.parent), 'XDG_RUNTIME_DIR': str(authority.parent),
           'WAYLAND_DISPLAY': display, 'DISPLAY': ':77', 'XAUTHORITY': str(authority),
           'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/no-session-bus', **renderer_env}
    groups = sorted({Path('/dev/dri').joinpath(name).stat().st_gid for name in os.listdir('/dev/dri')
                     if name.startswith(('renderD', 'card'))})
    processes = []
    try:
        with (root / ('gpu-transport-weston-' + generation + '.log')).open('w') as log:
            processes.append(subprocess.Popen(['weston', '--backend=headless', '--renderer=gl', '--no-config',
                '--socket=' + display, '--width=' + str(size[0]), '--height=' + str(size[1]), '--idle-time=0', '--debug'],
                env=env, cwd='/', user=owner_uid, group=account.pw_gid, extra_groups=groups, stdout=log, stderr=log))
        deadline = time.monotonic() + 20
        while not (authority.parent / display).exists():
            require(processes[-1].poll() is None and time.monotonic() < deadline, 'GPU transport Weston failed')
            time.sleep(.1)
        with (root / ('gpu-transport-xwayland-' + generation + '.log')).open('w') as log:
            processes.append(subprocess.Popen(['Xwayland', ':77', '-noreset', '-nolisten', 'tcp',
                '-geometry', f'{size[0]}x{size[1]}', '-glamor', 'es', '-auth', str(authority), '-verbose', '3'],
                env=env, cwd='/', user=owner_uid, group=account.pw_gid, extra_groups=groups, stdout=log, stderr=log))
        deadline = time.monotonic() + 20
        while not Path('/tmp/.X11-unix/X77').exists():
            require(processes[-1].poll() is None and time.monotonic() < deadline, 'GPU Xwayland failed')
            time.sleep(.1)
        result = subprocess.run(['glxinfo', '-B'], env=env, check=True, capture_output=True, text=True, timeout=15)
        renderer = hardware_renderer(result.stdout)
        (root / ('gpu-transport-glxinfo-' + generation + '.txt')).write_text(result.stdout + result.stderr)
        subprocess.run(['xdotool', 'mousemove', '100', '120', 'getmouselocation', '--shell'],
                       env=env, check=True, capture_output=True, text=True, timeout=5)
        report = {'kind': 'private-gpu-weston-xwayland', 'renderer': renderer, 'direct': True, 'accelerated': True,
                  'x11_display': ':77', 'wayland_display': display, 'host_library_environment': renderer_env,
                  'pids_in_fixture_namespace': [p.pid for p in processes], 'size': size}
        (root / ('gpu-transport-' + generation + '.json')).write_text(json.dumps(report, indent=2) + '\n')
        return processes, report
    except BaseException:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        raise
