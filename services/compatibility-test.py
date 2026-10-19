"""Isolated compatibility property/worker tests; never attaches to live Android."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('compatibility', Path(__file__).with_name('runtime-compatibility.py'))
compatibility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compatibility)
PACKAGE = 'com.example.game'
WIFI = compatibility.PROPERTIES['fake_wifi']
TOUCH = compatibility.PROPERTIES['mouse_as_touch']
ON = {'fake_wifi': True, 'mouse_as_touch': True}
OFF = {'fake_wifi': False, 'mouse_as_touch': False}


class Android:
    def __init__(self, wifi='', touch=''):
        self.values = {WIFI: wifi, TOUCH: touch}
        self.packages = [PACKAGE]
        self.calls = []
        self.hook = None

    def __call__(self, *args):
        self.calls.append(args)
        if self.hook:
            override = self.hook(args)
            if override is not None:
                return override
        if args[:4] == ('pm', 'list', 'packages', '--user'):
            assert args[4:] == ('0', PACKAGE)
            return '\n'.join('package:' + package for package in self.packages)
        if args[0] == 'getprop':
            assert len(args) == 2 and args[1] in (WIFI, TOUCH)
            return self.values[args[1]]
        if args[0] == 'setprop':
            assert len(args) == 3 and args[1] in (WIFI, TOUCH)
            self.values[args[1]] = args[2]
            return ''
        raise AssertionError(args)

    def report(self, runtime='first', generation='g1'):
        return compatibility.read(self, PACKAGE, runtime, generation)

    def apply(self, settings=ON, runtime='first', generation='g1', revision=None):
        return compatibility.apply(self, PACKAGE, runtime, generation, settings,
                                   revision or self.report(runtime, generation)['revision'])

    def writes(self):
        return [args for args in self.calls if args[0] == 'setprop']


class CompatibilityTest(unittest.TestCase):
    def test_installed_exact_package_required(self):
        for packages in ([], [PACKAGE + '.other']):
            with self.subTest(packages=packages):
                android = Android(); android.packages = packages
                with self.assertRaisesRegex(RuntimeError, 'not installed'):
                    android.report()
                self.assertEqual(android.writes(), [])

    def test_package_command_injection_rejected_before_adapter(self):
        for package in ('com.game;id', 'com.game\n', '-com.game', '*', None, 'com.' + 'a' * 255,
                        'com.game,$(id)', 'com.game/../../x', 'single', ['com.game']):
            with self.subTest(package=package):
                android = Android()
                with self.assertRaisesRegex(RuntimeError, 'Invalid Android package'):
                    compatibility.read(android, package, 'first', 'g1')
                self.assertEqual(android.calls, [])

    def test_list_failure_propagates_without_mutation(self):
        android = Android()
        def failure(args):
            if args[0] == 'pm':
                raise RuntimeError('PackageManager unavailable')
        android.hook = failure
        with self.assertRaisesRegex(RuntimeError, 'PackageManager unavailable'):
            android.report()
        self.assertEqual(android.writes(), [])

    def test_invalid_package_manager_output_is_bounded(self):
        for output in (None, b'package:' + PACKAGE.encode(), 'x' * 65537):
            android = Android()
            # Hook sentinel None means no override, use direct wrapper instead.
            adapter = lambda *args: output if args[0] == 'pm' else android(*args)
            with self.subTest(output_type=type(output)):
                with self.assertRaisesRegex(RuntimeError, 'Invalid PackageManager'):
                    compatibility.read(adapter, PACKAGE, 'first', 'g1')

    def test_read_reports_explicit_inherited_effective_and_no_behavior_claim(self):
        android = Android('*,com.other.app', PACKAGE)
        report = android.report()
        self.assertEqual(report['settings']['fake_wifi'], {'explicit': False, 'inherited': True, 'effective': True})
        self.assertEqual(report['settings']['mouse_as_touch'], {'explicit': True, 'inherited': False, 'effective': True})
        self.assertFalse(report['behavior_verified'])
        self.assertFalse(report['supports_global_override'])
        self.assertEqual(report['activation'], 'current_configuration')
        self.assertFalse(report['changed'])

    def test_enable_preserves_other_packages_order_and_duplicates(self):
        android = Android('com.other.app,com.other.app', 'com.other.touch')
        result = android.apply()
        self.assertEqual(android.values[WIFI], 'com.other.app,com.other.app,' + PACKAGE)
        self.assertEqual(android.values[TOUCH], 'com.other.touch,' + PACKAGE)
        self.assertEqual(result['activation'], 'app_restart_required')
        self.assertTrue(result['changed'])
        self.assertTrue(result['settings']['mouse_as_touch']['effective'])

    def test_disable_removes_all_app_entries_only(self):
        android = Android(f'{PACKAGE},com.other.app,{PACKAGE}', f'com.other.app,{PACKAGE}')
        android.apply(OFF)
        self.assertEqual(android.values, {WIFI: 'com.other.app', TOUCH: 'com.other.app'})

    def test_reset_preserves_global_wildcard_and_inheritance(self):
        android = Android(f'*,{PACKAGE},com.other.app,{PACKAGE}', PACKAGE)
        old = android.report()
        result = compatibility.reset(android, PACKAGE, 'first', 'g1', old['revision'])
        self.assertEqual(android.values, {WIFI: '*,com.other.app', TOUCH: ''})
        self.assertTrue(result['settings']['fake_wifi']['inherited'])
        self.assertFalse(result['settings']['fake_wifi']['explicit'])
        self.assertTrue(result['settings']['fake_wifi']['effective'])
        self.assertTrue(result['changed'])

    def test_wildcard_per_app_disable_fails_before_any_write(self):
        for wifi, touch in (('*', ''), ('', '*')):
            android = Android(wifi, touch)
            with self.assertRaisesRegex(RuntimeError, 'global wildcard'):
                android.apply(OFF)
            self.assertEqual(android.writes(), [])

    def test_saving_inherited_setting_does_not_add_explicit_entry(self):
        android = Android('*', PACKAGE)
        result = android.apply()
        self.assertEqual(android.values[WIFI], '*')
        self.assertFalse(result['settings']['fake_wifi']['explicit'])
        self.assertFalse(result['changed'])
        self.assertEqual(android.writes(), [])

    def test_noop_no_write_or_restart_claim(self):
        android = Android(PACKAGE, PACKAGE)
        result = android.apply()
        self.assertEqual(android.writes(), [])
        self.assertFalse(result['changed'])
        self.assertEqual(result['activation'], 'current_configuration')

    def test_noop_reset_retains_global(self):
        android = Android('*', 'com.other.app')
        result = compatibility.reset(android, PACKAGE, 'first', 'g1', android.report()['revision'])
        self.assertFalse(result['changed'])
        self.assertEqual(android.writes(), [])

    def test_settings_are_exact_boolean_allowlist(self):
        for settings in (None, {}, {'fake_wifi': True}, {**ON, 'property': 'evil'},
                         {'fake_wifi': 1, 'mouse_as_touch': True}, {'fake_wifi': 'yes', 'mouse_as_touch': False}):
            with self.subTest(settings=settings):
                android = Android()
                with self.assertRaisesRegex(RuntimeError, 'exactly two boolean'):
                    android.apply(settings)
                self.assertEqual(android.writes(), [])

    def test_unknown_property_grammar_refused_without_loss(self):
        for value in ('com.foo.*', 'com.foo.app;id', 'com.foo.app,', ' com.foo.app', 'a' * 256 + '.app',
                      'com.foo.app\ncom.bar.app', 'x' * 8193):
            with self.subTest(value=value[:50]):
                android = Android(value)
                with self.assertRaisesRegex(RuntimeError, 'property'):
                    android.apply()
                self.assertEqual(android.values[WIFI], value)
                self.assertEqual(android.writes(), [])

    def test_property_full_preflight_no_partial_writes(self):
        tokens = ['com.other.a' + str(i) for i in range(450)]
        # Valid bounded grammar with insufficient room for this package.
        value = ','.join(tokens)
        while len(value) + len(PACKAGE) + 1 <= compatibility.MAX_VALUE:
            value += ',com.z'
        android = Android(value)
        with self.assertRaisesRegex(RuntimeError, 'property is full'):
            android.apply()
        self.assertEqual(android.writes(), [])

    def test_stale_revision_prevents_overwriting_external_changes(self):
        android = Android()
        old = android.report()['revision']
        android.values[WIFI] = 'com.other.external'
        with self.assertRaisesRegex(RuntimeError, 'changed; refresh'):
            android.apply(revision=old)
        self.assertEqual(android.writes(), [])

    def test_revision_is_bound_to_runtime_and_generation(self):
        android = Android()
        old = android.report()['revision']
        for runtime, generation in (('second', 'g1'), ('first', 'g2')):
            with self.subTest(runtime=runtime, generation=generation):
                with self.assertRaisesRegex(RuntimeError, 'changed; refresh'):
                    android.apply(runtime=runtime, generation=generation, revision=old)
        self.assertEqual(android.writes(), [])

    def test_two_runtime_same_package_independent_apply_and_reset(self):
        first, second = Android('com.first.app'), Android('com.second.app')
        first.apply(runtime='first')
        self.assertEqual(second.values, {WIFI:'com.second.app', TOUCH:''})
        second.apply(runtime='second', settings={'fake_wifi':False,'mouse_as_touch':True})
        compatibility.reset(first, PACKAGE, 'first', 'g1', first.report()['revision'])
        self.assertEqual(first.values, {WIFI:'com.first.app', TOUCH:''})
        self.assertEqual(second.values, {WIFI:'com.second.app', TOUCH:PACKAGE})

    def test_each_write_failure_including_ambiguous_mutation_is_rolled_back(self):
        for failed_prop in (WIFI, TOUCH):
            for mutate in (False, True):
                with self.subTest(property=failed_prop, mutate=mutate):
                    android = Android('com.old.wifi', 'com.old.touch')
                    before = dict(android.values)
                    revision = android.report()['revision']
                    failed = False
                    def hook(args):
                        nonlocal failed
                        if not failed and args[:2] == ('setprop', failed_prop):
                            failed = True
                            if mutate: android.values[failed_prop] = args[2]
                            raise RuntimeError('setprop failure')
                    android.hook = hook
                    with self.assertRaisesRegex(RuntimeError, 'previous settings restored'):
                        android.apply(revision=revision)
                    self.assertEqual(android.values, before)

    def test_each_immediate_readback_failure_rolls_back(self):
        for failed_prop in (WIFI, TOUCH):
            for mode in ('mismatch', 'exception'):
                with self.subTest(property=failed_prop, mode=mode):
                    android = Android('com.old.wifi', 'com.old.touch')
                    before = dict(android.values); revision = android.report()['revision']
                    failed = False
                    def hook(args):
                        nonlocal failed
                        if not failed and args == ('getprop', failed_prop) and android.values[failed_prop].endswith(PACKAGE):
                            failed = True
                            if mode == 'exception': raise RuntimeError('readback failed')
                            return 'com.unexpected.app'
                    android.hook = hook
                    with self.assertRaisesRegex(RuntimeError, 'previous settings restored'):
                        android.apply(revision=revision)
                    self.assertEqual(android.values, before)

    def test_final_readback_change_rolls_back(self):
        android = Android('com.old.wifi', 'com.old.touch')
        before = dict(android.values); revision = android.report()['revision']
        reads = 0
        def hook(args):
            nonlocal reads
            if args == ('getprop', WIFI):
                reads += 1
                if reads == 3:
                    return 'com.changed.app'
        android.hook = hook
        with self.assertRaisesRegex(RuntimeError, 'previous settings restored'):
            android.apply(revision=revision)
        self.assertEqual(android.values, before)

    def test_rollback_failure_explicitly_reports_uncertain_state(self):
        for mode in ('write', 'read'):
            with self.subTest(mode=mode):
                android = Android('com.old.wifi', 'com.old.touch')
                revision = android.report()['revision']
                failed = False
                def hook(args):
                    nonlocal failed
                    if args[:2] == ('setprop', TOUCH):
                        failed = True
                        raise RuntimeError('write denied')
                    if failed and mode == 'write' and args[:2] == ('setprop', WIFI):
                        raise RuntimeError('rollback denied')
                    if failed and mode == 'read' and args == ('getprop', WIFI):
                        return 'com.unexpected.app'
                android.hook = hook
                with self.assertRaisesRegex(RuntimeError, 'rollback could not restore'):
                    android.apply(revision=revision)

    def test_invalid_revision_never_writes(self):
        for revision in (None, 'x', [], 'A' * 64):
            android = Android()
            with self.subTest(revision=revision):
                with self.assertRaisesRegex(RuntimeError, 'Refresh compatibility'):
                    compatibility.apply(android, PACKAGE, 'first', 'g1', ON, revision)
                self.assertEqual(android.calls, [])

    def test_generation_scoped_dispatch_validates_fields_before_adapter(self):
        request = {'op':'compatibility', 'generation':'g1', 'action':'get', 'package':PACKAGE}
        for change in ({'generation':'old'}, {'action':'shell'}, {'uid':0}, {'property':'ro.anything'}):
            android = Android()
            with self.subTest(change=change):
                with self.assertRaises(RuntimeError):
                    compatibility.dispatch(android, {**request, **change}, 'first', 'g1')
                self.assertEqual(android.calls, [])

    def test_dispatch_get_set_reset(self):
        android = Android()
        request = {'op':'compatibility', 'generation':'g1', 'action':'get', 'package':PACKAGE}
        report = compatibility.dispatch(android, request, 'first', 'g1')
        report = compatibility.dispatch(android, {**request, 'action':'set', 'settings':ON,
                                                  'revision':report['revision']}, 'first', 'g1')
        report = compatibility.dispatch(android, {**request, 'action':'reset', 'revision':report['revision']}, 'first', 'g1')
        self.assertFalse(report['settings']['fake_wifi']['effective'])
        self.assertEqual(android.values, {WIFI:'',TOUCH:''})


class WorkerAdapterTest(unittest.TestCase):
    def backend(self, **change):
        status = {'state':'Running','generation':'g1','capabilities':['app_compatibility']}
        result = Android().report()
        report = {**status, 'compatibility':result}
        status.update(change.pop('status', {})); report.update(change)
        class Backend:
            calls = []
            def status(self, identifier, operation='status', payload=None):
                self.calls.append((identifier,operation,payload))
                return status if operation == 'status' else report
        return Backend()

    def test_sends_only_validated_generation_scoped_request(self):
        backend = self.backend()
        result = compatibility.worker_request(backend, 'first', 'set', PACKAGE, ON, 'a'*64)
        self.assertEqual(result['runtime_id'], 'first')
        self.assertEqual(backend.calls[1], ('first','compatibility',
                         {'op':'compatibility','generation':'g1','action':'set','package':PACKAGE,
                          'settings':ON,'revision':'a'*64}))

    def test_unavailable_or_old_workers_fail_without_mutation(self):
        for status in ({'state':'Stopped'}, {'state':'Frozen'}, {'capabilities':[]}):
            backend = self.backend(status=status)
            with self.subTest(status=status):
                with self.assertRaises(RuntimeError):
                    compatibility.worker_request(backend,'first','get',PACKAGE)
                self.assertEqual(len(backend.calls),1)

    def test_worker_errors_generation_and_result_spoofing_rejected(self):
        valid = Android().report()
        for change in ({'app_error':'Denied'}, {'generation':'g2'}, {'state':'Stopped'},
                       {'compatibility':None}, {'compatibility':{**valid,'runtime_id':'second'}},
                       {'compatibility':{**valid,'generation':'old'}},
                       {'compatibility':{**valid,'package':'com.other.app'}}):
            with self.subTest(change=change):
                with self.assertRaises(RuntimeError):
                    compatibility.worker_request(self.backend(**change),'first','get',PACKAGE)


if __name__ == '__main__':
    unittest.main()
