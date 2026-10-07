#!/usr/bin/python3
"""Fresh KWin probe, with optional explicit actions scoped to one package.

The first report is a snapshot at request time. After an action a second probe
is required to verify the asynchronous configure/commit has completed.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
import argparse
import re

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)
loop = GLib.MainLoop()
bus = dbus.SessionBus()
name = dbus.service.BusName('org.anvildroid.P3', bus)
nonce = str(uuid.uuid4())
received = False
parser = argparse.ArgumentParser()
parser.add_argument('--activate', help='Optionally focus this exact Waydroid package before probing')
parser.add_argument('--action', choices=['maximize', 'restore', 'minimize', 'unminimize', 'close'])
parser.add_argument('--resize', nargs=2, type=int, metavar=('WIDTH', 'HEIGHT'))
parser.add_argument('--move', nargs=2, type=int, metavar=('X', 'Y'))
parser.add_argument('--runtime', default='default', help='Exact runtime ID for an activation/action target')
args = parser.parse_args()
if args.runtime != 'default' and not re.fullmatch(r'r-[a-f0-9]{32}', args.runtime):
    parser.error('Invalid runtime ID')
if (args.action or args.resize or args.move) and not args.activate:
    parser.error('An action requires an exact --activate package')
if args.resize and any(n < 64 or n > 8192 for n in args.resize):
    parser.error('Resize dimensions must be between 64 and 8192')
actions = {
    'maximize': 'w.setMaximize(true, true);',
    'restore': 'w.setMaximize(false, false);',
    'minimize': 'w.minimized = true;',
    'unminimize': 'w.minimized = false;',
    'close': 'w.closeWindow();',
}
action = actions.get(args.action, '')
if args.resize:
    action += 'var g=w.frameGeometry; w.frameGeometry={x:g.x,y:g.y,width:%d,height:%d};' % tuple(args.resize)
if args.move:
    action += 'var g=w.frameGeometry; w.frameGeometry={x:%d,y:%d,width:g.width,height:g.height};' % tuple(args.move)

class Probe(dbus.service.Object):
    @dbus.service.method('org.anvildroid.P3', in_signature='s', out_signature='')
    def Report(self, value):
        global received
        record = json.loads(str(value))
        if record.get('nonce') == nonce:
            received = True
            print(json.dumps(record, indent=2), flush=True)
            if args.action or args.resize or args.move:
                GLib.timeout_add(2000, lambda: (loop.quit(), False)[1])
            else:
                loop.quit()

probe = Probe(bus, '/Probe')
script = r'''
var result = {nonce: NONCE, windows: []};
try {
    result.timerApi = typeof QTimer;
    result.activeOutput = workspace.activeScreen ? workspace.activeScreen.name : null;
    result.cursorOutput = workspace.screenAt(workspace.cursorPos).name;
    for (var w of workspace.windowList()) {
        if (!/^(waydroid(?:\.|$)|anvildroid\.r-[a-f0-9]{32}\.)/i.test(String(w.resourceClass))) continue;
        if (String(w.resourceClass) === ACTIVATE) { workspace.activeWindow = w; ACTION }
        var f = w.frameGeometry;
        var c = w.clientGeometry;
        var area = workspace.clientArea(KWin.MaximizeArea, w);
        result.windows.push({id: String(w.internalId), app: String(w.resourceClass),
            desktopFileName: String(w.desktopFileName),
            frame: {x:f.x,y:f.y,width:f.width,height:f.height},
            client: c ? {x:c.x,y:c.y,width:c.width,height:c.height} : null,
            workarea: {x:area.x,y:area.y,width:area.width,height:area.height},
            fullscreen:w.fullScreen, minimized:w.minimized, resizeable:w.resizeable,
            noBorder:w.noBorder, closeable:w.closeable, minimizeable:w.minimizeable,
            normal:w.normalWindow, dialog:w.dialog, maximizeMode:w.maximizeMode,
            output:w.output ? w.output.name : null,
            maxSize:w.maxSize, minSize:w.minSize, maximizeable:w.maximizeable});
    }
} catch(e) { result.error = String(e); }
callDBus('org.anvildroid.P3','/Probe','org.anvildroid.P3','Report',JSON.stringify(result));
'''.replace('NONCE', json.dumps(nonce)).replace('ACTIVATE', json.dumps(('waydroid.' if args.runtime == 'default' else 'waydroid.anvildroid.' + args.runtime + '.') + args.activate if args.activate else '')).replace('ACTION', action)

def db(*args):
    return subprocess.check_output(['qdbus6', 'org.kde.KWin', *args], text=True).strip()

with tempfile.TemporaryDirectory(prefix='anvildroid-probe-') as folder:
    path = Path(folder) / 'probe.js'
    path.write_text(script)
    plugin = 'anvildroid-probe-' + nonce
    sid = db('/Scripting', 'org.kde.kwin.Scripting.loadScript', str(path), plugin)
    try:
        db('/Scripting/Script' + sid, 'org.kde.kwin.Script.run')
        db('/Scripting', 'org.kde.kwin.Scripting.start')
        GLib.timeout_add(8000, lambda: (loop.quit(), False)[1])
        loop.run()
    finally:
        db('/Scripting', 'org.kde.kwin.Scripting.unloadScript', plugin)
if not received:
    raise SystemExit('KWin did not return a fresh probe result')
