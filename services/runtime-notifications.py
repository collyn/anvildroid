"""Mirror Android notifications to the desktop, like ARC on ChromeOS.

Worker side (root). The companion APK's NotificationListenerService listens on
the abstract socket ``org.anvildroid.notify.v1`` inside the container's network
namespace and accepts only root. This module connects there, applies the
owner's per-app policy, rate-limits updates, and drives a relay process that
runs as the desktop user and talks to org.freedesktop.Notifications.

Policy file (instance/notifications.json, written by the controller):
    {"enabled": true, "packages": {"com.example.app": false}}
Missing file: enabled, every app allowed (ChromeOS/Android default).
"""
import json
import os
from pathlib import Path
import re
import select
import socket
import struct
import subprocess
import threading
import time

SOCKET_NAME = 'org.anvildroid.notify.v1'
COMPANION = 'org.anvildroid.ime'
LISTENER = COMPANION + '/' + COMPANION + '.NotifyListener'
FRAME_LIMIT = 262144
MAX_SHOWN = 50
UPDATE_INTERVAL = 1.0
# The listener answers pings; a silent link is treated as lost so a missed
# EOF (e.g. after Android rebinds the listener) never stalls mirroring.
PING_INTERVAL = 15.0
LINK_TIMEOUT = 35.0
PACKAGE = re.compile(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+')
CLONE_NEWNET = 0x40000000


def validate_policy(value):
    if not isinstance(value, dict) or set(value) - {'enabled', 'packages'}:
        raise ValueError('Invalid notification policy')
    enabled = value.get('enabled', True)
    packages = value.get('packages', {})
    if type(enabled) is not bool or not isinstance(packages, dict) or len(packages) > 4096:
        raise ValueError('Invalid notification policy')
    for name, allowed in packages.items():
        if not isinstance(name, str) or len(name) > 255 or not PACKAGE.fullmatch(name) or type(allowed) is not bool:
            raise ValueError('Invalid notification policy entry')
    return {'enabled': enabled, 'packages': dict(packages)}


def read_policy(path):
    try:
        return validate_policy(json.loads(Path(path).read_text()))
    except FileNotFoundError:
        return {'enabled': True, 'packages': {}}


def write_policy(path, value):
    """Atomic, root-only replacement read by the worker's Policy."""
    value = validate_policy(value)
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return value


class Policy:
    """Owner policy, re-read when the controller replaces the file."""

    def __init__(self, path):
        self.path = Path(path)
        self.stamp = None
        self.value = {'enabled': True, 'packages': {}}

    def refresh(self):
        """Return True when the policy changed since the last call."""
        try:
            info = self.path.stat()
            stamp = (info.st_ino, info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            stamp = None
        if stamp == self.stamp:
            return False
        self.stamp = stamp
        value = {'enabled': True, 'packages': {}}
        if stamp is not None:
            try:
                value = validate_policy(json.loads(self.path.read_text()))
            except (OSError, ValueError):
                pass  # keep defaults rather than silencing every app on a bad file
        changed = value != self.value
        self.value = value
        return changed

    @property
    def enabled(self):
        return self.value['enabled']

    def allowed(self, package):
        return self.enabled and self.value['packages'].get(package, True)


def read_frame(sock, limit=FRAME_LIMIT):
    header = _read_exact(sock, 4)
    size = struct.unpack('>I', header)[0]
    if not 2 <= size <= limit:
        raise ValueError('Invalid notification frame size')
    return json.loads(_read_exact(sock, size).decode('utf-8'))


def write_frame(sock, value):
    data = json.dumps(value, ensure_ascii=False).encode('utf-8')
    if len(data) > 32768:
        raise ValueError('Notification request too large')
    sock.sendall(struct.pack('>I', len(data)) + data)


def _read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise ConnectionError('Android notification listener disconnected')
        data.extend(part)
    return bytes(data)


def companion_uid(packages_list):
    """UID Android assigned to the companion APK, from /data/system/packages.list."""
    try:
        with open(packages_list, 'rb') as stream:
            for line in stream.read(4 * 1024 * 1024).decode('utf-8', 'replace').splitlines():
                fields = line.split()
                if len(fields) >= 2 and fields[0] == COMPANION and fields[1].isdecimal():
                    uid = int(fields[1])
                    return uid if uid >= 10000 else None
    except OSError:
        return None
    return None


def connect_android(netns_path, packages_list, timeout=2):
    """Connect to the listener inside the container's network namespace.

    Abstract UNIX names belong to the namespace that created the socket, so
    the socket is created and connected after a thread-local setns, then the
    thread returns to its own namespace. The peer must be the companion UID.
    """
    expected = companion_uid(packages_list)
    if expected is None:
        raise RuntimeError('Notification companion is not installed')
    own = os.open('/proc/thread-self/ns/net', os.O_RDONLY | os.O_CLOEXEC)
    target = os.open(netns_path, os.O_RDONLY | os.O_CLOEXEC)
    sock = None
    try:
        os.setns(target, CLONE_NEWNET)
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect('\0' + SOCKET_NAME)
        finally:
            os.setns(own, CLONE_NEWNET)
        _, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != expected:
            raise RuntimeError('Unexpected notification listener identity')
        sock.settimeout(None)
        return sock
    except BaseException:
        if sock is not None:
            sock.close()
        raise
    finally:
        os.close(own)
        os.close(target)


def clean_item(item):
    """Validate one Android notification description from the listener."""
    if not isinstance(item, dict):
        return None
    key, package = item.get('key'), item.get('package')
    if not isinstance(key, str) or not 0 < len(key) <= 1024 or not isinstance(package, str) or not PACKAGE.fullmatch(package):
        return None
    text = lambda name, limit: (item.get(name) if isinstance(item.get(name), str) else '')[:limit]
    actions = []
    for action in item.get('actions', [])[:3] if isinstance(item.get('actions'), list) else []:
        if isinstance(action, dict) and type(action.get('index')) is int and 0 <= action['index'] < 16 \
                and isinstance(action.get('title'), str) and action['title']:
            actions.append({'index': action['index'], 'title': action['title'][:64], 'reply': action.get('reply') is True})
    result = {'key': key, 'package': package, 'app': text('app', 128) or package,
              'title': text('title', 512), 'text': text('text', 2048), 'sub_text': text('sub_text', 256),
              'category': text('category', 64), 'alert': item.get('alert') is not False,
              'clickable': item.get('clickable') is True, 'actions': actions,
              'icon': text('icon', 200000)}
    progress = item.get('progress')
    if isinstance(progress, dict) and type(progress.get('max')) is int and type(progress.get('value')) is int:
        result['progress'] = {'max': max(0, progress['max']), 'value': max(0, progress['value']),
                              'indeterminate': progress.get('indeterminate') is True}
    return result


class NotificationBridge(threading.Thread):
    """Android listener <-> policy <-> desktop relay, for one runtime session."""

    def __init__(self, identifier, policy, connect, start_relay, stopping, state, clock=time.monotonic):
        super().__init__(daemon=True, name='notification-bridge')
        self.identifier, self.policy, self.connect, self.start_relay = identifier, policy, connect, start_relay
        self.stopping, self.state, self.clock = stopping, state, clock
        self.link = None
        self.relay = None
        self.relay_buffer = b''
        self.shown = {}      # key -> package currently on the desktop
        self.last_sent = {}  # key -> clock of the last forwarded update
        self.pending = {}    # key -> newest update held back by rate limiting
        self.retry_at = 0
        self.last_received = 0
        self.last_ping = 0

    # --- relay (desktop user process) ---
    def relay_send(self, message):
        if self.relay is None or self.relay.poll() is not None:
            return False
        try:
            self.relay.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8'))
            self.relay.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def ensure_relay(self):
        if self.relay is not None and self.relay.poll() is None:
            return True
        if self.relay is not None:
            self.shown.clear()
            self.relay = None
        try:
            self.relay = self.start_relay()
            self.relay_buffer = b''
            return True
        except (OSError, RuntimeError) as error:
            self.state['notifications_error'] = str(error)[-256:]
            return False

    def close_relay(self):
        if self.relay is not None:
            try:
                self.relay.stdin.close()
                self.relay.wait(timeout=3)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.relay.kill()
            self.relay = None
        self.shown.clear()

    # --- Android side ---
    def android_send(self, message):
        if self.link is None:
            return
        try:
            write_frame(self.link, message)
        except (OSError, ValueError):
            self.drop_link()

    def drop_link(self):
        if self.link is not None:
            self.link.close()
            self.link = None

    # --- forwarding ---
    def show(self, item, now):
        if not self.policy.allowed(item['package']):
            self.hide(item['key'])
            return
        last = self.last_sent.get(item['key'])
        finished = 'progress' not in item or item['progress']['value'] >= item['progress']['max']
        if last is not None and now - last < UPDATE_INTERVAL and not finished:
            self.pending[item['key']] = item
            return
        if item['key'] not in self.shown and len(self.shown) >= MAX_SHOWN:
            oldest = min(self.shown, key=lambda key: self.last_sent.get(key, 0))
            self.hide(oldest)
        message = dict(item, op='show', desktop_entry=f'waydroid.anvildroid.{self.identifier}.{item["package"]}')
        if self.relay_send(message):
            self.shown[item['key']] = item['package']
            self.last_sent[item['key']] = now
            self.pending.pop(item['key'], None)

    def hide(self, key):
        self.pending.pop(key, None)
        self.last_sent.pop(key, None)
        if self.shown.pop(key, None) is not None:
            self.relay_send({'op': 'close', 'key': key})

    def android_message(self, message, now):
        kind = message.get('type') if isinstance(message, dict) else None
        if kind == 'snapshot':
            items = [clean_item(item) for item in message.get('items', [])[:200]] if isinstance(message.get('items'), list) else []
            current = {item['key'] for item in items if item}
            for key in list(self.shown):
                if key not in current:
                    self.hide(key)
            for item in items:
                if item:
                    self.show(item, now)
        elif kind == 'posted':
            item = clean_item(message.get('item'))
            if item:
                self.show(item, now)
        elif kind == 'removed' and isinstance(message.get('key'), str):
            self.hide(message['key'])

    def relay_event(self, event):
        if not isinstance(event, dict):
            return
        if event.get('event') == 'error' and isinstance(event.get('error'), str):
            self.state['notifications_error'] = event['error'][-256:]
            return
        key = event.get('key')
        # Only notifications this session put on the desktop can be acted on.
        if not isinstance(key, str) or key not in self.shown:
            return
        kind = event.get('event')
        if kind == 'click':
            self.android_send({'op': 'click', 'key': key})
        elif kind == 'action' and type(event.get('index')) is int:
            self.android_send({'op': 'action', 'key': key, 'index': event['index']})
        elif kind == 'reply' and type(event.get('index')) is int and isinstance(event.get('text'), str):
            self.android_send({'op': 'action', 'key': key, 'index': event['index'], 'reply': event['text'][:4096]})
        elif kind == 'dismissed':
            # Closed by the user on the desktop: dismiss it in Android too.
            self.shown.pop(key, None)
            self.android_send({'op': 'dismiss', 'key': key})

    def relay_lines(self):
        try:
            chunk = os.read(self.relay.stdout.fileno(), 65536)
        except OSError:
            chunk = b''
        if not chunk:
            self.relay.poll()
            return []
        self.relay_buffer += chunk
        if len(self.relay_buffer) > 1024 * 1024:
            self.relay_buffer = b''
            return []
        *lines, self.relay_buffer = self.relay_buffer.split(b'\n')
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        return events

    def step(self, timeout=1.0):
        now = self.clock()
        changed = self.policy.refresh()
        active = self.policy.enabled
        self.state['notifications_active'] = active
        if not active:
            self.drop_link()
            self.close_relay()
            self.state['notifications'] = 'Disabled'
            self.stopping.wait(timeout)
            return
        if changed and self.link is not None:
            # Re-read Android's list so newly allowed or blocked apps converge.
            self.drop_link()
        if not self.ensure_relay():
            self.state['notifications'] = 'Desktop notifications unavailable'
            self.stopping.wait(min(timeout * 5, 5))
            return
        if self.link is None and now >= self.retry_at:
            try:
                self.link = self.connect()
                self.last_received = self.last_ping = now
                self.state['notifications'] = 'Connected'
                self.state.pop('notifications_error', None)
            except (OSError, RuntimeError, ValueError) as error:
                self.state['notifications'] = 'Waiting for Android'
                self.state['notifications_error'] = str(error)[-256:]
                self.retry_at = now + 3
        if self.link is not None:
            if now - self.last_received > LINK_TIMEOUT:
                self.drop_link()
                self.state['notifications'] = 'Waiting for Android'
            elif now - self.last_ping >= PING_INTERVAL:
                self.last_ping = now
                self.android_send({'op': 'ping'})
        for key, item in list(self.pending.items()):
            if now - self.last_sent.get(key, 0) >= UPDATE_INTERVAL:
                self.show(item, now)
        readers = [self.relay.stdout] + ([self.link] if self.link is not None else [])
        try:
            ready, _, _ = select.select(readers, [], [], timeout)
        except (OSError, ValueError):
            ready = []
        if self.link is not None and self.link in ready:
            try:
                message = read_frame(self.link)
                self.last_received = self.clock()
                self.android_message(message, self.last_received)
            except (OSError, ValueError, ConnectionError):
                self.drop_link()
                self.retry_at = self.clock() + 1
        if self.relay is not None and self.relay.stdout in ready:
            for event in self.relay_lines():
                self.relay_event(event)

    def run(self):
        try:
            while not self.stopping.is_set():
                try:
                    self.step()
                except Exception as error:  # never take the worker down
                    self.state['notifications_error'] = str(error)[-256:]
                    self.drop_link()
                    self.stopping.wait(2)
        finally:
            self.drop_link()
            self.close_relay()
            self.state['notifications_active'] = False


def enable_listener(shell):
    """Grant the companion's listener and background-start exemption."""
    shell('cmd', 'notification', 'allow_listener', LISTENER)
    # A desktop click must open the app from the background (ChromeOS opens
    # the app window). SYSTEM_ALERT_WINDOW holders may start activities.
    shell('appops', 'set', COMPANION, 'SYSTEM_ALERT_WINDOW', 'allow')
