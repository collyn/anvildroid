#!/usr/bin/python3
"""Desktop side of Android notification mirroring; runs as the desktop user.

stdin  (from the worker, one JSON object per line):
    {"op": "show", "key", "app", "title", "text", "sub_text", "icon" (PNG base64),
     "actions": [{"index", "title", "reply"}], "progress", "category", "alert",
     "clickable", "desktop_entry"}
    {"op": "close", "key"}
stdout (to the worker):
    {"event": "click"|"dismissed", "key"}
    {"event": "action", "key", "index"}
    {"event": "reply", "key", "index", "text"}

Talks only to org.freedesktop.Notifications on DBUS_SESSION_BUS_ADDRESS. On
stdin EOF it withdraws every notification it showed and exits.
"""
import base64
import html
import json
import sys
import time

BUS = 'org.freedesktop.Notifications'
PATH = '/org/freedesktop/Notifications'
REASON_DISMISSED = 2
# Some servers (Cinnamon) report a click/action as a user dismissal right
# afterwards; that close must not also dismiss the Android notification.
ACTION_GRACE = 2.0


class Relay:
    def __init__(self, server, emit, clock=time.monotonic):
        self.server, self.emit, self.clock = server, emit, clock
        self.capabilities = set(server.capabilities())
        self.ids = {}       # key -> notification id
        self.keys = {}      # notification id -> key
        self.replies = {}   # notification id -> Android action index with inline reply
        self.acted = {}     # notification id -> clock of last click/action
        self.closing = set()

    def body(self, message):
        text = message.get('text', '')
        sub = message.get('sub_text', '')
        if 'body-markup' in self.capabilities:
            text = html.escape(text, quote=False)
            sub = html.escape(sub, quote=False)
            return f'{text}\n<i>{sub}</i>' if sub else text
        return f'{text}\n{sub}' if sub else text

    def show(self, message):
        key = message['key']
        actions, hints = [], {}
        if message.get('clickable'):
            actions += ['default', 'Open']
        reply_index = None
        for action in message.get('actions', []):
            if action.get('reply'):
                if 'inline-reply' in self.capabilities and reply_index is None:
                    reply_index = action['index']
                    actions += ['inline-reply', action['title']]
                    hints['x-kde-reply-placeholder-text'] = action['title']
                elif message.get('clickable'):
                    # No inline reply here: replying means opening the app.
                    actions += ['default:' + str(action['index']), action['title']]
            else:
                actions += ['a' + str(action['index']), action['title']]
        if message.get('desktop_entry'):
            hints['desktop-entry'] = message['desktop_entry']
        if message.get('category') in ('call', 'alarm'):
            hints['urgency'] = 2
        if not message.get('alert', True):
            hints['suppress-sound'] = True
        progress = message.get('progress')
        if progress and progress.get('max', 0) > 0 and not progress.get('indeterminate'):
            hints['value'] = max(0, min(100, progress['value'] * 100 // progress['max']))
        if message.get('icon'):
            try:
                hints['image-data'] = base64.b64decode(message['icon'], validate=True)
            except ValueError:
                pass
        previous = self.ids.get(key, 0)
        identifier = self.server.notify(message.get('app') or 'Android', previous,
                                        message.get('title') or message.get('app') or '',
                                        self.body(message), actions, hints)
        if previous and previous != identifier:
            self.forget(previous)
        self.ids[key] = identifier
        self.keys[identifier] = key
        if reply_index is None:
            self.replies.pop(identifier, None)
        else:
            self.replies[identifier] = reply_index

    def forget(self, identifier):
        key = self.keys.pop(identifier, None)
        if key is not None and self.ids.get(key) == identifier:
            del self.ids[key]
        self.replies.pop(identifier, None)
        self.acted.pop(identifier, None)

    def close(self, key):
        identifier = self.ids.get(key)
        if identifier is None:
            return
        self.closing.add(identifier)
        self.forget(identifier)
        self.server.close(identifier)

    def close_all(self):
        for key in list(self.ids):
            self.close(key)

    def command(self, message):
        if not isinstance(message, dict) or not isinstance(message.get('key'), str):
            return
        if message.get('op') == 'show':
            self.show(message)
        elif message.get('op') == 'close':
            self.close(message['key'])

    # --- signals from the notification server ---
    def action_invoked(self, identifier, action):
        key = self.keys.get(identifier)
        if key is None:
            return
        self.acted[identifier] = self.clock()
        if action == 'default' or action.startswith('default:'):
            self.emit({'event': 'click', 'key': key})
        elif action.startswith('a') and action[1:].isdecimal():
            self.emit({'event': 'action', 'key': key, 'index': int(action[1:])})

    def replied(self, identifier, text):
        key = self.keys.get(identifier)
        index = self.replies.get(identifier)
        if key is not None and index is not None and isinstance(text, str) and text.strip():
            self.acted[identifier] = self.clock()
            self.emit({'event': 'reply', 'key': key, 'index': index, 'text': text[:4096]})

    def closed(self, identifier, reason):
        if identifier in self.closing:
            self.closing.discard(identifier)
            return
        key = self.keys.get(identifier)
        acted = self.acted.get(identifier)
        self.forget(identifier)
        if key is None or reason != REASON_DISMISSED:
            return  # expired popups stay in Android, like ChromeOS
        if acted is not None and self.clock() - acted < ACTION_GRACE:
            return
        self.emit({'event': 'dismissed', 'key': key})


class DBusServer:
    """org.freedesktop.Notifications through dbus-python."""

    def __init__(self, bus, dbus):
        self.dbus = dbus
        self.interface = dbus.Interface(bus.get_object(BUS, PATH), BUS)

    def capabilities(self):
        return [str(value) for value in self.interface.GetCapabilities()]

    def notify(self, app, replaces, summary, body, actions, hints):
        dbus = self.dbus
        values = {}
        for name, value in hints.items():
            if name == 'image-data':
                image = png_image_data(value)
                if image is not None:
                    values[name] = dbus.Struct(image[:6] + (dbus.ByteArray(image[6]),), signature='iiibiiay')
            elif isinstance(value, bool):
                values[name] = dbus.Boolean(value)
            elif isinstance(value, int):
                values[name] = dbus.Byte(value) if name == 'urgency' else dbus.Int32(value)
            else:
                values[name] = dbus.String(value)
        return int(self.interface.Notify(app, dbus.UInt32(replaces), '', summary, body,
                                         dbus.Array(actions, signature='s'),
                                         dbus.Dictionary(values, signature='sv'), dbus.Int32(-1)))

    def close(self, identifier):
        try:
            self.interface.CloseNotification(self.dbus.UInt32(identifier))
        except self.dbus.DBusException:
            pass


def png_image_data(data):
    """PNG bytes -> (width, height, rowstride, alpha, bits, channels, pixels)."""
    try:
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.write(data)
        loader.close()
        image = loader.get_pixbuf()
        return (image.get_width(), image.get_height(), image.get_rowstride(), image.get_has_alpha(),
                image.get_bits_per_sample(), image.get_n_channels(), image.get_pixels())
    except Exception:
        return None


def main():
    try:
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib
    except ImportError as error:
        print(json.dumps({'event': 'error', 'error': f'Desktop notifications need python3-dbus and python3-gi: {error}'}), flush=True)
        return 3
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    emit = lambda event: (sys.stdout.write(json.dumps(event, ensure_ascii=False) + '\n'), sys.stdout.flush())
    relay = Relay(DBusServer(bus, dbus), emit)
    bus.add_signal_receiver(lambda i, a: relay.action_invoked(int(i), str(a)), 'ActionInvoked', BUS, path=PATH)
    bus.add_signal_receiver(lambda i, r: relay.closed(int(i), int(r)), 'NotificationClosed', BUS, path=PATH)
    bus.add_signal_receiver(lambda i, t: relay.replied(int(i), str(t)), 'NotificationReplied', BUS, path=PATH)
    loop = GLib.MainLoop()
    buffer = bytearray()

    def readable(source, condition):
        chunk = sys.stdin.buffer.read1(65536) if condition & GLib.IO_IN else b''
        if not chunk:
            relay.close_all()
            loop.quit()
            return False
        buffer.extend(chunk)
        while b'\n' in buffer:
            line, _, rest = bytes(buffer).partition(b'\n')
            buffer[:] = rest
            try:
                relay.command(json.loads(line))
            except (ValueError, KeyError, TypeError, dbus.DBusException) as error:
                print(f'notification relay: {error}', file=sys.stderr, flush=True)
        if len(buffer) > 1024 * 1024:
            buffer.clear()
        return True

    GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, readable)
    print(json.dumps({'event': 'ready', 'capabilities': sorted(relay.capabilities)}), flush=True)
    loop.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
