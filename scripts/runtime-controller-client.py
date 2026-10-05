#!/usr/bin/python3
"""Client for experimental runtime metadata, image preparation and headless lifecycle."""
import argparse
import json
import os
from pathlib import Path
import socket
import struct


def request(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        _, server_uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if server_uid != 0:
            raise RuntimeError('Refusing controller endpoint not owned by root')
        connection.sendall(json.dumps(payload).encode() + b'\n')
        data = bytearray()
        while not data.endswith(b'\n'):
            part = connection.recv(65536)
            if not part or len(data) + len(part) > 4 * 1024 * 1024:
                raise RuntimeError('Invalid controller response')
            data.extend(part)
        return json.loads(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, default=Path('/run/anvildroid/control.sock'))
    commands = parser.add_subparsers(dest='op', required=True)
    commands.add_parser('list')
    commands.add_parser('requirements')
    commands.add_parser('create').add_argument('name')
    commands.add_parser('inspect').add_argument('id')
    commands.add_parser('health').add_argument('id')
    commands.add_parser('apps').add_argument('id')
    commands.add_parser('app_states').add_argument('id')
    commands.add_parser('keymaps').add_argument('id')
    commands.add_parser('app_job').add_argument('id')
    commands.add_parser('display_info').add_argument('id')
    display = commands.add_parser('display_set')
    display.add_argument('id')
    display.add_argument('mode', choices=['headless','desktop'])
    action = commands.add_parser('app_action')
    action.add_argument('id')
    action.add_argument('action', choices=['launch', 'force_stop', 'keymap_edit', 'full_ui'])
    action.add_argument('package')
    commands.add_parser('prepare').add_argument('id')
    for operation in ('start', 'stop', 'refresh'):
        operation_parser = commands.add_parser(operation)
        operation_parser.add_argument('id')
        if operation == 'start': operation_parser.add_argument('--display', default=os.environ.get('WAYLAND_DISPLAY'))
    commands.add_parser('storage_info').add_argument('id')
    move = commands.add_parser('move')
    move.add_argument('id')
    move.add_argument('destination')
    discard = commands.add_parser('discard_previous')
    discard.add_argument('id')
    discard.add_argument('confirmed_name')
    rename = commands.add_parser('rename')
    rename.add_argument('id')
    rename.add_argument('name')
    args = vars(parser.parse_args())
    path = args.pop('socket')
    if args.get('op') == 'start' and args.get('display') is None: args.pop('display')
    try:
        response = request(path, args)
        print(json.dumps(response, indent=2))
        raise SystemExit(0 if response['ok'] else 1)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'Controller request failed: {error}\n')
