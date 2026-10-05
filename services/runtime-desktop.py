"""Socket hints and bounded protocol prerequisites; no per-window SSD guarantee."""
from pathlib import Path
import os
import pwd
import re
import socket
import stat
import struct
import time


def registry_protocols(connection, timeout=2):
    """Inventory an authenticated, fresh Wayland connection without binding globals.

    Only wl_display.get_registry/sync are sent. No surfaces or input objects are
    created. Bounds apply to the entire roundtrip, including a trickling peer.
    The caller owns and closes this dedicated connection after the probe.
    """
    deadline = time.monotonic() + timeout
    previous_timeout = connection.gettimeout()
    received = 0
    globals_ = {}

    def read(size):
        nonlocal received
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Desktop protocol probe timed out')
            connection.settimeout(remaining)
            part = connection.recv(size - len(data))
            if not part:
                raise RuntimeError('Desktop compositor disconnected during protocol probe')
            received += len(part)
            if received > 256 * 1024:
                raise RuntimeError('Desktop protocol inventory exceeds limit')
            data.extend(part)
        return bytes(data)

    try:
        connection.settimeout(timeout)
        # Native-endian Wayland headers: object, (size << 16) | opcode.
        connection.sendall(struct.pack('=6I', 1, (12 << 16) | 1, 2,
                                       1, 12 << 16, 3))
        while True:
            obj, header = struct.unpack('=2I', read(8))
            size, opcode = header >> 16, header & 0xffff
            if size < 8 or size % 4:
                raise RuntimeError('Malformed desktop protocol event')
            body = read(size - 8)
            if obj == 1 and opcode == 0:
                raise RuntimeError('Desktop compositor rejected protocol probe')
            if obj == 3 and opcode == 0 and len(body) == 4:
                protocols = {}
                for name, version in globals_.values():
                    protocols[name] = max(protocols.get(name, 0), version)
                return protocols
            if obj == 1 and opcode == 1 and len(body) == 4:
                continue
            if obj == 2 and opcode == 1 and len(body) == 4:
                globals_.pop(struct.unpack('=I', body)[0], None)
                continue
            if obj != 2 or opcode != 0 or len(body) < 16:
                raise RuntimeError('Unexpected desktop protocol event')
            identifier, length = struct.unpack_from('=2I', body)
            padded = (length + 3) & ~3
            if not 2 <= length <= 256 or len(body) != 12 + padded:
                raise RuntimeError('Malformed desktop protocol name')
            raw = body[8:8 + length]
            if raw[-1] != 0 or not re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_]*', raw[:-1]):
                raise RuntimeError('Invalid desktop protocol name')
            version = struct.unpack_from('=I', body, 8 + padded)[0]
            if not identifier or not version or identifier in globals_:
                raise RuntimeError('Invalid desktop protocol global')
            globals_[identifier] = (raw[:-1].decode('ascii'), version)
    finally:
        connection.settimeout(previous_timeout)


def require_window_controls(protocols):
    """Advertisement is a prerequisite, not proof of per-window SSD acceptance."""
    if not protocols.get('xdg_wm_base'):
        raise RuntimeError('Desktop compositor does not provide xdg-shell windows. Use Headless mode or another Wayland session.')
    if not protocols.get('zxdg_decoration_manager_v1'):
        if not all(protocols.get(name) for name in ('wl_compositor', 'wl_subcompositor', 'wl_shm')):
            raise RuntimeError('Desktop compositor lacks the Wayland protocols required for AnvilDroid CSD')
        return


X11_TOKEN_PREFIX = 'x11:'
X11_ABS_SOCKET = re.compile(r'^/tmp/\.X11-unix/X\d+$')
X11_NUMBERED = re.compile(r'^(?:unix|localhost)?:(\d+)(?:\.\d+)?$')


def parse_x11_display(value):
    """Validate a DISPLAY string; return (transport, target) or None.
    'local' -> filesystem socket Path; 'tcp' -> ('127.0.0.1', 6000+n) for localhost: only."""
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    if X11_ABS_SOCKET.match(value):
        return ('local', Path(value))
    match = X11_NUMBERED.match(value)
    if not match:
        return None
    number = int(match.group(1))
    if value.startswith('localhost'):
        return ('tcp', ('127.0.0.1', 6000 + number))
    return ('local', Path('/tmp/.X11-unix') / ('X' + str(number)))


def x11_reachable(value):
    parsed = parse_x11_display(value)
    if parsed is None:
        return False
    transport, target = parsed
    if transport == 'local':
        try:
            return stat.S_ISSOCK(target.lstat().st_mode)
        except OSError:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        try:
            probe.connect(target)
            return True
        except OSError:
            return False


def x11_authority(uid):
    """Resolve the session's X authority file for uid, or None."""
    candidates = []
    try:
        user = pwd.getpwuid(uid)
    except KeyError:
        return None
    candidates += [Path(f'/run/user/{uid}/gdm/Xauthority'), Path(user.pw_dir) / '.Xauthority']
    for candidate in candidates:
        try:
            if candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def session_x11_display(uid):
    """Most common DISPLAY among uid-owned processes (a root service has no session env)."""
    inherited = os.environ.get('DISPLAY')
    if inherited and parse_x11_display(inherited):
        return inherited
    counts, hits = {}, 0
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            data = (entry / 'environ').read_bytes()[:65536]
        except OSError:
            continue
        for token in data.split(b'\0'):
            if token.startswith(b'DISPLAY='):
                value = token[8:].decode('ascii', 'replace')
                if parse_x11_display(value):
                    counts[value] = counts.get(value, 0) + 1
                    hits += 1
        if hits >= 64:
            break
    return max(counts, key=counts.get) if counts else None


def capabilities(uid):
    if type(uid) is not int or uid < 0:
        return {'session': 'unknown', 'wayland': False, 'desktop': False,
                'decoration': 'unknown', 'placement': 'generic', 'detail': 'Owner session is unavailable.'}
    runtime = Path('/run/user') / str(uid)
    sockets = (sorted(runtime.glob('wayland-*')) + sorted(runtime.glob('anvildroid-gnome-*'))) if runtime.is_dir() else []
    sockets = [p for p in sockets if p.is_socket()]
    if not sockets:
        display = session_x11_display(uid)
        if display:
            reachable = x11_reachable(display)
            return {'session': 'x11', 'wayland': False, 'desktop': True,
                    'display': display, 'decoration': 'wm-managed',
                    'placement': 'wm-managed',
                    'detail': (f'X11 display {display} detected; AnvilDroid opens a nested Weston window inside this session.'
                               if reachable else
                               f'X11 display {display} found; server connectivity is verified at startup.')}
        return {'session': 'none', 'wayland': False, 'desktop': False,
                'decoration': 'unavailable', 'placement': 'generic',
                'detail': 'No Wayland socket or X11 display is available for this user. Headless mode can still run; Desktop mode needs a graphical login.'}
    return {'session': 'wayland', 'wayland': True, 'desktop': True,
            'socket': sockets[0].name, 'decoration': 'unverified',
            'placement': 'compositor-managed',
            'detail': 'Wayland socket detected; window controls are unverified. Startup probes the selected compositor for xdg-shell and SSD or the protocols required for CSD. Protocol availability does not establish per-window compatibility.'}
