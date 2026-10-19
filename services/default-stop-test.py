#!/usr/bin/env python3
"""Inert asynchronous Default Stop and queued hardware callback regressions.

No service, Binder instance, Android process, mount, or namespace is created.
"""
import ast
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location('default_stop_system', Path(__file__).with_name('runtime-system.py'))
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)


class Arguments(types.SimpleNamespace):
    def __contains__(self, key):
        return hasattr(self, key)


class Context:
    """A deterministic main loop: callbacks run only when the test yields."""
    def __init__(self):
        self.now = 100.0
        self.sources = {}
        self.next_id = 1
        self.removed = []

    def timeout_add(self, milliseconds, callback):
        source = self.next_id
        self.next_id += 1
        self.sources[source] = (milliseconds, callback)
        return source

    def source_remove(self, source):
        self.removed.append(source)
        self.sources.pop(source, None)
        return True

    def dispatch(self, source, elapsed=.05):
        self.now += elapsed
        _interval, callback = self.sources[source]
        if not callback():
            self.sources.pop(source, None)


def integration_fixture():
    hardware = types.SimpleNamespace(add_service=Mock())
    tools = types.SimpleNamespace(
        actions=types.SimpleNamespace(container_manager=types.SimpleNamespace(
            do_start=Mock(), freeze=Mock(), stop=Mock())),
        helpers=types.SimpleNamespace(images=types.SimpleNamespace(mount_rootfs=Mock(), make_prop=Mock()),
                                      lxc=types.SimpleNamespace(status=Mock(return_value='STOPPED'))),
        services=types.SimpleNamespace(hardware_manager=types.SimpleNamespace(IHardware=hardware)))
    context = Context()
    integration = system.Integration(tools, context)
    integration.args = Arguments(hardware_manager=None)
    integration.sharing = Mock(active=True)
    return integration, context


