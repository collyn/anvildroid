#!/usr/bin/env python3
"""Lifecycle only: fake child processes/private sockets, no Android or GPU."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time

spec = importlib.util.spec_from_file_location('native_x11', Path(__file__).with_name('runtime-x11.py'))
x11 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(x11)

STUB = '''#!/usr/bin/python3
import os,signal,socket,sys,time
p=sys.argv[sys.argv.index('--socket')+1]
mode=os.environ.get('TEST_MODE','ready')
if mode=='crash':sys.exit(7)
if mode=='silent':time.sleep(30)
s=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);s.bind(p)
print('x11_adapter_ready protocol=1 socket='+p,flush=True)
if mode in ('close','false-close'):
 print('full_ui_closed=1',flush=True);sys.exit(0 if mode=='close' else 7)
while True:signal.pause()
'''


def main():
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise RuntimeError('Run lifecycle checks as the desktop user')
    checks = []
    with tempfile.TemporaryDirectory(prefix='anvil-x11-lifecycle-') as temporary:
        root = Path(temporary)
        executable = root / 'presenter'
        executable.write_text(STUB)
        executable.chmod(0o700)
        sessions = []

        def session(name, mode='ready'):
            s = x11.Session(root / name, executable, root / (name + '.log'), uid, gid,
                ':0', '/dev/null', 'amd', '123456789abc', dict(TEST_MODE=mode))
            sessions.append(s)
            s.start()
            return s

        def fail(callback, error=RuntimeError):
            try:
                callback()
            except error:
                return
            raise AssertionError('Expected ' + error.__name__)

        def exited(s, expected):
            deadline = time.monotonic() + 2
            while s.process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            fail(s.check, expected)

        try:
            a, b = session('a'), session('b')
            a.wait_listening(); b.wait_listening()
            a.check(); b.check()
            assert a.state == b.state == 'ready'
            assert a.directory != b.directory
            assert a.directory.stat().st_mode & 0o777 == 0o700
            checks.append('private-generation-and-endpoint-readiness')
            order = []
            def guest():
                assert a.process.poll() is None
                order.append('guest')
            def renderer():
                assert a.process.poll() is not None
                order.append('renderer')
            a.close(guest, renderer)
            assert order == ['guest', 'renderer'] and not a.directory.exists()
            b.check(); assert b.state == 'ready'
            checks.append('stop-order-and-runtime-isolation')
            a.close(lambda: order.append('wrong'), lambda: order.append('wrong'))
            assert order == ['guest', 'renderer']
            fail(a.start)
            checks.append('idempotent-stop-no-generation-reuse')
            def broken_guest():
                raise RuntimeError('guest still running')
            fail(lambda: b.close(broken_guest, lambda: order.append('wrong')))
            b.check(); assert b.process.poll() is None and order == ['guest', 'renderer']
            checks.append('failed-guest-stop-retains-gpu')
            fail(lambda: x11.Session(b.directory, executable, root/'duplicate.log', uid, gid,
                ':0', '/dev/null', 'amd', '123456789abc'), FileExistsError)
            checks.append('stale-generation-refused')
            exited(session('crash', 'crash'), RuntimeError)
            exited(session('close', 'close'), RuntimeError)
            exited(session('false-close', 'false-close'), RuntimeError)
            checks.extend(['crash-is-failure', 'presenter-exit-is-not-window-close', 'failed-exit-cannot-spoof-close'])
            silent = session('silent', 'silent')
            fail(lambda: silent.wait_listening(timeout=.15))
            checks.append('bounded-startup')
            (root / 'old').mkdir()
            moved = session('replacement')
            moved.wait_listening()
            moved.directory.rename(root / 'old' / 'original')
            moved.directory.mkdir()
            sentinel = moved.directory / 'token'
            sentinel.write_text('another generation')
            moved.close(lambda: None, lambda: None)
            assert sentinel.read_text() == 'another generation'
            checks.append('replacement-directory-not-touched')
            lost = session('already-removed')
            lost.wait_listening()
            for p in lost.directory.iterdir():
                p.unlink()
            lost.directory.rmdir()
            def renderer_failure():
                raise RuntimeError('renderer teardown failed')
            fail(lambda: lost.close(lambda: None, renderer_failure))
            assert lost.process.poll() is not None
            checks.append('cleanup-does-not-mask-renderer-failure')
        finally:
            for s in sessions:
                if s.state != 'stopped':
                    s.close(lambda: None, lambda: None)
    for name in checks:
        print('PASS:', name)
    print('PASS:', len(checks), 'native X11 lifecycle checks')


if __name__ == '__main__':
    main()
