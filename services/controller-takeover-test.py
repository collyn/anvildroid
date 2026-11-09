"""A socket-activated controller replaces one started outside systemd."""
import errno
import fcntl
import importlib.util
import os
from pathlib import Path
import signal
import socket
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('takeover_controller', HERE / 'runtime-controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
install_spec = importlib.util.spec_from_file_location('installer_umask', HERE.parent / 'scripts/install-runtime-controller.py')
installer = importlib.util.module_from_spec(install_spec)
install_spec.loader.exec_module(installer)
start_spec = importlib.util.spec_from_file_location('start_helper', HERE.parent / 'scripts/start-runtime-controller.py')
start = importlib.util.module_from_spec(start_spec)
start_spec.loader.exec_module(start)


def fake_proc(root, processes):
    """processes: {pid: argv list}; owned by the current user (tests run unprivileged)."""
    for pid, argv in processes.items():
        (root / str(pid)).mkdir()
        (root / str(pid) / 'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in argv) + b'\0')
    (root / 'self').mkdir()


class Takeover(unittest.TestCase):
    def test_finds_only_controllers_of_the_same_store(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            script = '/usr/local/lib/anvildroid-controller/runtime-controller.py'
            fake_proc(proc, {
                10: ['/usr/bin/python3', '-B', script, '--store', '/var/lib/anvildroid-controller', '--socket', '/run/anvildroid/control.sock'],
                11: ['/usr/bin/python3', '-B', script, '--store', '/srv/other'],
                12: ['/usr/bin/python3', '/usr/local/lib/anvildroid-controller/runtime-worker.py', '--store', '/var/lib/anvildroid-controller'],
                13: ['/usr/bin/python3', '-B', script, '--store', '/var/lib/anvildroid-controller'],
            })
            found = controller.standalone_controllers('/var/lib/anvildroid-controller', proc=proc, self_pid=13, root=os.getuid())
            self.assertEqual(found, [10])

    def test_kills_the_holder_and_waits_until_it_is_gone(self):
        alive = {41}
        killed = []
        def kill(pid, sig):
            killed.append((pid, sig))
            alive.discard(pid)
        pids = controller.take_over_store('/store', find=lambda _: sorted(alive), kill=kill, sleep=lambda _: None)
        self.assertEqual((pids, killed), ([41], [(41, signal.SIGKILL)]))

    def test_gives_up_waiting_after_the_deadline(self):
        now = [0.0]
        def sleep(seconds): now[0] += seconds
        controller.take_over_store('/store', wait=1, find=lambda _: [7], kill=lambda *_: None,
                                   sleep=sleep, clock=lambda: now[0])
        self.assertGreaterEqual(now[0], 1)

    def test_lock_probe_reports_a_held_store_without_taking_it(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(controller.store_locked(directory))       # no lock file yet
            lock = os.open(Path(directory) / 'controller.lock', os.O_RDWR | os.O_CREAT, 0o600)
            self.assertFalse(controller.store_locked(directory))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(controller.store_locked(directory))
            os.close(lock)
            self.assertFalse(controller.store_locked(directory))


class StartHelper(unittest.TestCase):
    def test_prefers_an_installed_socket_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, units = root / 'run', root / 'units'
            units.mkdir()
            self.assertFalse(start.systemd_unit_installed(run, (units,)))
            run.mkdir()
            self.assertFalse(start.systemd_unit_installed(run, (units,)))
            (units / start.SOCKET_UNIT).write_text('[Socket]\n')
            self.assertTrue(start.systemd_unit_installed(run, (units,)))

    def test_a_full_listen_queue_is_busy_not_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'control.sock'
            self.assertEqual(start.socket_state(path), 'absent')
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(path)); server.listen(0)
            try:
                state = 'live'
                for _ in range(64):            # fill the queue; nobody accepts
                    state = start.socket_state(path)
                    if state != 'live':
                        break
                self.assertEqual(state, 'busy')
            finally:
                server.close()
            # Nobody listens any more: stale for root-owned sockets (unsafe here, unprivileged).
            self.assertIn(start.socket_state(path), {'stale', 'unsafe'} if os.getuid() else {'stale'})


@unittest.skipUnless(os.geteuid() == 0, 'installation directories must be root-owned')
class InstallPermissions(unittest.TestCase):
    def test_directories_stay_traversable_under_a_private_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            old = os.umask(0o077)       # what start-runtime-controller.py sets
            try:
                created = Path(directory) / 'native-x11'
                installer.trusted_directory(created)
                self.assertEqual(created.stat().st_mode & 0o777, 0o755)
                broken = Path(directory) / 'old'
                broken.mkdir(mode=0o700)
                installer.trusted_directory(broken)  # left 0700 by earlier releases
                self.assertEqual(broken.stat().st_mode & 0o777, 0o755)
            finally:
                os.umask(old)


if __name__ == '__main__':
    unittest.main()
