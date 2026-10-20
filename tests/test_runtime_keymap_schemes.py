"""Scheme state machine and actual unprivileged shell transaction behavior.

The attach adapter is replaced with a shell in a temporary tree. No Android,
user desktop, controller socket or globally installed files are touched.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('schemes', Path(__file__).resolve().parents[1] / 'services/runtime-keymap.py')
keymap = importlib.util.module_from_spec(spec); spec.loader.exec_module(keymap)
GEN = 'b' * 32
PKG = 'com.test.game'
ACTIVE = f'ANVIL_KEYMAP 1 default {PKG} 1\n30 100 200 0\n'


def request(action='list', scheme_id='', name='', revision='', content=''):
    return dict(op='keymap_scheme', id='default', package=PKG, action=action,
                scheme_id=scheme_id, name=name, revision=revision, content=content)


class SchemePlanTests(unittest.TestCase):
    def test_legacy_default_clone_select_native_save_reset_and_remove(self):
        state, _, _ = keymap._scheme_plan(request(), ACTIVE, '')
        self.assertEqual((state['selected'], state['schemes'][0]['binding_count']), ('default', 1))
        clone, active, metadata = keymap._scheme_plan(request('clone', 'default', 'Keyboard', state['revision']), ACTIVE, '')
        custom = clone['schemes'][1]['id']
        selected, active, metadata = keymap._scheme_plan(request('select', custom, revision=clone['revision']), active, metadata)
        self.assertEqual(selected['selected'], custom)
        native_save = active.replace('100 200', '300 400')
        native_report, _, _ = keymap._scheme_plan(request(), native_save, metadata)
        back, active, metadata = keymap._scheme_plan(request('select', 'default', revision=native_report['revision']), native_save, metadata)
        self.assertEqual(active, ACTIVE)
        saved_custom = next(s for s in json.loads(metadata)['schemes'] if s['id'] == custom)
        self.assertEqual(saved_custom['profile'], native_save)
        reset, active, metadata = keymap._scheme_plan(request('reset', custom, revision=back['revision']), active, metadata)
        self.assertEqual(reset['schemes'][1]['binding_count'], 0)
        removed, active, metadata = keymap._scheme_plan(request('remove', custom, revision=reset['revision']), active, metadata)
        self.assertEqual(len(removed['schemes']), 1)
        with self.assertRaisesRegex(RuntimeError, 'Default'):
            keymap._scheme_plan(request('remove', 'default', revision=removed['revision']), active, metadata)

    def test_stale_native_save_and_scope_rejected_without_mutation(self):
        report, _, _ = keymap._scheme_plan(request(), ACTIVE, '')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            keymap._scheme_plan(request('create', name='New', revision=report['revision']), ACTIVE.replace('100', '101'), '')
        for change in ({'package': '../evil'}, {'id': 'r-other'}, {'action': 'shell'}, {'content': 'rm -rf'}, {'name': '\n'}):
            with self.assertRaises(RuntimeError): keymap.validate_scheme_request(dict(request(), **change))

    def test_export_import_rebases_only_valid_supported_native_data(self):
        report, _, _ = keymap._scheme_plan(request(), ACTIVE, '')
        exported, _, _ = keymap._scheme_plan(request('export', 'default', revision=report['revision']), ACTIVE, '')
        envelope = json.loads(exported['content'])
        envelope['profile'] = envelope['profile'].replace('default', 'r-'+'a'*32).replace(PKG, 'com.other.game')
        envelope['reference'] = {'width': 1920, 'height': 1080, 'orientation': 'landscape'}
        imported, _, metadata = keymap._scheme_plan(request('import', name='Imported', revision=report['revision'], content=json.dumps(envelope)), ACTIVE, '')
        self.assertEqual(imported['schemes'][1]['reference']['width'], 1920)
        self.assertEqual(json.loads(metadata)['schemes'][1]['profile'], ACTIVE)
        for change in ({'version': True}, {'format': 'BlueStacks'}, {'profile': ACTIVE+'sh malicious\n'}, {'reference': {'width': 0}}):
            with self.assertRaises((RuntimeError, ValueError)):
                keymap._scheme_plan(request('import', revision=report['revision'], content=json.dumps(dict(envelope, **change))), ACTIVE, '')

    def test_scheme_limits_and_corrupt_metadata_preserved(self):
        active, metadata = ACTIVE, ''
        for i in range(15):
            report, _, _ = keymap._scheme_plan(request(), active, metadata)
            report, active, metadata = keymap._scheme_plan(request('create', name=str(i), revision=report['revision']), active, metadata)
        with self.assertRaisesRegex(RuntimeError, '16'):
            keymap._scheme_plan(request('create', name='overflow', revision=report['revision']), active, metadata)
        with self.assertRaises(RuntimeError):
            keymap._scheme_plan(request(), active, metadata.replace('"version":1', '"version":true'))


class SchemeShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name) / 'waydroid_tmp/anvildroid'
        self.folder = self.base / 'keymaps'; self.folder.mkdir(parents=True)
        self.active = self.folder / PKG; self.active.write_text(ACTIVE)
        (self.base / 'keymap-scheme-protocol').write_text(GEN+'\n')
        self.crash = False
        self.fail = False
        self.shell_prefix = ''
        self.scripts = []
        self.attach = patch.object(keymap, '_scheme_attach', side_effect=self.local_attach); self.attach.start()

    def tearDown(self):
        self.attach.stop(); self.temporary.cleanup()

    def local_attach(self, lxc, container, script, input_text=''):
        self.scripts.append(script)
        script = script.replace('/data/waydroid_tmp', str(self.base.parent))
        if input_text and self.crash:
            script = script.replace('mv -f .scheme-lock/new-a "$a" &&', 'mv -f .scheme-lock/new-a "$a"; kill -KILL $$;')
        if input_text and self.fail:
            script = script.replace('mv -f .scheme-lock/new-m "$m"', 'false')
        result = subprocess.run(['sh', '-c', self.shell_prefix + script], input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode: raise RuntimeError(result.stdout or 'killed')
        return result.stdout

    def call(self, value):
        return keymap.keymap_schemes('/trusted/lxc', 'default', 'default', value, lambda *args: 'package:/data/app/base.apk', GEN)

    def test_actual_shell_round_trip_and_names_never_enter_shell(self):
        report = self.call(request())
        name = 'quotes " $(touch NO) `touch NO`'
        report = self.call(request('clone', 'default', name, report['revision']))
        self.assertEqual(report['schemes'][1]['name'], name)
        custom = report['schemes'][1]['id']
        report = self.call(request('select', custom, revision=report['revision']))
        self.assertEqual(self.active.read_text(), ACTIVE)
        self.assertFalse((self.folder/'NO').exists())
        self.assertTrue(all(name not in script for script in self.scripts))
        self.assertFalse((self.folder/'.scheme-lock').exists())

    def test_shell_hash_alias_does_not_break_mutations_or_bypass_snapshot_guard(self):
        # Android mksh keeps its default hash alias when a same-named function
        # is declared. Host shells differ, so also prohibit that colliding name.
        self.shell_prefix = "alias hash='false'\n"
        report = self.call(request())
        report = self.call(request('clone', 'default', 'Keyboard', report['revision']))
        custom = report['schemes'][1]['id']
        report = self.call(request('select', custom, revision=report['revision']))
        self.assertEqual(report['selected'], custom)
        self.assertEqual(self.active.read_text(), ACTIVE)
        for script in self.scripts:
            self.assertNotRegex(script, r'\bhash\s*\(')
            self.assertNotIn('$(hash ', script)

        metadata = (self.folder/f'.schemes-{PKG}').read_bytes()
        changed = ACTIVE.replace('100 200', '300 400')
        attach = self.local_attach

        def native_save_before_commit(lxc, container, script, input_text=''):
            if input_text:
                self.active.write_text(changed)
            return attach(lxc, container, script, input_text)

        with patch.object(keymap, '_scheme_attach', side_effect=native_save_before_commit):
            with self.assertRaisesRegex(RuntimeError, 'Schemes changed'):
                self.call(request('reset', custom, revision=report['revision']))
        self.assertEqual(self.active.read_text(), changed)
        self.assertEqual((self.folder/f'.schemes-{PKG}').read_bytes(), metadata)
        self.assertFalse((self.folder/'.scheme-lock').exists())

    def test_editor_dirty_guard_and_invalid_profile_preserve_files(self):
        report = self.call(request())
        marker = self.base/'keymap-editing'; marker.write_text(f'{GEN} {PKG} {os.getpid()}\n')
        with self.assertRaisesRegex(RuntimeError, 'Save or cancel'):
            self.call(request('reset', 'default', revision=report['revision']))
        self.assertEqual(self.active.read_text(), ACTIVE)
        self.assertFalse((self.folder/f'.schemes-{PKG}').exists())
        # Read-only inventory and export remain available while editing.
        self.call(request('export', 'default', revision=report['revision']))
        marker.unlink()
        self.active.write_text('invalid\n')
        with self.assertRaises(RuntimeError): self.call(request())
        self.assertEqual(self.active.read_text(), 'invalid\n')

    def test_symlink_hardlink_and_uninstalled_package_never_written(self):
        self.active.unlink(); self.active.symlink_to(self.base/'outside')
        with self.assertRaises(RuntimeError): self.call(request())
        self.active.unlink(); self.active.write_text(ACTIVE); os.link(self.active, self.base/'alias')
        with self.assertRaises(RuntimeError): self.call(request())
        with patch.object(keymap, '_scheme_attach') as attach:
            with self.assertRaisesRegex(RuntimeError, 'not installed'):
                keymap.keymap_schemes('/lxc', 'default', 'default', request(), lambda *args: '', GEN)
            attach.assert_not_called()

    def test_failed_second_rename_rolls_back_complete_pair(self):
        report = self.call(request()); self.fail = True
        with self.assertRaises(RuntimeError): self.call(request('reset', 'default', revision=report['revision']))
        self.assertEqual(self.active.read_text(), ACTIVE)
        self.assertFalse((self.folder/f'.schemes-{PKG}').exists())
        self.assertFalse((self.folder/'.scheme-lock').exists())

    def test_sigkill_between_renames_recovers_old_pair_on_next_read(self):
        report = self.call(request()); self.crash = True
        with self.assertRaises(RuntimeError): self.call(request('reset', 'default', revision=report['revision']))
        self.assertNotEqual(self.active.read_text(), ACTIVE)
        self.assertTrue((self.folder/'.scheme-lock/ready').exists())
        self.crash = False
        recovered = self.call(request())
        self.assertEqual(self.active.read_text(), ACTIVE)
        self.assertEqual(recovered['revision'], report['revision'])
        self.assertFalse((self.folder/'.scheme-lock').exists())

    def test_new_app_empty_directory_and_dead_editor_marker(self):
        self.active.unlink()
        marker = self.base/'keymap-editing'; marker.write_text(f'{GEN} {PKG} 99999999\n')
        report = self.call(request())
        report = self.call(request('create', name='New', revision=report['revision']))
        self.assertEqual(len(report['schemes']), 2)
        self.assertFalse(marker.exists())

    def test_reused_lock_pid_and_interrupted_initialization_recover(self):
        lock=self.folder/'.scheme-lock'; lock.mkdir()
        (lock/'pid').write_text(f'{os.getpid()} 1 {GEN}\n')  # Live PID, different process start time.
        self.assertEqual(self.call(request())['selected'],'default')
        self.assertFalse(lock.exists())
        lock.mkdir(); (lock/'pid-tmp').write_text('interrupted')
        os.utime(lock,(1,1))
        self.call(request()); self.assertFalse(lock.exists())

    def test_reused_editor_pid_and_new_live_marker_are_distinguished(self):
        report=self.call(request())
        marker=self.base/'keymap-editing'
        marker.write_text(f'{GEN} {PKG} {os.getpid()} 1\n')
        report=self.call(request('create',name='Recovered',revision=report['revision']))
        self.assertFalse(marker.exists())
        start=Path(f'/proc/{os.getpid()}/stat').read_text().split()[21]
        marker.write_text(f'{GEN} {PKG} {os.getpid()} {start}\n')
        with self.assertRaisesRegex(RuntimeError,'Save or cancel'):
            self.call(request('reset','default',revision=report['revision']))

    def test_uninitialized_lock_and_live_writer_do_not_allow_mutation(self):
        lock=self.folder/'.scheme-lock'; lock.mkdir()
        with self.assertRaisesRegex(RuntimeError,'initializing'): self.call(request())
        start=Path(f'/proc/{os.getpid()}/stat').read_text().split()[21]
        (lock/'pid').write_text(f'{os.getpid()} {start} {GEN}\n')
        with self.assertRaisesRegex(RuntimeError,'busy'): self.call(request())
        self.assertEqual(self.active.read_text(),ACTIVE)


class SchemeAdapterTests(unittest.TestCase):
    def test_managed_adapter_carries_generation_and_checks_response_scope(self):
        spec=importlib.util.spec_from_file_location('scheme_android',Path(__file__).resolve().parents[1]/'services/runtime-android.py')
        android=importlib.util.module_from_spec(spec);spec.loader.exec_module(android)
        backend=android.AndroidBackend(None)
        status={'state':'Frozen','generation':GEN,'capabilities':['keymap_schemes']}
        result={'runtime_id':'default','package':PKG,'revision':'a'*64,'selected':'default','schemes':[]}
        response={'state':'Running','generation':GEN,'keymap_scheme':result}
        with patch.object(backend,'status',side_effect=[status,response]) as call:
            self.assertEqual(backend.keymap_scheme('default',request()),result)
            self.assertEqual(call.call_args.args,('default','keymap_scheme',dict(request(),generation=GEN)))
        for changed in (dict(response,generation='c'*32),dict(response,keymap_scheme=dict(result,package='com.other.app')),dict(response,app_error='Refused')):
            with patch.object(backend,'status',side_effect=[status,changed]),self.assertRaises(RuntimeError): backend.keymap_scheme('default',request())


if __name__ == '__main__': unittest.main()
