#!/usr/bin/env python3
"""Real GLib pool regression; --mount-test uses two disposable mount namespaces.

No Waydroid, Binder, Android, or filesystem mounts are started. The optional
root test changes only the test process's namespaces and private propagation.
"""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location('native_pool_system', SCRIPT.with_name('runtime-system.py'))
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)


def task_namespace():
    # /proc/self/ns reflects the group leader, even inside a native worker.
    return os.readlink('/proc/thread-self/ns/mnt')


def native_threads():
    result = []
    for task in Path('/proc/self/task').iterdir():
        if int(task.name) == threading.get_native_id():
            continue
        try:
            result.append({'tid': int(task.name), 'name': (task / 'comm').read_text().strip(),
                           'namespace': os.readlink(task / 'ns/mnt')})
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


class RealPool:
    """Exercise public GLib APIs and record worker versus main-context delivery."""
    def __init__(self):
        self.glib = ctypes.CDLL('libglib-2.0.so.0')
        self.function = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
        self.source_function = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
        signatures = {
            'g_thread_pool_new': (ctypes.c_void_p, [self.function, ctypes.c_void_p, ctypes.c_int,
                                                  ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]),
            'g_thread_pool_push': (ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_void_p)]),
            'g_thread_pool_free': (None, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
            'g_thread_pool_stop_unused_threads': (None, []),
            'g_main_context_default': (ctypes.c_void_p, []),
            'g_main_context_iteration': (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            'g_idle_source_new': (ctypes.c_void_p, []),
            'g_source_set_callback': (None, [ctypes.c_void_p, self.source_function,
                                            ctypes.c_void_p, ctypes.c_void_p]),
            'g_source_attach': (ctypes.c_uint, [ctypes.c_void_p, ctypes.c_void_p]),
            'g_source_unref': (None, [ctypes.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.glib, name)
            function.restype, function.argtypes = result, arguments
        self.context = self.glib.g_main_context_default()

    def run(self):
        completed = threading.Event()
        delivered = threading.Event()
        observations, errors = {}, []

        @self.source_function
        def dispatch(_data):
            observations['dispatch'] = {'tid': threading.get_native_id(), 'namespace': task_namespace()}
            delivered.set()
            return False

        @self.function
        def work(_data, _user_data):
            try:
                observations['worker'] = {'tid': threading.get_native_id(), 'namespace': task_namespace()}
                source = self.glib.g_idle_source_new()
                assert source, 'GLib idle source allocation failed'
                try:
                    self.glib.g_source_set_callback(source, dispatch, None, None)
                    assert self.glib.g_source_attach(source, self.context), 'GLib source attachment failed'
                finally:
                    self.glib.g_source_unref(source)
            except BaseException as error:
                errors.append(repr(error))
            finally:
                completed.set()

        error = ctypes.c_void_p()
        pool = self.glib.g_thread_pool_new(work, None, 1, False, ctypes.byref(error))
        assert pool and not error.value, 'GLib test pool allocation failed'
        try:
            assert self.glib.g_thread_pool_push(pool, ctypes.c_void_p(1), ctypes.byref(error))
            assert not error.value and completed.wait(3), 'Native GLib worker did not complete'
            assert not errors, errors
            deadline = time.monotonic() + 3
            while not delivered.is_set():
                self.glib.g_main_context_iteration(self.context, False)
                assert time.monotonic() < deadline, 'Main-context delivery timed out'
                time.sleep(.001)
        finally:
            self.glib.g_thread_pool_free(pool, True, True)
        observations['threads'] = native_threads()
        return observations

    def retire_worker(self, tid):
        # Force each subsequent cycle to exercise a newly spawned worker,
        # rather than merely reusing the first correctly isolated thread.
        self.glib.g_thread_pool_stop_unused_threads()
        deadline = time.monotonic() + 3
        while Path('/proc/self/task', str(tid)).exists():
            assert time.monotonic() < deadline, 'Unused GLib worker did not exit'
            time.sleep(.005)


def child(primed, sessions=False):
    baseline = task_namespace()
    owner = threading.get_native_id()
    assert not native_threads(), 'Test must start without inherited native threads'
    if primed:
        system.prepare_native_thread_pool()
    pool = RealPool()
    baseline_fd = os.open('/proc/thread-self/ns/mnt', os.O_RDONLY)
    records = []
    first_session = None
    try:
        for cycle in range(3 if sessions and primed else 1):
            if sessions:
                os.unshare(os.CLONE_NEWNS)
                assert task_namespace() != baseline
            session = task_namespace()
            if first_session is None:
                first_session = session
            try:
                observation = pool.run()
                expected_worker = baseline if primed else first_session
                assert observation['worker']['tid'] != owner, observation
                assert observation['worker']['namespace'] == expected_worker, observation
                assert observation['dispatch'] == {'tid': owner, 'namespace': session}, observation
                spawners = [task for task in observation['threads'] if task['name'] == 'pool-spawner']
                assert spawners, observation
                assert all(task['namespace'] == expected_worker for task in observation['threads']), observation
                records.append({'cycle': cycle, 'session': session, **observation})
                if sessions and primed:
                    pool.retire_worker(observation['worker']['tid'])
            finally:
                # The deliberately unprimed case retains its first session
                # until process exit, just as the production holder guard
                # requires. Its native threads also share CLONE_FS state.
                if sessions and primed:
                    os.setns(baseline_fd, os.CLONE_NEWNS)
                    assert task_namespace() == baseline
    finally:
        os.close(baseline_fd)
    print(json.dumps({'primed': primed, 'baseline': baseline, 'cycles': records}))


class NativePoolTests(unittest.TestCase):
    def test_real_glib_worker_and_main_context_delivery_in_fresh_process(self):
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--native-child'],
                                text=True, capture_output=True, timeout=15, check=True)
        value = json.loads(result.stdout)
        self.assertTrue(value['primed'])
        self.assertEqual(len(value['cycles']), 1)

    def test_library_load_failure_aborts_before_session(self):
        with patch.object(system.ctypes, 'CDLL', side_effect=OSError('missing GLib')):
            with self.assertRaisesRegex(OSError, 'missing GLib'):
                system.prepare_native_thread_pool()

    def fake_glib(self, pool, error):
        def allocate(_callback, _data, _count, _exclusive, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).contents.value = error
            return pool
        return types.SimpleNamespace(g_thread_pool_new=Mock(side_effect=allocate),
                                     g_thread_pool_free=Mock(), g_error_free=Mock())

    def test_null_pool_fails_closed(self):
        glib = self.fake_glib(None, None)
        with patch.object(system.ctypes, 'CDLL', return_value=glib):
            with self.assertRaisesRegex(RuntimeError, 'before starting a session'):
                system.prepare_native_thread_pool()
        glib.g_thread_pool_free.assert_not_called()
        glib.g_error_free.assert_not_called()

    def test_error_fails_closed_and_frees_both_resources(self):
        glib = self.fake_glib(123, 456)
        with patch.object(system.ctypes, 'CDLL', return_value=glib):
            with self.assertRaisesRegex(RuntimeError, 'before starting a session'):
                system.prepare_native_thread_pool()
        glib.g_thread_pool_free.assert_called_once_with(123, True, True)
        self.assertEqual(glib.g_error_free.call_args.args[0].value, 456)


def mount_test():
    if os.geteuid() != 0:
        raise RuntimeError('--mount-test requires root for isolated mount namespaces')
    original = task_namespace()
    reports = []
    for mode in ('unprimed', 'primed'):
        result = subprocess.run(['unshare', '--mount', '--propagation', 'private',
                                 sys.executable, '-B', str(SCRIPT), '--namespace-child', mode],
                                text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise RuntimeError(mode + ' namespace test failed:\n' + result.stdout + result.stderr)
        report = json.loads(result.stdout)
        assert report['baseline'] != original, report
        # Linux can immediately reuse a destroyed namespace's inode number;
        # every cycle independently checks successful unshare and baseline return.
        assert len(report['cycles']) == (3 if mode == 'primed' else 1), report
        reports.append(report)
    assert task_namespace() == original, 'Parent mount namespace changed'
    print(json.dumps({'status': 'PASS', 'host_namespace_unchanged': True, 'reports': reports}, indent=2))


if __name__ == '__main__':
    if sys.argv[1:] == ['--native-child']:
        child(True)
    elif len(sys.argv) == 3 and sys.argv[1] == '--namespace-child':
        child(sys.argv[2] == 'primed', sessions=True)
    elif sys.argv[1:] == ['--mount-test']:
        mount_test()
    else:
        unittest.main()
