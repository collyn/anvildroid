#!/usr/bin/python3
"""User-session D-Bus persistence for KWin (whose JS API has no writeConfig)."""
import json
import math
import os
from pathlib import Path
import re
import tempfile

BUS_NAME = 'org.anvildroid.WindowPositions'
APP = re.compile(r'(?:waydroid\.|anvildroid\.r-[a-f0-9]{32}\.waydroid\.)[A-Za-z0-9_.-]+')


class PositionStore:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, key):
        if len(key) > 240 or not APP.fullmatch(key):
            raise ValueError('Invalid app identity')
        return self.root / (key + '.json')

    @staticmethod
    def validate(value):
        if not isinstance(value, dict) or set(value) != {'x', 'y', 'output'}:
            raise ValueError('Invalid position')
        for axis in ('x', 'y'):
            n = value[axis]
            if type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 1000000:
                raise ValueError('Invalid coordinate')
        if not isinstance(value['output'], str) or not 1 <= len(value['output']) <= 256:
            raise ValueError('Invalid output')
        return value

    def load(self, key):
        path = self.path(key)
        try:
            with path.open() as stream:
                record = json.loads(stream.read(4097))
            if not isinstance(record, dict) or record.get('version') != 1 or record.get('app') != key:
                raise ValueError('Invalid position scope/version')
            return self.validate(record['position'])
        except FileNotFoundError:
            return None

    def save(self, key, value):
        path = self.path(key)
        value = self.validate(value)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Refuse to overwrite corrupt or future-version state.
        self.load(key)
        fd, tmp = tempfile.mkstemp(prefix='.pending-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'version': 1, 'app': key, 'position': value}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def main():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    name = dbus.service.BusName(BUS_NAME, bus, do_not_queue=True)
    root = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    store = PositionStore(root / 'anvildroid/window-positions')

    class Service(dbus.service.Object):
        @dbus.service.method(BUS_NAME, in_signature='s', out_signature='s')
        def Load(self, key):
            return json.dumps(store.load(str(key)))

        @dbus.service.method(BUS_NAME, in_signature='ss', out_signature='b')
        def Save(self, key, value):
            if len(value) > 4096:
                return False
            try:
                store.save(str(key), json.loads(str(value)))
                return True
            except (OSError, ValueError, KeyError):
                return False

    service = Service(bus, '/Positions')
    loop = GLib.MainLoop()
    loop.run()


if __name__ == '__main__':
    main()