class AsyncStopTests(unittest.TestCase):
    def setUp(self):
        self.integration, self.context = integration_fixture()
        self.reply, self.error = Mock(), Mock()
        self.clock = patch.object(system.time, 'monotonic', side_effect=lambda: self.context.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def stop(self):
        self.integration.stop_async(self.integration.args, True, self.reply, self.error)

    def test_deferred_cleanup_progresses_between_attempts_before_success_reply(self):
        events = []
        held = [True]

        def stock_stop(*_args):
            self.assertTrue(self.integration.stopping)
            self.assertIsNone(self.integration.session_token)
            events.append('stock-stop')

        def cleanup(wait=True):
            self.assertFalse(wait, 'Async Stop must never enter the blocking holder loop')
            events.append('guard')
            if held[0]:
                raise system.default_shares.NamespaceBusy('retained Binder object')
            self.integration.sharing.active = False
            events.append('namespace-marker-lock-clean')

        self.integration.session_token = object()
        self.integration.original_stop.side_effect = stock_stop
        self.integration.close = Mock(side_effect=cleanup)
        self.reply.side_effect = lambda: events.append('reply')
        self.stop()
        source = self.integration.stop_timer
        self.assertEqual(events, ['stock-stop', 'guard'])
        self.assertTrue(self.integration.stopping)
        self.reply.assert_not_called()
        self.error.assert_not_called()
        self.assertEqual(self.context.sources[source][0], 50)
        # A deferred native destroy notifier can now run on the ordinary loop.
        events.append('native-release')
        held[0] = False
        stale_poll = self.context.sources[source][1]
        self.context.dispatch(source)
        self.assertEqual(events, ['stock-stop', 'guard', 'native-release', 'guard',
                                  'namespace-marker-lock-clean', 'reply'])
        self.integration.original_stop.assert_called_once_with(self.integration.args, True)
        self.assertFalse(self.integration.stopping)
        self.assertIsNone(self.integration.stop_timer)
        self.assertIn(source, self.context.removed)
        self.assertFalse(stale_poll())
        self.reply.assert_called_once_with()
        self.error.assert_not_called()
        self.assertEqual(self.integration.close.call_count, 2)

    def test_persistent_holder_errors_once_retains_state_and_later_stop_recovers(self):
        self.integration.close = Mock(side_effect=system.default_shares.NamespaceBusy('holder remains'))
        self.stop()
        source = self.integration.stop_timer
        stale_poll = self.context.sources[source][1]
        self.context.dispatch(source, elapsed=3.01)
        self.reply.assert_not_called()
        self.assertIsInstance(self.error.call_args.args[0], system.default_shares.NamespaceBusy)
        self.assertTrue(self.integration.sharing.active)
        self.assertFalse(self.integration.stopping)
        self.assertIsNone(self.integration.stop_timer)
        self.assertFalse(stale_poll())
        self.error.assert_called_once()
        self.integration.close.side_effect = None
        later_reply, later_error = Mock(), Mock()
        self.integration.stop_async(self.integration.args, False, later_reply, later_error)
        later_reply.assert_called_once_with()
        later_error.assert_not_called()
        self.assertEqual(self.integration.original_stop.call_count, 2)

    def test_original_stop_exception_survives_successful_deferred_cleanup(self):
        failure = ValueError('stock stop failed')
        self.integration.original_stop.side_effect = failure
        self.integration.close = Mock(side_effect=[system.default_shares.NamespaceBusy('pending'), None])
        self.stop()
        self.context.dispatch(self.integration.stop_timer)
        self.reply.assert_not_called()
        self.error.assert_called_once_with(failure)
        self.assertFalse(self.integration.stopping)

    def test_non_holder_failure_is_immediate_and_never_retried(self):
        failure = RuntimeError('Android is still active')
        self.integration.close = Mock(side_effect=failure)
        self.stop()
        self.error.assert_called_once_with(failure)
        self.reply.assert_not_called()
        self.assertFalse(self.context.sources)
        self.assertTrue(self.integration.sharing.active)

    def test_wrong_owner_never_starts_stop_or_invalidates_callback_token(self):
        token = self.integration.session_token = object()
        failure = RuntimeError('wrong namespace owner')
        self.integration.sharing.check_thread.side_effect = failure
        self.integration.close = Mock()
        self.stop()
        self.error.assert_called_once_with(failure)
        self.reply.assert_not_called()
        self.integration.original_stop.assert_not_called()
        self.integration.close.assert_not_called()
        self.assertIs(self.integration.session_token, token)
        self.assertFalse(self.integration.stopping)
        self.assertFalse(self.context.sources)

    def test_original_failure_has_priority_over_cleanup_failure(self):
        original = ValueError('stock stop failed')
        self.integration.original_stop.side_effect = original
        self.integration.close = Mock(side_effect=RuntimeError('cleanup failed too'))
        self.stop()
        self.error.assert_called_once_with(original)
        self.reply.assert_not_called()
        self.assertFalse(self.integration.stopping)

    def test_source_removal_failure_completes_once_and_cannot_repeat_cleanup(self):
        removal = OSError('source removal failed')
        self.integration.close = Mock(side_effect=[system.default_shares.NamespaceBusy('pending'), None])
        self.stop()
        source = self.integration.stop_timer
        stale_poll = self.context.sources[source][1]
        with patch.object(self.context, 'source_remove', side_effect=removal):
            self.context.dispatch(source)
        self.error.assert_called_once_with(removal)
        self.reply.assert_not_called()
        self.assertFalse(self.integration.stopping)
        self.assertIsNone(self.integration.stop_timer)
        self.assertFalse(stale_poll())
        self.error.assert_called_once_with(removal)
        self.assertEqual(self.integration.close.call_count, 2)

    def test_timer_allocation_failure_reports_error_and_retains_state(self):
        self.integration.close = Mock(side_effect=system.default_shares.NamespaceBusy('pending'))
        self.context.timeout_add = Mock(return_value=0)
        self.stop()
        self.error.assert_called_once()
        self.reply.assert_not_called()
        self.assertFalse(self.integration.stopping)
        self.assertTrue(self.integration.sharing.active)

    def test_concurrent_start_and_stop_cannot_cross_pending_cleanup(self):
        self.integration.close = Mock(side_effect=system.default_shares.NamespaceBusy('pending'))
        self.stop()
        self.integration.args.session = {'existing': 'session'}
        with self.assertRaisesRegex(RuntimeError, 'stopping'):
            self.integration.start(self.integration.args, {})
        self.integration.original_start.assert_not_called()
        duplicate_reply, duplicate_error = Mock(), Mock()
        self.integration.stop_async(self.integration.args, False, duplicate_reply, duplicate_error)
        duplicate_error.assert_called_once()
        duplicate_reply.assert_not_called()
        self.integration.original_stop.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, 'stopping'):
            self.integration.stop(self.integration.args)
        self.integration.original_stop.assert_called_once()

    def test_retained_session_blocks_new_start_after_timeout(self):
        with self.assertRaisesRegex(RuntimeError, 'cleanup is incomplete'):
            self.integration.start(self.integration.args, {})
        self.integration.original_start.assert_not_called()

    def test_watch_does_no_work_while_async_stop_pending(self):
        self.integration.stopping = True
        self.integration.renderer = Mock()
        self.integration.stop = Mock()
        self.assertTrue(self.integration.watch())
        self.integration.renderer.check.assert_not_called()
        self.integration.tools.helpers.lxc.status.assert_not_called()
        self.integration.sharing.reconcile.assert_not_called()
        self.integration.stop.assert_not_called()

    def test_real_close_rechecks_android_before_each_holder_attempt(self):
        self.integration.sharing.close.side_effect = system.default_shares.NamespaceBusy('pending')
        self.integration.timer = self.context.timeout_add(1000, self.integration.watch)
        watch_source = self.integration.timer
        with patch.object(system.gc, 'collect') as collect:
            self.stop()
            self.assertNotIn(watch_source, self.context.sources)
            self.integration.tools.helpers.lxc.status.return_value = 'RUNNING'
            self.context.dispatch(self.integration.stop_timer)
        self.error.assert_called_once()
        self.reply.assert_not_called()
        self.assertEqual(self.integration.tools.helpers.lxc.status.call_count, 2)
        self.integration.sharing.close.assert_called_once_with(True, wait=False)
        collect.assert_called_once_with()


