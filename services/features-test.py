"""Real owner-authorized feature dispatch against inert stores/Android adapters."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('features', Path(__file__).with_name('runtime-features.py'))
features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(features)
FIRST = 'r-' + '1' * 32
SECOND = 'r-' + '2' * 32
OTHER = 'r-' + '3' * 32
UID = 1000
PACKAGE = 'com.example.game'
SETTINGS = {'fake_wifi':True, 'mouse_as_touch':True}
DIGEST = 'a' * 64


class Android:
    def __init__(self):
        self.properties = dict.fromkeys(features.compatibility.PROPERTIES.values(), '')
        self.calls = []
        self.hook = None

    def __call__(self, *args):
        self.calls.append(args)
        if self.hook:
            self.hook(args)
        if args[:5] == ('pm','list','packages','--user','0'):
            return 'package:' + PACKAGE
        if args[0] == 'getprop':
            return self.properties[args[1]]
        if args[0] == 'setprop':
            self.properties[args[1]] = args[2]
            return ''
        raise AssertionError(args)


class Storage:
    def __init__(self, calls):
        self.calls = calls; self.busy = None
    def ensure(self, identifier):
        self.calls.append(('ensure',identifier))


class Backend:
    def __init__(self, root, calls):
        self.root = root; self.calls = calls
    def location(self, identifier):
        self.calls.append(('location',identifier))
        return self.root / identifier


class Workers:
    def __init__(self, calls):
        self.calls = calls
        self.adapters = {FIRST:Android(),SECOND:Android(),OTHER:Android()}
        self.generations = dict.fromkeys(self.adapters, 'g1')
        self.quiet = True
    def status(self, identifier, operation='status', payload=None):
        self.calls.append(('worker',identifier,operation))
        report = {'runtime_id':identifier,'generation':self.generations[identifier], 'state':'Running',
                  'capabilities':['app_compatibility']}
        if operation == 'compatibility':
            try:
                report['compatibility'] = features.compatibility.dispatch(self.adapters[identifier],payload,
                                                                          identifier,self.generations[identifier])
            except RuntimeError as error:
                report['app_error'] = str(error)
        return report
    def quiescent(self, identifier):
        self.calls.append(('quiescent',identifier))
        return self.quiet


class Existing:
    def __init__(self, calls):
        self.calls = calls; self.session = {'pid':'91','user_id':str(UID)}
        self.adapter = Android(); self.running = True; self.owner = UID
    def authorize(self, uid):
        self.calls.append(('default-authorize',uid))
        if uid != self.owner:
            raise RuntimeError('Default belongs to another user')
        return dict(self.session) if self.session is not None else None, {'owner_uid':self.owner}
    def record(self, session, saved):
        return {'id':'default','owner_uid':saved['owner_uid'],'state':'Running' if self.running else 'Stopped'}
    def state(self):
        self.calls.append(('default-state',))
        return 'RUNNING' if self.running else 'STOPPED'
    def android(self, *args):
        self.calls.append(('default-android',args[0]))
        return self.adapter(*args)


class Store:
    def __init__(self, root):
        self.calls = []
        self.data = {'runtimes':[{'id':identifier, 'owner_uid':owner, 'state':'Running'}
                                 for identifier,owner in ((FIRST,UID),(SECOND,UID),(OTHER,UID+1))]}
        self.deletions = {}; self.active_jobs = {}; self.app_jobs = {}
        self.storage = Storage(self.calls); self.backend = Backend(root,self.calls)
        self.android = Workers(self.calls); self.existing = Existing(self.calls)
        self.selections = {}
        self.arm = {'enabled':False,'provider':'libndk_translation 0.2.3','supported':True,'available':True,
                    'sources':[{'engine':'libndk','sha256':DIGEST,'installed':True,'version':'0.2.3'}]}
        self.google = {'packages':{'com.android.vending':False,'com.google.android.gms':False,'com.google.android.gsf':False}}
    def _dispatch(self, request, uid):
        self.calls.append(('dispatch',request,uid))
        op = request['op']
        if op == 'arm_info':
            return self.arm
        if op == 'google_services':
            return self.google
        if op == 'arm_set':
            self.arm = {**self.arm,'enabled':request['enabled'],'archive_sha256':request.get('archive_sha256')}
            return self.arm
        if op in ('health','resources_info','display_info'):
            return {'available':False}
        raise AssertionError(request)


class FeatureDispatchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='anvil-features-test-')
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        for identifier in (FIRST,SECOND,OTHER):
            directory = self.store.backend.root / identifier / 'support'
            directory.mkdir(parents=True)
            (directory / 'compatibility.json').write_text('{"sdk":33}')

    def dispatch(self, op='compatibility_get', identifier=FIRST, **fields):
        request = {'op':op,'id':identifier,**fields}
        return features.dispatch(self.store,request,UID)

    def read(self, identifier=FIRST):
        return self.dispatch(identifier=identifier,package=PACKAGE)

    def test_owner_rejected_before_any_backend_path_or_android_call_for_all_operations(self):
        for op, fields in features.FIELDS.items():
            request = dict.fromkeys(fields, 'untrusted')
            request.update(op=op,id=OTHER)
            self.store.calls.clear()
            with self.subTest(op=op):
                with self.assertRaisesRegex(RuntimeError,'not found for this user'):
                    features.dispatch(self.store,request,UID)
                self.assertEqual(self.store.calls,[])

    def test_unknown_runtime_and_invalid_runtime_ids_do_not_touch_paths(self):
        for identifier in ('r-'+'f'*32,'../../host','default/../../x',None,12,'r-123'):
            self.store.calls.clear()
            with self.subTest(identifier=identifier):
                with self.assertRaises(RuntimeError):
                    self.read(identifier)
                self.assertEqual(self.store.calls,[])

    def test_fields_and_extra_descriptor_rejected_before_authorization_or_paths(self):
        valid = {'op':'compatibility_get','id':FIRST,'package':PACKAGE}
        for request, descriptor in (({**valid,'uid':UID+1},None),({**valid,'property':'ro.foo'},None),
                                    ({'op':'compatibility_get','id':FIRST},None),(valid,9),
                                    ({**valid,'op':'unknown'},None)):
            self.store.calls.clear()
            with self.subTest(request=request,descriptor=descriptor):
                with self.assertRaisesRegex(RuntimeError,'Unexpected feature'):
                    features.dispatch(self.store,request,UID,descriptor)
                self.assertEqual(self.store.calls,[])

    def test_deletion_and_active_job_rejected_before_backend_paths(self):
        for attribute in ('deletions','active_jobs'):
            getattr(self.store,attribute)[FIRST] = {'status':'Running'}
            self.store.calls.clear()
            with self.subTest(attribute=attribute):
                with self.assertRaises(RuntimeError):
                    self.read()
                self.assertEqual(self.store.calls,[])
            getattr(self.store,attribute).clear()

    def test_storage_and_app_jobs_gate_before_backend_paths(self):
        for busy in ('storage','app'):
            if busy == 'storage': self.store.storage.busy = FIRST
            else: self.store.app_jobs[FIRST] = {'status':'Running'}
            self.store.calls.clear()
            with self.subTest(busy=busy):
                with self.assertRaisesRegex(RuntimeError,'operation|busy|finish'):
                    self.read()
                self.assertEqual(self.store.calls,[])
            self.store.storage.busy = None; self.store.app_jobs.clear()

    def test_valid_managed_dispatch_real_adapter_and_runtime_independence(self):
        before = self.read()
        result = self.dispatch('compatibility_set',package=PACKAGE,settings=SETTINGS,revision=before['revision'])
        self.assertEqual(result['runtime_id'],FIRST)
        self.assertTrue(result['changed'])
        self.assertTrue(result['settings']['fake_wifi']['effective'])
        self.assertFalse(self.read(SECOND)['settings']['fake_wifi']['effective'])
        result = self.dispatch('compatibility_reset',package=PACKAGE,revision=result['revision'])
        self.assertFalse(result['settings']['fake_wifi']['effective'])

    def test_stale_runtime_generation_refuses_mutation(self):
        before = self.read()
        self.store.android.generations[FIRST] = 'g2'
        with self.assertRaisesRegex(RuntimeError,'changed'):
            self.dispatch('compatibility_set',package=PACKAGE,settings=SETTINGS,revision=before['revision'])
        self.assertFalse(any(args[0]=='setprop' for args in self.store.android.adapters[FIRST].calls))

    def test_default_owner_is_checked_before_android_and_backend(self):
        self.store.existing.owner = UID+1
        with self.assertRaisesRegex(RuntimeError,'another user'):
            self.read('default')
        self.assertEqual(self.store.calls,[('default-authorize',UID)])
        self.assertEqual(self.store.existing.adapter.calls,[])

    def test_default_read_set_and_cross_runtime_revision_rejected(self):
        before = self.read('default')
        with self.assertRaisesRegex(RuntimeError,'changed'):
            self.dispatch('compatibility_set',package=PACKAGE,settings=SETTINGS,revision=before['revision'])
        changed = self.dispatch('compatibility_set',identifier='default',package=PACKAGE,
                                settings=SETTINGS,revision=before['revision'])
        self.assertTrue(changed['changed'])
        self.assertEqual(changed['generation'],'91')
        self.assertFalse(self.read()['settings']['fake_wifi']['effective'])

    def test_default_session_generation_mismatch_before_mutation_and_after_read(self):
        before = self.read('default')
        self.store.existing.session['pid'] = '92'
        with self.assertRaisesRegex(RuntimeError,'changed'):
            self.dispatch('compatibility_set',identifier='default',package=PACKAGE,
                          settings=SETTINGS,revision=before['revision'])
        def replace_session(args):
            if args[0] == 'getprop': self.store.existing.session['pid'] = '93'
        self.store.existing.adapter.hook = replace_session
        with self.assertRaisesRegex(RuntimeError,'Default runtime changed'):
            self.read('default')
        self.assertFalse(any(args[0]=='setprop' for args in self.store.existing.adapter.calls))

    def test_stopped_default_refuses_android_queries(self):
        self.store.existing.running = False
        with self.assertRaisesRegex(RuntimeError,'Start Default'):
            self.read('default')
        self.assertEqual(self.store.existing.adapter.calls,[])

    def test_missing_default_session_fails_closed(self):
        self.store.existing.session = None
        with self.assertRaisesRegex(RuntimeError,'generation|session|Start Default'):
            self.read('default')
        self.assertEqual(self.store.existing.adapter.calls,[])

    def test_default_session_disappearing_after_read_reports_runtime_change(self):
        def disappear(args):
            if args[0] == 'getprop':
                self.store.existing.session = None
                self.store.existing.running = False
        self.store.existing.adapter.hook = disappear
        with self.assertRaisesRegex(RuntimeError,'Default runtime changed'):
            self.read('default')

    def test_invalid_default_generation_refuses_android_access(self):
        for generation in (None, '', 0, 'not-a-pid'):
            self.store.existing.session = {'pid':generation,'user_id':str(UID)}
            self.store.existing.adapter.calls.clear()
            with self.subTest(generation=generation):
                with self.assertRaisesRegex(RuntimeError,'generation'):
                    self.read('default')
                self.assertEqual(self.store.existing.adapter.calls,[])

    def test_failure_second_write_rolls_back_through_real_worker_dispatch(self):
        before = self.read()
        android = self.store.android.adapters[FIRST]
        failed = False
        def hook(args):
            nonlocal failed
            if not failed and args[:2] == ('setprop',features.compatibility.PROPERTIES['mouse_as_touch']):
                failed = True
                android.properties[args[1]] = args[2]
                raise RuntimeError('Fixture simulated interrupted second write')
        android.hook = hook
        with self.assertRaisesRegex(RuntimeError,'previous settings restored'):
            self.dispatch('compatibility_set',package=PACKAGE,settings=SETTINGS,revision=before['revision'])
        self.assertEqual(set(android.properties.values()),{''})

    def test_components_stale_revision_does_not_install(self):
        before = self.dispatch('components_get')
        self.store.google['packages']['com.google.android.gms'] = True
        # Partial GAPPS does not alter all-installed status; change ARM state too.
        self.store.arm['enabled'] = True
        self.store.calls.clear()
        with self.assertRaisesRegex(RuntimeError,'changed; refresh'):
            self.dispatch('components_set',component_id='libndk',enabled=True,archive_sha256=DIGEST,
                          revision=before['revision'])
        self.assertFalse(any(call[0]=='dispatch' and call[1]['op']=='arm_set' for call in self.store.calls))

    def test_component_valid_revision_uses_existing_verified_arm_operation(self):
        before = self.dispatch('components_get')
        result = self.dispatch('components_set',component_id='libndk',enabled=True,archive_sha256=DIGEST,
                               revision=before['revision'])
        self.assertNotEqual(result['revision'],before['revision'])
        self.assertIn(('dispatch',{'op':'arm_set','id':FIRST,'enabled':True,'archive_sha256':DIGEST},UID),self.store.calls)

    def test_component_revision_bound_to_runtime_and_image_sdk(self):
        before = self.dispatch('components_get')
        for identifier in (SECOND,FIRST):
            if identifier == FIRST:
                (self.store.backend.root/FIRST/'support/compatibility.json').write_text('{"sdk":34}')
            with self.subTest(identifier=identifier):
                with self.assertRaisesRegex(RuntimeError,'changed; refresh'):
                    self.dispatch('components_set',identifier=identifier,component_id='libndk',enabled=True,
                                  archive_sha256=DIGEST,revision=before['revision'])

    def test_uncached_component_is_not_falsely_reported_as_cached_or_installable(self):
        self.store.arm.update(available=False,sources=[{'engine':'libndk','sha256':DIGEST,'installed':False,'version':'known'}])
        report = self.dispatch('components_get')
        row = next(item for item in report['components'] if item['id']=='libndk')
        self.assertFalse(row['can_enable'])
        self.assertTrue(all(package.get('cached') is not True for package in row['packages']))

    def test_shares_mutations_require_stopped_and_quiescent_before_config_call(self):
        for op,fields in (('shares_set',{'name':'docs','source':'/untrusted/path','read_only':True}),
                          ('shares_remove',{'name':'docs'})):
            for state,quiet in (('Running',True),('Frozen',True),('Stopped',False),('Starting',True)):
                self.store.data['runtimes'][0]['state'] = state; self.store.android.quiet = quiet
                with self.subTest(op=op,state=state,quiet=quiet):
                    with patch.object(features.shares,'configure') as configure, patch.object(features.shares,'remove') as remove:
                        with self.assertRaisesRegex(RuntimeError,'Stop this runtime completely'):
                            self.dispatch(op,**fields)
                        configure.assert_not_called(); remove.assert_not_called()

    def test_stopped_share_uses_authorized_owner_and_selected_runtime(self):
        self.store.data['runtimes'][0]['state'] = 'Stopped'
        expected = {'shares':[], 'activation':'next_start'}
        with patch.object(features.shares,'configure',return_value=expected) as configure:
            result = self.dispatch('shares_set',name='docs',source='/home/fixture/docs',read_only=True)
            configure.assert_called_once_with(self.store.backend.root/FIRST,UID,
                                              {'name':'docs','source':'/home/fixture/docs','read_only':True})
            self.assertEqual(result['runtime_id'],FIRST)
            self.assertTrue(result['editable'])

    def test_default_shares_are_explicitly_unavailable(self):
        with self.assertRaisesRegex(RuntimeError,'managed runtime'):
            self.dispatch('shares_get',identifier='default')
        self.assertFalse(any(call[0] in ('location','default-android') for call in self.store.calls))


if __name__ == '__main__':
    unittest.main()
