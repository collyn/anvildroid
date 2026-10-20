import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest
import os
import tempfile
import sys

spec=importlib.util.spec_from_file_location('keymap_worker',Path(__file__).resolve().parents[1]/'services/runtime-worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)

class KeymapTests(unittest.TestCase):
    def test_v4_parameters_and_shared_keyboard_reservations(self):
        def row(key, kind, **changes):
            values=[key,32768,32768,0,kind,49152,32768,250,3,0,1,8192,80,8192,8192,100,150,0,0]+[0]*12
            fields=('key','x','y','hold','kind','ex','ey','duration','repeats','source','end','radius','ramp','lw','lh','sx','sy','invert','activation',
                    'up','left','down','right','walk','walk_radius','suspend','drag','skill','cancel','cx','cy')
            for name,value in changes.items(): values[fields.index(name)]=value
            return ' '.join(map(str,values))
        def profile(rows): return 'BEGIN\nANVIL_KEYMAP 4 default com.game.test '+str(len(rows))+'\n'+'\n'.join(rows)+'\nEND\n'
        movement=row(103,1,up=103,left=105,down=108,right=106,walk=42,walk_radius=40)
        look=row(57,8,activation=1,suspend=56)
        skill=row(16,4,skill=2,cancel=18,cx=100,cy=200)
        walking=row(273,9,source=1)
        report=worker.parse_keymaps(profile([movement,look,skill,walking]),'default')
        self.assertEqual(report['invalid_count'],0)
        self.assertEqual(report['profiles'][0]['bindings'][0]['up_key'],103)
        self.assertEqual(report['profiles'][0]['bindings'][2]['skill_mode'],2)
        self.assertEqual(report['profiles'][0]['bindings'][3]['kind'],'walk')
        for rows in ([row(103,1,up=103,left=103,down=108,right=106)],
                     [row(103,1,up=103,left=105,down=108,right=106,walk=60)],
                     [movement,row(42,0)],[look,row(56,0)],[skill,row(18,0)],
                     [row(273,9,source=1,radius=0)],[row(16,0,drag=1)],
                     [row(272,0,source=1,end=2,drag=1),row(273,0,source=1,end=2,drag=1)]):
            self.assertEqual(worker.parse_keymaps(profile(rows),'default')['invalid_count'],1,rows)
        canonical=profile([look])
        for bad in (canonical.replace('ANVIL_KEYMAP',' ANVIL_KEYMAP'), canonical.replace('4 default','4\r default'), canonical.replace('4 default','4\ndefault')):
            self.assertEqual(worker.parse_keymaps(bad,'default')['invalid_count'],1)
        self.assertEqual(worker.parse_keymaps(canonical.replace('ANVIL_KEYMAP ','ANVIL_KEYMAP\t'),'default')['invalid_count'],0)

    def test_inventory_scope_and_validation(self):
        good='BEGIN\nANVIL_KEYMAP 1 r-one com.game.test 2\n30 100 200 0\n57 65535 0 1\nEND\n'
        report=worker.parse_keymaps(good,'r-one')
        self.assertEqual(report['profiles'][0]['bindings'][0]['kind'],'hold')
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

    def test_v3_sources_regions_and_conflicts(self):
        def profile(rows):
            return 'BEGIN\nANVIL_KEYMAP 3 r-one com.game.test '+str(len(rows))+'\n'+'\n'.join(rows)+'\nEND\n'
        look='273 32768 32768 0 8 49152 32768 250 3 1 0 0 0 8192 8192 100 150 1 1'
        pointer='0 0 0 0 7 0 0 250 3 2 0 0 0 0 0 0 0 0 0'
        basic='273 100 200 0 0 0 0 50 3 0 1 0 0 0 0 0 0 0 0'
        report=worker.parse_keymaps(profile([look,pointer,basic]),'r-one')
        self.assertEqual(report['invalid_count'],0)
        binding=report['profiles'][0]['bindings'][0]
        self.assertEqual((binding['input_source'],binding['activation'],binding['sensitivity_y']),('mouse','capture',150))
        self.assertEqual(report['profiles'][0]['bindings'][2]['kind'],'tap')
        # The explicit mode is authoritative even if an imported hold bit
        # disagrees. Inventory labels must match native playback/editor.
        for bit,end,label in ((1,1,'tap'),(0,2,'hold'),(0,0,'hold')):
            fields=basic.split();fields[3]=str(bit);fields[10]=str(end)
            item=worker.parse_keymaps(profile([' '.join(fields)]),'r-one')['profiles'][0]['bindings'][0]
            self.assertEqual(item['kind'],label)
        for rows in ([look,look],[pointer,pointer],[look.replace('8192 8192','32767 32767').replace('32768 32768','0 0')],
                     [look.replace('100 150','9 150')],[pointer.replace(' 2 0 0 0',' 0 0 0 0')],
                     [look.replace('273 ','277 ',1)],[look+' 0'],[look.replace(' 1 1',' 2 1')]):
            self.assertEqual(worker.parse_keymaps(profile(rows),'r-one')['invalid_count'],1,rows)
        aim='17 0 0 0 6 0 0 250 3 0 0 0 0 0 0 0 0 0 0'
        self.assertEqual(worker.parse_keymaps(profile([aim,look.replace('273 ','272 ',1)]),'r-one')['invalid_count'],1)

    def test_inventory_limits_and_running_read_detects_oversize(self):
        oversized='BEGIN\nANVIL_KEYMAP 1 r-one com.game.test 0\n'+' '*16384+'\nEND\n'
        self.assertEqual(worker.parse_keymaps(oversized,'r-one')['invalid_count'],1)
        with self.assertRaisesRegex(RuntimeError,'inventory exceeds limit'):
            worker.parse_keymaps('x'*(3*1024*1024+1),'r-one')
        with patch.object(worker.keymap.linux,'run_bounded',return_value='') as run:
            worker.keymap.keymap_profiles('/lxc','container','r-one')
        self.assertIn('head -c 16385',run.call_args.args[-1])
        self.assertEqual(run.call_args.kwargs['output_limit'],3*1024*1024)

    def test_running_inventory_preserves_last_frame_through_process_runner(self):
        # A real subprocess is necessary: a mocked runner hid strip() removing
        # both the last profile newline and its framing delimiter on the host.
        for rows in ('', '16 100 200 0\n'):
            payload = f'ANVIL_KEYMAP 1 r-one com.game.test {int(bool(rows))}\n' + rows
            frame = f'SIZE {len(payload.encode())}\n' + payload + '\n'
            with patch.object(worker.keymap.linux, 'android_command',
                              return_value=(sys.executable, '-c', 'import sys;sys.stdout.write(' + repr(frame) + ')')):
                result = worker.keymap.keymap_profiles('/lxc', 'container', 'r-one')
            self.assertEqual(result['invalid_count'], 0)
            self.assertEqual(len(result['profiles']), 1)
            self.assertEqual(len(result['profiles'][0]['bindings']), int(bool(rows)))

    def test_strict_decimal_lexer_and_trusted_file_framing(self):
        good='ANVIL_KEYMAP 1 default com.game.test 1\n30 100 200 0\n'
        def framed(payload): return 'SIZE '+str(len(payload.encode()))+'\n'+payload+'\n'
        for token in ('+30','3_0','３０','-30'):
            malformed=good.replace('30 100',token+' 100')
            self.assertEqual(worker.keymap.parse_keymap_frames(framed(malformed),'default')['invalid_count'],1)
        for count in ('+1','0_1','１'):
            malformed=good.replace('test 1','test '+count)
            self.assertEqual(worker.keymap.parse_keymap_frames(framed(malformed),'default')['invalid_count'],1)
        injected='garbage\nEND\nBEGIN\n'+good
        self.assertEqual(worker.keymap.parse_keymap_frames(framed(injected),'default')['profiles'],[])
        oversized=(good+' '*16385)[:16385]
        self.assertEqual(worker.keymap.parse_keymap_frames('SIZE 20000\n'+oversized+'\n','default')['profiles'],[])
        with self.assertRaisesRegex(RuntimeError,'frame'):
            worker.keymap.parse_keymap_frames('SIZE 99\n'+good+'\n','default')
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)/'waydroid_tmp/anvildroid/keymaps';folder.mkdir(parents=True)
            (folder/'injected').write_text(injected)
            fd=os.open(temporary,os.O_RDONLY|os.O_DIRECTORY)
            try:
                result=worker.keymap.keymap_profiles_at(fd,'default')
                self.assertEqual(result['profiles'],[]);self.assertEqual(result['invalid_count'],1)
            finally: os.close(fd)

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
            (folder / 'oversized').write_bytes(b'x' * 16385)
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