class HardwareCallbackTests(unittest.TestCase):
    def test_callbacks_are_versioned_and_cannot_act_during_or_after_stop(self):
        integration, _context = integration_fixture()
        hardware = integration.tools.services.hardware_manager.IHardware
        original = hardware.add_service
        captured = []
        original.side_effect = lambda args, *callbacks: captured.append(callbacks) or 'registered'
        integration.install_hardware_callbacks()
        callbacks = [Mock(return_value=number + 10) for number in range(5)]
        integration.session_token = object()
        self.assertEqual(hardware.add_service(integration.args, *callbacks), 'registered')
        first = captured[-1]
        for callback, wrapped in zip(callbacks, first):
            self.assertEqual(wrapped('value', option=True), callback.return_value)
            callback.assert_called_once_with('value', option=True)
            callback.reset_mock()
        integration.stopping = True
        for wrapped in first:
            self.assertEqual(wrapped(), 0)
        integration.stopping = False
        integration.session_token = None
        for wrapped in first:
            self.assertEqual(wrapped(), 0)
        integration.session_token = object()  # A later Android session.
        hardware.add_service(integration.args, *callbacks)
        for wrapped in first:
            self.assertEqual(wrapped(), 0)
        for callback in callbacks:
            callback.assert_not_called()
        for callback, wrapped in zip(callbacks, captured[-1]):
            self.assertEqual(wrapped(), callback.return_value)
            callback.assert_called_once_with()

    def test_callback_registered_without_session_never_becomes_valid(self):
        integration, _context = integration_fixture()
        hardware = integration.tools.services.hardware_manager.IHardware
        original = hardware.add_service
        integration.install_hardware_callbacks()
        callbacks = [Mock() for _ in range(5)]
        hardware.add_service(integration.args, *callbacks)
        captured = original.call_args.args[1:]
        integration.session_token = object()
        for wrapped in captured:
            self.assertEqual(wrapped(), 0)
        for callback in callbacks:
            callback.assert_not_called()


