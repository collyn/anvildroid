"""Probe the desktop compositor workarea and derive the boot canvas capacity.

The worker runs this before Android boots (Desktop mode only). The chain is
KWin scripting (KDE) -> Mutter DisplayConfig (GNOME) -> a bounded pure-Python
wl_output enumeration (any Wayland compositor). Each stage fails closed; if
all fail the boot continues without the canvas property and the bridge keeps
its fixed 2x reserve (see native/bridge/boot-canvas.inc).

Every output entry carries w/h in PHYSICAL pixels (the size a maximized
window needs on that output); the reported scale is evidence only. KDE
clientArea values are logical and are converted with the output scale.

Margins, budget and rounding stay in native/bridge/boot-canvas.h so all
sizing policy lives in one place.

The KWin script reports back over the session bus through a python-dbus
listener (guarded import; missing python-dbus falls through to the next
backend). A stdlib-only alternative would be to let the script print into
the journal (console.log, tag kwin_wayland) and poll
`journalctl --user -t kwin_wayland` under runuser; not implemented here.
"""
from pathlib import Path
import importlib.util
import json
import math
import os
import pwd
import re
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid

# The X11 display parser/authority resolution is security-relevant; reuse the
# desktop module's implementation (single source of truth) instead of copying.
_desktop_spec = importlib.util.spec_from_file_location(
    'runtime_desktop', Path(__file__).with_name('runtime-desktop.py'))
desktop_hints = importlib.util.module_from_spec(_desktop_spec)
_desktop_spec.loader.exec_module(desktop_hints)
_X11_TOKEN_PREFIX = desktop_hints.X11_TOKEN_PREFIX

CANVAS_PROPERTY = 'persist.anvildroid.canvas'
CAPACITY_MAX_AXIS = 8192
CAPACITY_MAX_PIXELS = 16777216
SOCKET_NAME = re.compile(r'(?:wayland|anvildroid-gnome)-[A-Za-z0-9_.-]+')

_KDE_SCRIPT = r'''
var result = {nonce: NONCE, outputs: [], scaleType: 'missing', dprType: 'missing'};
try {
    var outputsList = [];
    if (typeof workspace.outputs !== 'undefined' && workspace.outputs.length)
        outputsList = workspace.outputs;
    else if (typeof workspace.screens !== 'undefined' && workspace.screens.length)
        outputsList = workspace.screens;
    if (outputsList.length) {
        result.scaleType = typeof outputsList[0].scale;
        result.dprType = typeof outputsList[0].devicePixelRatio;
    }
    for (var i = 0; i < outputsList.length; ++i) {
        var output = outputsList[i];
        var area = workspace.clientArea(KWin.MaximizeArea, output, workspace.currentDesktop);
        var scale = 0;
        if (typeof output.scale === 'number' && output.scale > 0) scale = output.scale;
        else if (typeof output.devicePixelRatio === 'number' && output.devicePixelRatio > 0) scale = output.devicePixelRatio;
        result.outputs.push({name: String(output.name), x: area.x, y: area.y,
            width: area.width, height: area.height, scale: scale});
    }
} catch(e) { result.error = String(e); }
callDBus('BUSNAME','/Probe','BUSNAME','Report',JSON.stringify(result));
'''

# The session bus on this host refuses root connections, and the worker is
# root, so the D-Bus listener runs as the socket owner in a helper process
# (the inspect-windows.py pattern); the worker reads the JSON report from
# the helper's last stdout line. python-dbus/GLib are only required inside
# the helper; a helper failure just moves the chain to the next backend.
_KDE_HELPER = r'''import dbus
import dbus.service
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

nonce = sys.argv[1]
bus_name = 'org.anvildroid.WorkareaProbe' + nonce
DBusGMainLoop(set_as_default=True)
loop = GLib.MainLoop()
bus = dbus.SessionBus()
name = dbus.service.BusName(bus_name, bus)
received = []

class Probe(dbus.service.Object):
    @dbus.service.method(bus_name, in_signature='s', out_signature='')
    def Report(self, value):
        record = json.loads(str(value))
        if record.get('nonce') == nonce:
            received.append(record)
            loop.quit()

probe = Probe(bus, '/Probe')
script = PROBEJS.replace('BUSNAME', bus_name).replace('NONCE', json.dumps(nonce))

def db(*args):
    return subprocess.check_output(['qdbus6', 'org.kde.KWin', *args], text=True).strip()

with tempfile.TemporaryDirectory(prefix='anvildroid-workarea-') as folder:
    path = Path(folder) / 'probe.js'
    path.write_text(script)
    plugin = 'anvildroid-workarea-' + nonce
    sid = db('/Scripting', 'org.kde.kwin.Scripting.loadScript', str(path), plugin)
    try:
        db('/Scripting/Script' + sid, 'org.kde.kwin.Script.run')
        db('/Scripting', 'org.kde.kwin.Scripting.start')
        GLib.timeout_add(8000, lambda: (loop.quit(), False)[1])
        loop.run()
    finally:
        db('/Scripting', 'org.kde.kwin.Scripting.unloadScript', plugin)
if not received:
    sys.exit('KWin did not return a workarea probe result')
record = received[0]
if record.get('error'):
    sys.exit('KWin script error: ' + str(record['error']))
print(json.dumps(record))
'''


