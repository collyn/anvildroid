#!/usr/bin/env python3
"""Disposable control-only handshake in one running managed Android runtime.

Run with sudo python3 -B native/input/scrcpy-handshake.py RUNTIME SERVER_JAR.
No input is emitted. The guest server has a 15-second lifetime limit. This is
a diagnostic, not a production endpoint or a way to install Android apps.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import time

from scrcpy_control import SERVER_SHA256, DIAGNOSTIC_SHA256, server_arguments, socket_name, verify_handshake


def descendant(pid, ancestor):
    for _ in range(32):
        if pid == ancestor:
            return True
        if pid <= 1:
            break
        try:
            status = Path(f'/proc/{pid}/status').read_text()
            pid = int(next(line.split()[1] for line in status.splitlines()
                           if line.startswith('PPid:')))
        except (OSError, StopIteration, ValueError):
            return False
    return False


def main(identifier, artifact, probe=None):
    if os.geteuid() != 0 or not re.fullmatch(r'r-[0-9a-f]{32}', identifier):
        raise ValueError('Run as root with an explicit managed runtime ID')
    data = Path(artifact).read_bytes()
    artifact_hash = hashlib.sha256(data).hexdigest()
    if artifact_hash not in (SERVER_SHA256, DIAGNOSTIC_SHA256):
        raise ValueError('Expected the reviewed scrcpy 3.3.1 server artifact')
    instance = Path('/var/lib/anvildroid-controller/instances') / identifier
    with socket.socket(socket.AF_UNIX) as worker_socket:
        worker_socket.settimeout(3)
        worker_socket.connect(str(instance / 'worker.sock'))
        worker_pid, uid, _ = struct.unpack('3i', worker_socket.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            raise PermissionError('Worker must belong to root')
        worker_socket.sendall(b'{"op":"status"}\n')
        status = json.loads(worker_socket.makefile('rb').readline(16385))
    if status.get('runtime_id') != identifier or status.get('state') != 'Running':
        raise RuntimeError('Selected runtime is not running')
    prefix = ['nsenter', '-t', str(worker_pid), '-m', '-n', '-p', '--']
    attach = prefix + ['lxc-attach', '-P', str(instance / 'android'), '-n', identifier]
    init_pid = subprocess.check_output(prefix + ['lxc-info', '-P', str(instance / 'android'),
                                      '-n', identifier, '-pH'], timeout=3, text=True).strip()
    if not init_pid.isdecimal() or int(init_pid) <= 1:
        raise RuntimeError('Invalid Android init PID')
    namespace = os.open(f'/proc/{worker_pid}/root/proc/{init_pid}/ns/net', os.O_RDONLY)
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('runtime_worker', root / 'services/runtime-worker.py')
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    def lxc(tool, name, *args, **kwargs):
        return subprocess.check_output(attach + list(args), text=True,
                                       timeout=kwargs.get('timeout', 3))

    environment = worker.shutdown_environment(lxc, identifier)
    scid = secrets.randbelow(0x80000000)
    guest = f'/data/local/tmp/anvil-p6-{scid:08x}.jar'
    process = None
    connection = None
    copied = False
    try:
        # Noclobber and a generated basename prevent replacing an existing file.
        subprocess.run(attach + ['--', '/system/bin/sh', '-c',
                       f'set -C; cat > {guest} && chmod 644 {guest}'],
                       input=data, check=True, timeout=5)
        copied = True
        process = subprocess.Popen(attach + environment + ['-u', '2000', '-g', '2000',
            '--set-var', f'CLASSPATH={guest}', '--', '/system/bin/timeout', '-s', 'KILL', '15',
            '/system/bin/app_process', '/system/bin', 'com.genymobile.scrcpy.Server'] +
            server_arguments(scid), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # Only this diagnostic process changes namespace. No TCP/ADB forwarding.
        os.setns(namespace, os.CLONE_NEWNET)
        deadline = time.monotonic() + 8
        while True:
            connection = socket.socket(socket.AF_UNIX)
            connection.settimeout(2)
            try:
                connection.connect(socket_name(scid))
                break
            except (ConnectionRefusedError, FileNotFoundError):
                connection.close()
                connection = None
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('scrcpy did not open its control socket')
                time.sleep(.1)
        peer_pid = struct.unpack('3i', connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
        if not descendant(peer_pid, process.pid):
            raise PermissionError('Socket peer is not the launched server')
        peer = verify_handshake(connection, peer_pid)
        result = probe(connection, lxc, identifier) if probe else None
        connection.close()
        connection = None
        output, _ = process.communicate(timeout=5)
        if process.returncode != 0:
            raise RuntimeError('Server did not exit cleanly: ' + output.decode(errors='replace'))
        print(json.dumps({'runtime': identifier, 'generation': status['generation'],
                          'server_sha256': artifact_hash, 'peer': peer,
                          'handshake': '00', 'touch_sent': probe is not None, 'probe': result,
                          'clean_exit': True, 'server_log': output.decode(errors='replace')}))
    finally:
        if connection is not None:
            connection.close()
        if process is not None and process.poll() is None:
            # The guest timeout also bounds failures before socket acceptance.
            output, _ = process.communicate(timeout=20)
            print(output.decode(errors='replace'), file=sys.stderr)
        os.close(namespace)
        if copied:
            subprocess.run(attach + ['--', '/system/bin/rm', '-f', guest], check=True, timeout=3)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
