import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest
import os
import tempfile

spec=importlib.util.spec_from_file_location('keymap_worker',Path(__file__).resolve().parents[1]/'services/runtime-worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)

class KeymapTests(unittest.TestCase):
    def test_inventory_scope_and_validation(self):
        good='BEGIN\nANVIL_KEYMAP 1 r-one com.game.test 2\n30 100 200 0\n57 65535 0 1\nEND\n'
        report=worker.parse_keymaps(good,'r-one')
        self.assertEqual(report['profiles'][0]['bindings'][1]['kind'],'hold')
        self.assertEqual(report['invalid_count'],0)
        self.assertEqual(worker.parse_keymaps(good,'r-two')['invalid_count'],1)
        self.assertEqual(worker.parse_keymaps(good.replace('57 65535','30 65535'),'r-one')['invalid_count'],1)
        self.assertEqual(worker.parse_keymaps(good.replace('65535','65536'),'r-one')['invalid_count'],1)
        self.assertEqual(worker.parse_keymaps(good.replace('1 r-one','2 r-one'),'r-one')['invalid_count'],1)
        self.assertEqual(worker.parse_keymaps(good.replace('END\n',''),'r-one')['invalid_count'],1)

    def test_advanced_profile_and_bounds(self):
        header='BEGIN\nANVIL_KEYMAP 2 r-one com.game.test 1\n'
        for kind in range(1,7):
            good=header+f'17 32768 32768 0 {kind} 49152 32768 250 3\nEND\n'
            result=worker.parse_keymaps(good,'r-one')
            self.assertEqual(result['invalid_count'],0)
            self.assertEqual(result['profiles'][0]['bindings'][0]['duration_ms'],250)
            for bad in (good.replace('250 3','49 3'),good.replace('250 3','250 21'),
                        good.replace('49152','65536'),good.replace('250 3','-1 3'),good.replace('\nEND',' 123\nEND'),good.replace('\nEND','\nunknown\nEND')):
                self.assertEqual(worker.parse_keymaps(bad,'r-one')['invalid_count'],1)
        self.assertEqual(worker.parse_keymaps(header+'17 0 0 0 5 0 0 5000 20\nEND\n','r-one')['invalid_count'],1)
        conflict=header.replace(' 1\n',' 2\n')+'17 0 0 0 1 0 0 250 3\n30 0 0 0 0 0 0 0 0\nEND\n'
        self.assertEqual(worker.parse_keymaps(conflict,'r-one')['invalid_count'],1)

    def test_saved_empty_profile_remains_visible_and_editable(self):
        report = worker.parse_keymaps('BEGIN\nANVIL_KEYMAP 1 default com.android.settings 0\nEND\n', 'default')
        self.assertEqual(report['profiles'][0]['package'], 'com.android.settings')
        self.assertEqual(report['profiles'][0]['bindings'], [])

    def test_frozen_inventory_reads_pinned_directory_and_preserves_unsafe_profiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'waydroid_tmp/anvildroid/keymaps'
            folder.mkdir(parents=True)
            good = 'ANVIL_KEYMAP 1 default com.game.test 1\n30 100 200 0\n'
            (folder / 'com.game.test').write_text(good)
            (folder / 'wrong.scope').write_text(good.replace('default', 'r-other'))
            (folder / 'oversized').write_bytes(b'x' * 4097)
            (folder / 'invalid.utf8').write_bytes(b'\xff\n')
            (folder / 'symlink').symlink_to(folder / 'com.game.test')
            os.mkfifo(folder / 'fifo')
            (folder / '.save-temp').write_text('in progress')
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                result = worker.keymap.keymap_profiles_at(fd, 'default')
                self.assertEqual([p['package'] for p in result['profiles']], ['com.game.test'])
                self.assertEqual(result['invalid_count'], 5)
                self.assertEqual((folder / 'com.game.test').read_text(), good)
                self.assertTrue((folder / 'symlink').is_symlink())
                os.fstat(fd)  # Caller still owns its descriptor.
            finally:
                os.close(fd)

    def test_frozen_inventory_missing_folder_and_symlink_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertEqual(worker.keymap.keymap_profiles_at(fd, 'default')['profiles'], [])
                (root / 'waydroid_tmp').symlink_to(root, target_is_directory=True)
                with self.assertRaises(OSError):
                    worker.keymap.keymap_profiles_at(fd, 'default')
            finally:
                os.close(fd)

    def test_request_is_generation_scoped_and_not_a_root_guest_write(self):
        calls=[];nonce='a'*32;generation='b'*32
        def android(*args):
            calls.append(args)
            if args==('cmd','activity','stack','list'):
                return 'taskId=7: com.game.test/.Main bounds=[0,0][800,600] visible=true'
            return nonce+' editing\n'
        with patch.object(worker.uuid,'uuid4',return_value=type('Nonce',(),{'hex':nonce})()),patch.object(worker,'run') as run:
            result=worker.open_keymap(Path('/instance'),'r-one',generation,'com.game.test',android)
        self.assertEqual(result['action'],'keymap_edit')
        args=run.call_args.args
        self.assertIn('--uid',args);self.assertEqual(args[args.index('--uid')+1],'1000')
        self.assertIn(generation,args[-1]);self.assertIn('set -C',args[-1])
        self.assertNotIn('chown',args[-1])

    def test_ambiguous_or_closed_app_never_writes_request(self):
        line='taskId=7: com.game.test/.Main bounds=[0,0][800,600] visible=true\n'
        for inventory in ('',line+line):
            with patch.object(worker,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'exactly one'):
                    worker.open_keymap(Path('/instance'),'r-one','b'*32,'com.game.test',lambda *args:inventory)
                run.assert_not_called()

if __name__=='__main__':unittest.main()
