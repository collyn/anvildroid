#!/usr/bin/env python3
"""Install/reload just the Plasma 6 Waydroid placement script; no runtime restart."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid


spec = importlib.util.spec_from_file_location('desktop_rules', Path(__file__).with_name('desktop-setup.py'))
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)
INITIAL_RULE = 'anvildroid-initial-placement'
# KWin 6.6 RuleSettings defaults placement to PlacementCentered. Apply (3)
# only initial maximize state, so later user maximize/restore remains available.
INITIAL_RULE_BODY = """Description=AnvilDroid initial app placement
wmclass=^waydroid[.](anvildroid[.]r-[a-f0-9]{32}[.])?[a-z0-9_]+([.][A-Za-z0-9_]+)+$
wmclassmatch=3
wmclasscomplete=false
placementrule=2
maximizehoriz=false
maximizehorizrule=3
maximizevert=false
maximizevertrule=3
"""


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    source = Path(__file__).resolve().parent / 'kwin-center'
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    target = data / 'kwin/scripts/anvildroid-center'
    backup = state / 'anvildroid/window-centering' / uuid.uuid4().hex
    run('qdbus6', 'org.kde.KWin', '/KWin', 'org.freedesktop.DBus.Peer.Ping')
    config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'kwinrulesrc'
    before = config.read_bytes() if config.exists() else b''
    updated = desktop.configure_rules(before.decode(), INITIAL_RULE, INITIAL_RULE_BODY)
    backup.mkdir(parents=True)
    (backup / 'kwinrulesrc.before').write_bytes(before)
    enabled = run('kreadconfig6', '--file', 'kwinrc', '--group', 'Plugins', '--key', 'anvildroid-centerEnabled', '--default', 'false')
    if target.exists():
        shutil.copytree(target, backup / 'script.before')
    (backup / 'record.json').write_text(json.dumps({'target': str(target), 'enabled_before': enabled}, indent=2))
    for relative in ['metadata.json', 'contents/code/main.js']:
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = dest.with_suffix(dest.suffix + '.tmp')
        temp.write_bytes((source / relative).read_bytes())
        temp.replace(dest)
    desktop.atomic(config, updated.encode())
    run('kwriteconfig6', '--file', 'kwinrc', '--group', 'Plugins', '--key', 'anvildroid-centerEnabled', '--type', 'bool', 'true')
    run('qdbus6', 'org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting.unloadScript', 'anvildroid-center')
    run('qdbus6', 'org.kde.KWin', '/KWin', 'org.kde.KWin.reconfigure')
    run('qdbus6', 'org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting.start')
    loaded = run('qdbus6', 'org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting.isScriptLoaded', 'anvildroid-center')
    if loaded != 'true':
        raise SystemExit(f'KWin did not load the script; backup: {backup}')
    print(f'Window centering enabled. Backup: {backup}')


if __name__ == '__main__':
    main()