def _user(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        raise RuntimeError('Unknown desktop uid') from None


def _dbus(uid, *argv, timeout=10):
    """Session-bus call under the socket owner; the worker has no session env."""
    return _run_as_user(uid, argv, timeout=timeout)


def _run_as_user(uid, argv, timeout=10, extra_env=None):
    """Run argv with the socket owner's session bus; drop to the owner when root."""
    environment = dict(os.environ)
    environment['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path=/run/user/{uid}/bus'
    environment.update(extra_env or {})
    command = list(argv)
    if os.geteuid() != uid:
        command = ['runuser', '-u', _user(uid), '--', 'env',
                   f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus',
                   *[f'{k}={v}' for k, v in (extra_env or {}).items()], *argv]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                          env=environment)


def _validate_socket_path(socket_path, uid):
    path = Path(socket_path)
    if uid <= 0 or not SOCKET_NAME.fullmatch(path.name):
        raise RuntimeError('Unsafe Wayland socket name')
    if path.parent != Path('/run/user') / str(uid):
        raise RuntimeError('Unsafe Wayland socket path')
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
        raise RuntimeError('Unsafe Wayland socket ownership')


def _physical(width, height, scale):
    """Logical workarea to physical pixels; rejects unknown/invalid scales."""
    if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
        raise RuntimeError('Invalid workarea dimensions')
    if not isinstance(scale, (int, float)) or scale <= 0:
        raise RuntimeError('Invalid output scale')
    return math.ceil(width * scale), math.ceil(height * scale)


def workarea_capacity(outputs):
    """Max physical capacity per axis; entries already carry physical w/h."""
    width = height = 0
    for output in outputs:
        w, h = output.get('w', 0), output.get('h', 0)
        if not (isinstance(w, int) and isinstance(h, int) and
                1 <= w <= CAPACITY_MAX_AXIS and 1 <= h <= CAPACITY_MAX_AXIS):
            continue
        width = max(width, w)
        height = max(height, h)
    if width <= 0 or height <= 0:
        raise RuntimeError('No valid output workarea')
    return width, height


def append_canvas_property(props, capacity):
    width, height = capacity
    if not (1 <= width <= CAPACITY_MAX_AXIS and 1 <= height <= CAPACITY_MAX_AXIS and
            width * height <= CAPACITY_MAX_PIXELS):
        raise RuntimeError('Canvas capacity out of bounds')
    props = [p for p in props if not p.startswith(CANVAS_PROPERTY + '=')]
    props.append(f'{CANVAS_PROPERTY}={width}x{height}')
    return props


def boot_props(props, socket_path, uid):
    """Append the canvas property when the compositor can be probed. Never raises."""
    try:
        report = probe_report(socket_path, uid)
        cap_w, cap_h = report['capacity']
        return append_canvas_property(list(props), (cap_w, cap_h)), {'capacity': [cap_w, cap_h]}
    except Exception as error:
        print(f'Workarea probe skipped: {error}', file=sys.stderr, flush=True)
        return props, {'skipped': str(error)[-256:]}


def probe_report(socket_path, uid, overall_timeout=20):
    """Full diagnostic probe: capacity, source backend and output inventory."""
    deadline = time.monotonic() + overall_timeout
    failures = []
    x11_token = isinstance(socket_path, str) and socket_path.startswith(_X11_TOKEN_PREFIX)
    terminal = (_x11, 'x11') if x11_token else (_wayland, 'wayland')
    for backend, source in ((_kde, 'kde'), (_gnome, 'gnome'), terminal):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failures.append('overall probe deadline exceeded')
            break
        try:
            outputs = backend(socket_path, uid, remaining)
        except Exception as error:
            failures.append(f'{source}: {error}')
            continue
        if outputs:
            cap_w, cap_h = workarea_capacity(outputs)
            if cap_w * cap_h > CAPACITY_MAX_PIXELS:
                raise RuntimeError('Canvas capacity out of bounds')
            return {'capacity': [cap_w, cap_h], 'source': source, 'outputs': outputs}
        failures.append(f'{source}: compositor not detected')
    raise RuntimeError('; '.join(failures) or 'workarea probe failed')


def capacity(socket_path, uid, overall_timeout=20):
    return tuple(probe_report(socket_path, uid, overall_timeout)['capacity'])


def _kde(socket_path, uid, timeout):
    """KWin scripting probe: per-output MaximizeArea (logical) + scale."""
    ping = _dbus(uid, 'gdbus', 'call', '--session', '--dest', 'org.kde.KWin',
                 '--object-path', '/KWin', '--method', 'org.freedesktop.DBus.Peer.Ping',
                 timeout=min(timeout, 4))
    if ping.returncode != 0:
        if 'NameHasNoOwner' in ping.stderr or 'ServiceUnknown' in ping.stderr:
            return None
        raise RuntimeError('KWin ping failed: ' + (ping.stderr or ping.stdout).strip()[:200])
    nonce = uuid.uuid4().hex
    helper = _KDE_HELPER.replace('PROBEJS', json.dumps(_KDE_SCRIPT))
    fd, path = tempfile.mkstemp(prefix='anvildroid-workarea-', suffix='.py', dir='/tmp')
    try:
        with os.fdopen(fd, 'w') as script_file:
            script_file.write(helper)
        os.chmod(path, 0o644)
        result = _run_as_user(uid, [sys.executable, path, nonce], timeout=min(timeout, 20))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if result.returncode != 0:
        raise RuntimeError('KWin workarea helper failed: ' + (result.stderr or result.stdout).strip()[-200:])
    try:
        record = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError('KWin workarea helper returned no report') from None
    outputs = []
    for entry in record.get('outputs', []):
        pw, ph = _physical(entry.get('width', 0), entry.get('height', 0), entry.get('scale', 0))
        if pw > CAPACITY_MAX_AXIS or ph > CAPACITY_MAX_AXIS:
            raise RuntimeError('KWin output exceeds capacity bounds')
        outputs.append({'name': entry.get('name'), 'w': pw, 'h': ph, 'scale': entry.get('scale')})
    if not outputs:
        raise RuntimeError('KWin reported no outputs')
    return outputs


_GNOME_MONITOR = re.compile(r"\(\('([^']+)', '[^']*', '[^']*', '[^']*'\), \[")
_GNOME_MODE = re.compile(r"\('([^']+)', (\d+), (\d+), ([0-9.]+), ([0-9.]+), \[([^\]]*)\], \{([^}]*)\}\)")


def parse_gnome_state(text):
    """Tolerant parse of org.gnome.Mutter.DisplayConfig.GetCurrentState text.

    Monitors are delimited by their header tuples (mode lists themselves
    contain "], {" pairs, so slices between consecutive headers are parsed
    instead of a single greedy pattern). The current mode (or the first)
    provides physical w/h. Logical-monitor layout is not parsed here.
    """
    if not isinstance(text, str) or len(text) > 262144:
        raise RuntimeError('Malformed Mutter DisplayConfig reply')
    monitors = []
    headers = list(_GNOME_MONITOR.finditer(text))
    for index, match in enumerate(headers):
        segment = text[match.start():headers[index + 1].start() if index + 1 < len(headers) else len(text)]
        modes = list(_GNOME_MODE.finditer(segment))
        if not modes:
            raise RuntimeError('Mutter monitor without modes')
        chosen = next((m for m in modes if re.search(r"'is-current':\s*<true>", m.group(7))), modes[0])
        width, height = int(chosen.group(2)), int(chosen.group(3))
        scale = float(chosen.group(5))
        if not (1 <= width <= CAPACITY_MAX_AXIS and 1 <= height <= CAPACITY_MAX_AXIS):
            raise RuntimeError('Mutter mode out of bounds')
        monitors.append({'name': match.group(1), 'w': width, 'h': height, 'scale': scale})
    if not monitors:
        raise RuntimeError('No monitors in Mutter DisplayConfig reply')
    if len(monitors) > 64:
        raise RuntimeError('Too many Mutter monitors')
    return monitors


def _gnome(socket_path, uid, timeout):
    reply = _dbus(uid, 'gdbus', 'call', '--session', '--dest', 'org.gnome.Mutter.DisplayConfig',
                  '--object-path', '/org/gnome/Mutter/DisplayConfig',
                  '--method', 'org.gnome.Mutter.DisplayConfig.GetCurrentState',
                  timeout=min(timeout, 10))
    if reply.returncode != 0:
        if 'NameHasNoOwner' in reply.stderr or 'ServiceUnknown' in reply.stderr:
            return None
        raise RuntimeError('Mutter DisplayConfig query failed: ' + (reply.stderr or reply.stdout).strip()[:200])
    return parse_gnome_state(reply.stdout)


_XRANDR_OUTPUT = re.compile(r'^(\S+) connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)')


def parse_xrandr(text):
    if not isinstance(text, str) or len(text) > 262144:
        raise RuntimeError('Malformed xrandr output')
    outputs = []
    for line in text.splitlines():
        match = _XRANDR_OUTPUT.match(line)
        if not match:
            continue
        w, h = int(match.group(2)), int(match.group(3))
        if not (1 <= w <= CAPACITY_MAX_AXIS and 1 <= h <= CAPACITY_MAX_AXIS):
            raise RuntimeError('X11 output out of bounds')
        outputs.append({'name': match.group(1), 'w': w, 'h': h, 'scale': 1})
    if not outputs:
        raise RuntimeError('No X11 outputs')
    return outputs


def _x11(display, uid, timeout):
    """X11 workarea via xrandr (xdotool-free); xdpyinfo reports total dimensions as fallback."""
    value = display[len(_X11_TOKEN_PREFIX):]
    if desktop_hints.parse_x11_display(value) is None:
        raise RuntimeError('Invalid X11 display')
    environment = {'DISPLAY': value}
    authority = desktop_hints.x11_authority(uid)
    if authority is not None:
        environment['XAUTHORITY'] = str(authority)
    result = _run_as_user(uid, ['xrandr', '--query'], timeout=min(timeout, 6), extra_env=environment)
    if result.returncode == 0 and result.stdout:
        return parse_xrandr(result.stdout)
    if result.returncode != 0 and not result.stdout:
        # xrandr absent: xdpyinfo reports total dimensions as a single output.
        fallback = _run_as_user(uid, ['xdpyinfo'], timeout=min(timeout, 6), extra_env=environment)
        match = re.search(r'dimensions:\s+(\d+)x(\d+) pixels', fallback.stdout or '')
        if fallback.returncode == 0 and match:
            w, h = int(match.group(1)), int(match.group(2))
            if 1 <= w <= CAPACITY_MAX_AXIS and 1 <= h <= CAPACITY_MAX_AXIS:
                return [{'name': None, 'w': w, 'h': h, 'scale': 1}]
        raise RuntimeError('xrandr/xdpyinfo probe failed: ' + (result.stderr or fallback.stderr or '').strip()[-200:])
    raise RuntimeError('xrandr probe failed: ' + result.stderr.strip()[-200:])


def _wayland(socket_path, uid, timeout):
    """Bounded wl_output enumeration on a fresh authenticated connection."""
    _validate_socket_path(socket_path, uid)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(min(timeout, 2))
        connection.connect(str(socket_path))
        peer = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        if struct.unpack('3i', peer)[1] != uid:
            raise RuntimeError('Untrusted compositor peer')
        outputs = wayland_outputs(connection, timeout=min(timeout, 2))
        if not outputs:
            raise RuntimeError('No outputs advertised')
        return outputs
    finally:
        connection.close()


def wayland_outputs(connection, timeout=2):
    """Enumerate wl_output modes/scales; pure wire client, no globals besides wl_output.

    Bounds apply to the entire roundtrip including a trickling peer. The
    caller owns and closes this dedicated connection after the probe.
    """
    deadline = time.monotonic() + timeout
    previous_timeout = connection.gettimeout()
    received = 0

    def read(size):
        nonlocal received
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Wayland output probe timed out')
            connection.settimeout(remaining)
            part = connection.recv(size - len(data))
            if not part:
                raise RuntimeError('Compositor disconnected during output probe')
            received += len(part)
            if received > 256 * 1024:
                raise RuntimeError('Wayland output probe exceeds limit')
            data.extend(part)
        return bytes(data)

    try:
        connection.settimeout(timeout)
        # Object ids must be allocated sequentially (registry=2, first sync=3,
        # then binds and the second sync in order): KWin 6.6 rejects new_ids
        # beyond max_used+1. Sequential allocation is legal everywhere.
        connection.sendall(struct.pack('=6I', 1, (12 << 16) | 1, 2, 1, 12 << 16, 3))
        outputs = {}
        next_id = 4
        sync2_id = 0
        while True:
            obj, header = struct.unpack('=2I', read(8))
            size, opcode = header >> 16, header & 0xffff
            if size < 8 or size % 4:
                raise RuntimeError('Malformed Wayland output event')
            body = read(size - 8)
            if obj == 1 and opcode == 0:
                raise RuntimeError('Compositor rejected output probe')
            if obj == 3 and opcode == 0 and len(body) == 4:
                # All globals arrived; the wl_output binds were sent after this
                # sync request, so their events follow a second roundtrip.
                sync2_id = next_id
                connection.sendall(struct.pack('=2I', 1, 12 << 16) + struct.pack('=I', sync2_id))
                continue
            if sync2_id and obj == sync2_id and opcode == 0 and len(body) == 4:
                result = []
                for entry in outputs.values():
                    if not entry['done'] or entry['mode'] is None:
                        raise RuntimeError('Incomplete wl_output advertisement')
                    w, h = entry['mode']
                    if not (1 <= w <= CAPACITY_MAX_AXIS and 1 <= h <= CAPACITY_MAX_AXIS):
                        raise RuntimeError('Output mode out of bounds')
                    result.append({'name': entry['name'] or None, 'w': w, 'h': h,
                                   'scale': float(entry['scale'] or 1)})
                return result
            if obj == 1 and opcode == 1 and len(body) == 4:
                continue
            if obj == 2 and opcode == 1 and len(body) == 4:
                continue
            if obj == 2 and opcode == 0 and len(body) >= 16:
                identifier, length = struct.unpack_from('=2I', body)
                padded = (length + 3) & ~3
                if not 2 <= length <= 256 or len(body) != 12 + padded:
                    raise RuntimeError('Malformed global name')
                raw = body[8:8 + length]
                if raw[-1] != 0 or not re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_]*', raw[:-1]):
                    raise RuntimeError('Invalid global name')
                version = struct.unpack_from('=I', body, 8 + padded)[0]
                if not identifier or not version:
                    raise RuntimeError('Invalid global')
                if raw[:-1] == b'wl_output':
                    bind_id = next_id
                    next_id += 1
                    outputs[bind_id] = {'name': '', 'mode': None, 'scale': 0, 'done': False}
                    interface = b'wl_output\0'
                    connection.sendall(struct.pack('=2I', 2, ((24 + len(interface) + len(interface) % 4) << 16) | 0) +
                                       struct.pack('=I', identifier) +
                                       struct.pack('=I', len(interface)) + interface +
                                       b'\0' * (-len(interface) % 4) +
                                       struct.pack('=I', min(version, 4)) +
                                       struct.pack('=I', bind_id))
                continue
            if obj in outputs:
                entry = outputs[obj]
                if opcode == 0:  # geometry (make/model strings may be non-empty)
                    if len(body) < 32:
                        raise RuntimeError('Malformed output geometry')
                    continue
                if opcode == 1:  # mode
                    if len(body) != 16:
                        raise RuntimeError('Malformed output mode')
                    flags, width, height, _refresh = struct.unpack_from('=4i', body)
                    if entry['mode'] is None or (flags & 1):
                        entry['mode'] = (width, height)
                    continue
                if opcode == 2:  # done
                    if len(body) != 0:
                        raise RuntimeError('Malformed output done')
                    entry['done'] = True
                    continue
                if opcode == 3:  # scale
                    if len(body) != 4:
                        raise RuntimeError('Malformed output scale')
                    entry['scale'] = struct.unpack_from('=i', body)[0]
                    continue
                if opcode == 4:  # name (v4)
                    if len(body) < 4:
                        raise RuntimeError('Malformed output name')
                    length = struct.unpack_from('=I', body)[0]
                    padded = (length + 3) & ~3
                    if length > 256 or len(body) != 4 + padded:
                        raise RuntimeError('Malformed output name')
                    raw = body[4:4 + length]
                    if raw[-1] != 0:
                        raise RuntimeError('Malformed output name')
                    entry['name'] = raw[:-1].decode('utf-8', 'replace')
                    continue
                if opcode == 5:  # description (v4)
                    continue
                raise RuntimeError('Unexpected wl_output event')
            raise RuntimeError('Unexpected Wayland output event')
    finally:
        connection.settimeout(previous_timeout)