class HolderGuardTests(unittest.TestCase):
    def test_nonblocking_holder_guard_never_sleeps_or_releases_resources(self):
        shares = system.default_shares
        session = shares.SessionMounts(Path('/unused'), None, Path('/unused'), 'unused')
        lock = session.lock = object()
        baseline = session.baseline = object()
        with patch.object(session, 'namespace_holders', return_value=['thread:42']), \
                patch.object(shares.time, 'sleep', side_effect=AssertionError('blocking wait')), \
                patch.object(shares.os, 'setns') as setns, patch.object(shares.os, 'close') as close:
            with self.assertRaises(shares.NamespaceBusy):
                session.close(True, wait=False)
        self.assertIs(session.lock, lock)
        self.assertIs(session.baseline, baseline)
        setns.assert_not_called()
        close.assert_not_called()

    def test_real_lock_and_marker_survive_busy_then_release_after_holder_exits(self):
        shares = system.default_shares
        with tempfile.TemporaryDirectory(prefix='anvil-stop-') as directory:
            root = Path(directory)
            lock_path = root / 'lifecycle.lock'
            marker = root / 'shares-session.json'
            marker.write_text('{"inert_fixture": true}')
            session = shares.SessionMounts(root, None, root, 'unused')
            session.lock = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            contender = os.open(lock_path, os.O_RDWR)
            fcntl.flock(session.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with patch.object(session, 'namespace_holders', return_value=['thread:42']):
                    with self.assertRaises(shares.NamespaceBusy):
                        session.close(True, wait=False)
                self.assertTrue(marker.is_file())
                self.assertTrue(session.active)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(session, 'namespace_holders', return_value=[]):
                    session.close(True, wait=False)
                self.assertFalse(marker.exists())
                self.assertFalse(session.active)
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                if session.lock is not None:
                    os.close(session.lock)
                os.close(contender)


class RealMainLoopTests(unittest.TestCase):
    def test_glib_context_keeps_dispatch_ownership_when_callback_releases_gil(self):
        try:
            from gi.repository import GLib
        except ImportError:
            self.skipTest('PyGObject is unavailable')
        context = GLib.MainContext.new()
        entered, release, second_called = threading.Event(), threading.Event(), threading.Event()
        ownership = []

        def first(*_data):
            ownership.append(context.is_owner())
            entered.set()
            release.wait(3)  # Releases the GIL, as subprocess waits do.
            return False

        def second(*_data):
            second_called.set()
            return False

        source = GLib.idle_source_new()
        source.set_callback(first)
        source.attach(context)
        thread = threading.Thread(target=context.iteration, args=(True,))
        thread.start()
        second_source = None
        try:
            self.assertTrue(entered.wait(2), 'First context callback did not dispatch')
            second_source = GLib.idle_source_new()
            second_source.set_callback(second)
            second_source.attach(context)
            acquired = context.acquire()
            if acquired:
                context.release()
            self.assertFalse(acquired, 'Another thread acquired a context during callback dispatch')
            self.assertFalse(context.iteration(False))
            self.assertFalse(second_called.is_set())
        finally:
            release.set()
            thread.join(timeout=3)
            source.destroy()
        try:
            self.assertFalse(thread.is_alive())
            self.assertEqual(ownership, [True])
            self.assertTrue(context.iteration(False))
            self.assertTrue(second_called.is_set())
        finally:
            if second_source is not None:
                second_source.destroy()

    def test_real_glib_dispatches_deferred_release_before_stop_reply(self):
        try:
            from gi.repository import GLib
        except ImportError:
            self.skipTest('PyGObject is unavailable')
        integration, _context = integration_fixture()
        integration.glib = GLib
        loop = GLib.MainLoop()
        events, errors = [], []
        holder = [True]
        sources = []
        integration.session_token = object()
        hardware = integration.tools.services.hardware_manager.IHardware
        original = hardware.add_service
        integration.install_hardware_callbacks()
        reboot = Mock()
        hardware.add_service(integration.args, Mock(), Mock(), Mock(), reboot, Mock())
        queued_reboot = original.call_args.args[4]

        def guard(stopped, wait=True):
            self.assertTrue(stopped)
            self.assertFalse(wait)
            events.append('guard')
            if holder[0]:
                raise system.default_shares.NamespaceBusy('deferred object')
            integration.sharing.active = False
            events.append('cleanup-complete')

        def release():
            events.append('idle-release')
            queued_reboot()
            holder[0] = False
            return False

        def reply():
            try:
                self.assertFalse(integration.stopping)
                self.assertIsNone(integration.stop_timer)
                self.assertIsNone(GLib.MainContext.default().find_source_by_id(sources[0]))
                events.append('reply')
            except Exception as failure:
                errors.append(failure)
            finally:
                loop.quit()

        def error(failure):
            errors.append(failure)
            loop.quit()

        def timeout():
            errors.append(AssertionError('Real GLib Stop cleanup did not finish'))
            loop.quit()
            return False

        integration.sharing.close.side_effect = guard
        # The pending idle cannot run until the initial Stop callback yields.
        idle = GLib.idle_add(release)
        watchdog = GLib.timeout_add(2000, timeout)
        try:
            with patch.object(system.gc, 'collect'):
                integration.stop_async(integration.args, True, reply, error)
                self.assertEqual(events, ['guard'])
                self.assertTrue(integration.stopping)
                sources.append(integration.stop_timer)
                loop.run()
            self.assertFalse(errors, errors)
            self.assertEqual(events, ['guard', 'idle-release', 'guard', 'cleanup-complete', 'reply'])
            reboot.assert_not_called()
            self.assertEqual(integration.sharing.close.call_count, 2)
            integration.original_stop.assert_called_once_with(integration.args, True)
        finally:
            for source in [idle, watchdog, *sources]:
                if source and GLib.MainContext.default().find_source_by_id(source):
                    GLib.source_remove(source)


class DbusOverrideTests(unittest.TestCase):
    def test_real_decorator_keeps_stock_start_authorization_and_async_stop(self):
        try:
            import dbus
            import dbus.service
        except ImportError:
            self.skipTest('dbus-python is unavailable')
        source = Path('/usr/lib/waydroid/tools/actions/container_manager.py')
        if not source.is_file():
            self.skipTest('Stock Waydroid class is unavailable')
        # Compile only the installed class: no Waydroid imports or runtime setup.
        tree = ast.parse(source.read_text())
        node = next(item for item in tree.body if isinstance(item, ast.ClassDef)
                    and item.name == 'DbusContainerManager')
        original_start = Mock()
        namespace = {'__name__': 'inert_stock_stop_fixture', 'dbus': dbus,
                     'helpers': types.SimpleNamespace(logging=types.SimpleNamespace(log_exceptions=lambda f: f)),
                     'do_start': original_start}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        stock = namespace['DbusContainerManager']
        integration, _context = integration_fixture()
        manager_module = integration.tools.actions.container_manager
        manager_module.DbusContainerManager = stock
        integration.install_stop_manager()
        replacement = manager_module.DbusContainerManager
        self.assertTrue(issubclass(replacement, stock))
        self.assertIs(replacement.Start, stock.Start)
        self.assertEqual(replacement.Stop._dbus_async_callbacks, ('reply', 'error'))
        self.assertEqual(replacement.Stop._dbus_in_signature, 'b')
        self.assertEqual(replacement.Stop._dbus_out_signature, '')
        manager = object.__new__(replacement)
        manager.args = integration.args
        bus_info = Mock()
        bus_info.GetConnectionUnixUser.return_value = 1000
        bus_info.GetConnectionUnixProcessID.return_value = 123
        conn = Mock()
        session = {'user_id': '1000', 'pid': '123'}
        with patch.object(dbus, 'Interface', return_value=bus_info):
            replacement.Start(manager, session, ':1.2', conn)
            original_start.assert_called_once_with(integration.args, session)
            bus_info.GetConnectionUnixUser.return_value = 1001
            with self.assertRaisesRegex(RuntimeError, 'another user'):
                replacement.Start(manager, session, ':1.2', conn)
            bus_info.GetConnectionUnixUser.return_value = 1000
            bus_info.GetConnectionUnixProcessID.return_value = 124
            with self.assertRaisesRegex(RuntimeError, 'Invalid session pid'):
                replacement.Start(manager, session, ':1.2', conn)
        original_start.assert_called_once()
        integration.stop_async = Mock()
        reply, error = Mock(), Mock()
        replacement.Stop(manager, True, reply, error)
        integration.stop_async.assert_called_once_with(integration.args, True, reply, error)


if __name__ == '__main__':
    unittest.main()
