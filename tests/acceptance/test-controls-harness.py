#!/usr/bin/env python3
"""Inert checks: reject unsafe input routing and ambiguous surface coordinates."""
import copy
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

probe=load('probe-android-controls')
live=load('live-android')
isolation=load('probe-editor-isolation')
game=load('probe-luanti-game')
gpu_desktop=load('gpu-desktop')
recovery=load('probe-controls-recovery')
default_controls=load('default-controls')

class Safety(unittest.TestCase):
    def test_gpu_renderer_requires_explicit_hardware_witness_and_refuses_cpu_fallback(self):
        hardware='direct rendering: Yes\nAccelerated: yes\nOpenGL renderer string: AMD Radeon RX 6600\n'
        self.assertEqual(gpu_desktop.hardware_renderer(hardware),'AMD Radeon RX 6600')
        for text in (hardware.replace('Accelerated: yes','Accelerated: no'),
                     hardware.replace('direct rendering: Yes','direct rendering: No'),
                     hardware.replace('AMD Radeon RX 6600','llvmpipe (LLVM 20)'),
                     hardware.replace('AMD Radeon RX 6600','SwiftShader'),
                     'OpenGL renderer string: AMD Radeon RX 6600\n'):
            with self.subTest(text=text),self.assertRaises(RuntimeError):gpu_desktop.hardware_renderer(text)

    def test_recovery_oracle_rejects_rotated_logs_and_stale_contacts(self):
        with self.assertRaisesRegex(RuntimeError,'buffer changed'):recovery.tail(['original'],['replaced'])
        self.assertEqual(recovery.tail(['original'],['original','new']),['new'])
        with self.assertRaises(RuntimeError):recovery.active([])
        with self.assertRaises(RuntimeError):recovery.active(['WARNING stale','AnvilTouchProbe: ACTION_MOVE activeAfter=3'])
        self.assertEqual(recovery.active(['AnvilTouchProbe: ACTION_POINTER_DOWN(2) activeAfter=3']),3)

    def test_default_controls_refuse_host_context_before_running_commands(self):
        with patch.object(default_controls.os,'getpid',return_value=200),\
             patch.object(default_controls.subprocess,'run') as run,\
             self.assertRaisesRegex(RuntimeError,'private disposable'):
            default_controls.Session(Path('/tmp/anvil-default-fixture'),{},None,None)
        run.assert_not_called()
        with patch.object(default_controls.os,'getpid',return_value=200),\
             patch.object(default_controls.subprocess,'run') as run,\
             self.assertRaisesRegex(RuntimeError,'isolated PID1'):
            default_controls.start_display(Path('/tmp/anvil-default-fixture'),{},[])
        run.assert_not_called()

    def test_default_disconnect_refuses_replaced_guest_process_before_kill(self):
        executable='/vendor/bin/hw/android.hardware.graphics.composer@2.1-service'
        calls=[]
        def command(args):
            calls.append(args)
            if 'pidof' in args[-2]:return '123\n'
            if args[-2:]==['/system/bin/getprop','init.svc.vendor.hwcomposer-2-1']:return 'stopping\n'
            if args[-2:]==['/system/bin/cat','/proc/123/cmdline']:
                return executable+'\0' if sum('/proc/123/cmdline' in row for row in calls)==1 else '/system/bin/unrelated\0'
            return ''
        session=SimpleNamespace(guest=['private-guest','--'],run=command)
        with patch.object(default_controls.time,'monotonic',side_effect=[0,4]),\
             self.assertRaisesRegex(RuntimeError,'changed before crash'):
            default_controls.disconnect_hwc(session)
        self.assertFalse(any('/system/bin/kill' in row for row in calls))
        self.assertTrue(all(row[:2]==['private-guest','--'] for row in calls))

    def test_default_attach_rediscovers_window_replaced_after_geometry_settles(self):
        from itertools import count
        session=object.__new__(default_controls.Session)
        session.nested=False;session.x=['private-xdotool'];session.guest=['private-guest','--']
        session.offset=(0,0);session.m={'id':'default'}
        session.logs=lambda:['AnvilTouchProbe: SIZE contentPx=448x704',
                             'AnvilTouchProbe: FOCUS=true active=0']
        windows=iter(['old-window','new-window']);focused=[]
        def discover():session.window=next(windows)
        def command(args,**kwargs):
            if 'windowfocus' in args:
                focused.append(session.window)
                if session.window=='old-window':
                    raise subprocess.CalledProcessError(1,args,stderr=b'BadWindow')
            return ''
        session.run=command
        with tempfile.TemporaryDirectory() as directory:
            session.output=Path(directory)
            with patch.object(default_controls.recovery.Session,'attach',side_effect=discover) as base,\
                 patch.object(default_controls.probe,'editor_attachment',return_value=(0,0,448,704)),\
                 patch.object(default_controls.time,'monotonic',side_effect=count(0,.25)),\
                 patch.object(default_controls.time,'sleep'):
                session.attach()
            self.assertEqual(base.call_count,2)
        self.assertEqual(focused,['old-window','new-window'])

    def test_recovery_profile_uses_valid_production_model_and_system_owned_save(self):
        session=object.__new__(recovery.Session);session.m={'id':'r-'+'b'*32};session.guest=['guest','--']
        authored=[]
        def command(args,**kwargs):
            if 'input' in kwargs:authored.append((args,kwargs['input'].decode()))
            return ''
        with patch.object(session,'run',side_effect=command),patch.object(session,'request'),\
             patch.object(session,'attach'),patch.object(session,'profile',side_effect=lambda:authored[-1][1]):
            session.prepare()
        args,text=authored[-1];self.assertIn('--uid',args);self.assertEqual(args[args.index('--uid')+1],'1000')
        rows=[list(map(int,row.split())) for row in text.splitlines()[1:]]
        self.assertEqual([row[4] for row in rows],[0,1,7]);self.assertEqual(rows[-1][9],2)
        with tempfile.TemporaryDirectory() as directory:
            header=(Path(__file__).resolve().parents[2]/'native/bridge/keymap-model.h').as_posix()
            source=Path(directory)/'profile.c';exe=Path(directory)/'profile'
            source.write_text('#include "'+header+'"\nint main(void){struct keymap_model m={.count=3,.points={'+
                ','.join('{'+','.join(map(str,row))+'}' for row in rows)+'}};return !keymap_valid(&m,1);}\n')
            subprocess.run(['cc',str(source),'-o',str(exe)],check=True,capture_output=True,timeout=20)
            subprocess.run([str(exe)],check=True,capture_output=True,timeout=5)

    def test_game_rejects_changed_official_bytes_before_guest_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'changed.apk';path.write_bytes(b'changed official archive')
            session=object.__new__(game.Session)
            with patch.object(session,'raw',create=True) as raw,patch.object(session,'request',create=True) as request,\
                 self.assertRaises(RuntimeError):session.prepare(path,path)
            raw.assert_not_called();request.assert_not_called()

    def test_game_normalization_makes_small_editor_reachable_and_refuses_missing_app(self):
        session=object.__new__(game.Session);session.nested=True;session.m={'id':'r-fixture'}
        session.transport={'x11_window':'42'};session.x=['xdotool']
        def scene(x,y,r,b):
            return (f'\n\tView 1 role xdg_toplevel of waydroid.anvildroid.r-fixture.{game.PACKAGE},'
                    f'\nposition: ({x}, {y}) -> ({r}, {b})\n')
        with patch.object(session,'scene',side_effect=[scene(0,32,448,315),scene(0,172,448,455),scene(0,32,1600,1000)]),\
             patch.object(session,'run') as run,patch.object(game.time,'sleep'):
            session.normalize()
        commands=[call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands),2)
        self.assertIn('Super_L',commands[0]);self.assertIn('--repeat',commands[1])
        with patch.object(session,'scene',return_value='unrelated app'),patch.object(session,'run') as run,\
             self.assertRaisesRegex(RuntimeError,'Ambiguous mapped game'):
            session.normalize()
        run.assert_not_called()

    def test_game_archive_rejects_traversal_and_links(self):
        for name,kind in [('minetest_game-5.8.0/../escape',tarfile.REGTYPE),
                          ('/minetest_game-5.8.0/escape',tarfile.REGTYPE),
                          ('minetest_game-5.8.0/link',tarfile.SYMTYPE)]:
            data=io.BytesIO()
            with tarfile.open(fileobj=data,mode='w') as archive:
                entry=tarfile.TarInfo(name);entry.type=kind;entry.linkname='/outside'
                archive.addfile(entry,io.BytesIO())
            with self.subTest(name=name),self.assertRaises(RuntimeError):game.assets(data.getvalue())

    def test_game_oracle_refuses_stale_sequence_and_replaced_server(self):
        session=object.__new__(game.Session);session.epoch='current'
        with patch.object(session,'game_state',return_value={'epoch':'current','seq':12}),\
             patch.object(game.time,'monotonic',side_effect=[0,1]),self.assertRaises(RuntimeError):
            session.wait(lambda v:True,'stale',after=12,timeout=.1)
        with patch.object(session,'game_state',return_value={'epoch':'replaced','seq':999}),\
             self.assertRaisesRegex(RuntimeError,'epoch changed'):
            session.wait(lambda v:True,'replaced',after=12)

    def test_game_profiles_pass_production_validator_and_editor_can_own_files(self):
        with tempfile.TemporaryDirectory() as directory:
            session=object.__new__(game.Session);session.m={'id':'r-'+'a'*32}
            session.output=Path(directory);session.guest=['lxc-attach','--']
            with patch.object(session,'run',return_value='') as run:session.write_profile()
            commands=[c.args[0] for c in run.call_args_list]
            self.assertIn(['lxc-attach','--','/system/bin/chown','1000:1000',
                           '/data/waydroid_tmp/anvildroid/keymaps',
                           '/data/waydroid_tmp/anvildroid/keymaps/'+game.PACKAGE],commands)
            self.assertIn('--uid',commands[-1]);self.assertEqual(commands[-1][2],'1000')
            text=(session.output/'game-profile.txt').read_text();lines=text.splitlines()
            rows=[list(map(int,row.split())) for row in lines[1:]]
            self.assertEqual(lines[0].split()[2:4],[session.m['id'],game.PACKAGE])
            header=(Path(__file__).resolve().parents[2]/'native/bridge/keymap-model.h').as_posix()
            def model(values):return '{.count='+str(len(values))+',.points={'+','.join('{'+','.join(map(str,row))+'}' for row in values)+'}}'
            captured=copy.deepcopy(rows);look=next(row for row in captured if row[4]==8)
            look[0],look[9],look[18]=38,0,1
            invalid=captured+[copy.deepcopy(rows[3])]
            source=Path(directory)/'game-profile.c'
            source.write_text('#include "'+header+'"\nint main(void) {\n'+
                '\n'.join('struct keymap_model m'+str(i)+'='+model(values)+';' for i,values in enumerate([rows,captured,invalid]))+
                '\nreturn !(keymap_valid(&m0,1) && keymap_valid(&m1,1) && !keymap_valid(&m2,1));\n}\n')
            executable=Path(directory)/'validate'
            subprocess.run(['cc',str(source),'-o',str(executable)],check=True,capture_output=True,timeout=20)
            subprocess.run([str(executable)],check=True,capture_output=True,timeout=5)

    def test_editor_isolation_refuses_personal_or_non_fixture_endpoints(self):
        for path in ('/var/lib/waydroid/acceptance.json','/tmp/personal/acceptance.json','/home/user/acceptance.json'):
            with self.subTest(path=path),patch.object(isolation.subprocess,'run') as run,self.assertRaises(RuntimeError):
                isolation.Endpoint(Path(path),1,Path('/tmp/evidence'))
            run.assert_not_called()

    def test_editor_isolation_rechecks_worker_and_compositor_identity(self):
        endpoint=object.__new__(isolation.Endpoint)
        endpoint.identity=('123','mnt:private',b'worker');endpoint.nested=True
        endpoint.compositor_identity=('456','mnt:compositor','pid:private',b'weston')
        with patch.object(endpoint,'state',return_value=endpoint.identity),\
             patch.object(endpoint,'compositor_state',return_value=endpoint.compositor_identity):endpoint.guard()
        with patch.object(endpoint,'state',return_value=('124','mnt:private',b'worker')),self.assertRaises(RuntimeError):endpoint.guard()
        with patch.object(endpoint,'state',return_value=endpoint.identity),\
             patch.object(endpoint,'compositor_state',return_value=('457','mnt:compositor','pid:private',b'weston')),\
             self.assertRaises(RuntimeError):endpoint.guard()

    def test_session_masks_host_endpoints_and_discards_inherited_environment(self):
        identity=SimpleNamespace(st_dev=9,st_ino=12)
        account=SimpleNamespace(pw_dir='/home/fixture',pw_gid=1000)
        environment={'PATH':'/host/untrusted','HOME':'/home/personal','DISPLAY':':1',
                     'DBUS_SESSION_BUS_ADDRESS':'unix:path=/private/old-bus',
                     'PULSE_SERVER':'unix:/custom/audio','SSH_AUTH_SOCK':'/custom/agent',
                     'XDG_CONFIG_HOME':'/personal/config','SECRET_TEST_TOKEN':'private'}
        with patch.object(live,'validate_root'),patch.object(live.os,'readlink',side_effect=['mnt:test','mnt:parent']),\
             patch.object(live.pwd,'getpwuid',return_value=account),patch.object(live.os,'open',return_value=19),\
             patch.object(live.os,'fstat',return_value=identity),patch.object(live.os,'close') as close,\
             patch.object(Path,'stat',return_value=identity),patch.object(Path,'mkdir'),\
             patch.object(live.os,'chown'),patch.object(live.subprocess,'run') as run,\
             patch.object(live,'bind_fixture_descriptor') as bind,\
             patch.dict(os.environ,environment,clear=True):
            live.isolate_session(Path('/tmp/anvil-live-fixture'),1000)
            self.assertEqual(os.environ['HOME'],'/home/fixture')
            self.assertEqual(os.environ['DBUS_SESSION_BUS_ADDRESS'],'unix:path=/run/no-session-bus')
            for key in ('DISPLAY','PULSE_SERVER','SSH_AUTH_SOCK','XDG_CONFIG_HOME','SECRET_TEST_TOKEN'):
                self.assertNotIn(key,os.environ)
            self.assertNotEqual(os.environ['PATH'],environment['PATH'])
            masks=[call.args[0][-1] for call in run.call_args_list if '-t' in call.args[0]]
            self.assertEqual(masks,['/run','/tmp','/home','/var/lib/waydroid','/var/lib/anvildroid-controller'])
            bind.assert_called_once_with(19,Path('/tmp/anvil-live-fixture'))
            close.assert_called_once_with(19)

    def test_session_refuses_host_mount_namespace_before_masking(self):
        with patch.object(live,'validate_root'),patch.object(live.os,'readlink',return_value='mnt:host'),\
             patch.object(live.subprocess,'run') as run,self.assertRaises(RuntimeError):
            live.isolate_session(Path('/tmp/anvil-live-fixture'),1000)
        run.assert_not_called()

    def metadata(self):
        return dict(owner_uid=1000,generation='a'*32,input_transport=dict(kind='nested-weston-x11',
            x11_display=':77',authority='/run/user/1000/nested-authority',
            wayland_display='wayland-anvil-'+'a'*32,compositor_namespace_pid=10,protocols={'wl_seat':7}))

    def test_transport_rejects_external_targets_and_missing_seat(self):
        base=self.metadata();self.assertEqual(probe.validate_nested_transport(base)['x11_display'],':77')
        for key,value in [('x11_display',':0'),('authority','/home/user/.Xauthority'),
                          ('wayland_display','wayland-0'),('compositor_namespace_pid',1),('protocols',{})]:
            bad=copy.deepcopy(base);bad['input_transport'][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):probe.validate_nested_transport(bad)
        bad=copy.deepcopy(base);bad['owner_uid']=0
        with self.assertRaises(RuntimeError):probe.validate_nested_transport(bad)

    def scene(self,rect='(200, 100) -> (648, 803)',suffix='',extra=''):
        return "\n\tView 0 (role xdg_toplevel, top-level window 'Touch Probe' of waydroid.anvildroid.r-test.org.anvildroid.touchprobe"+suffix+", 0x1):\n\t\tposition: "+rect+'\n'+extra

    def test_geometry_requires_exact_scoped_mapped_app(self):
        self.assertEqual(probe.scene_rectangle(self.scene(),'r-test'),(200,100))
        self.assertEqual(probe.scene_rectangle(self.scene('(216, 89) -> (920, 537)'),'r-test'),(216,89))
        for scene in [self.scene()+self.scene(),self.scene(suffix='.evil'),
                      self.scene(extra='[view is not mapped!]'),self.scene('(-1, 0) -> (448, 703)'),
                      self.scene('(0, 0) -> (1700, 1000)'),self.scene('(0, 0) -> (200, 300)'),
                      self.scene('(216, 89) -> (920, 508)')]:
            with self.subTest(scene=scene),self.assertRaises(RuntimeError):probe.scene_rectangle(scene,'r-test')

    def test_placement_setup_keeps_strict_bounds_and_refuses_unreachable_headers(self):
        self.assertIsNone(probe.scene_header_move(self.scene(),'r-test'))
        shifted=self.scene('(421, 485) -> (869, 1189)')
        self.assertEqual(probe.scene_header_move(shifted,'r-test'),(645,469,229,49))
        with self.assertRaisesRegex(RuntimeError,'outside'):probe.scene_rectangle(shifted,'r-test')
        for invalid in [self.scene('(0, 0) -> (1700, 1000)'),
                        self.scene('(200, 1200) -> (648, 1904)'),shifted+shifted]:
            with self.subTest(scene=invalid),self.assertRaises(RuntimeError):probe.scene_header_move(invalid,'r-test')

    def test_visible_content_oracle_rejects_background_markers_and_missing_app_frame(self):
        dark=bytes((18,24,30));white=bytes((255,255,255))
        valid=dark*(64*64-8)+white*8
        self.assertGreater(probe.require_probe_content(valid),.99)
        for invalid in [b'',dark*(64*64),bytes((0,0,0))*(64*64),
                        bytes((120,120,120))*(64*64-8)+white*8]:
            with self.subTest(size=len(invalid)),self.assertRaises(RuntimeError):probe.require_probe_content(invalid)

    def test_default_geometry_requires_exact_stock_app_and_never_managed_peer(self):
        scene=self.scene().replace('waydroid.anvildroid.r-test.','waydroid.')
        self.assertEqual(probe.scene_rectangle(scene,'default'),(200,100))
        for invalid in (self.scene(),scene+scene,scene.replace('touchprobe,','touchprobe.other,'),
                        scene+'[view is not mapped!]'):
            with self.subTest(scene=invalid),self.assertRaises(RuntimeError):probe.scene_rectangle(invalid,'default')

    def event(self,action,count,source='0x1002',tool='FINGER(1)'):
        return f'AnvilTouchProbe: 7 ACTION_{action} source={source} id=0(1,2) tool={tool} activeAfter={count}'

    def test_touch_oracle_accepts_balanced_multitouch_and_cancel(self):
        events=[self.event('DOWN',1),self.event('POINTER_DOWN(1)',2),
                self.event('MOVE',2),self.event('POINTER_UP(1)',1),self.event('UP',0)]
        self.assertEqual(probe.touch_events(events),events)
        self.assertEqual(len(probe.touch_events([self.event('DOWN',1),self.event('CANCEL',0)])),2)

    def test_touch_oracle_rejects_false_positive_terminal_zero(self):
        cases=[[],[self.event('UP',0)],
               [self.event('DOWN',1),self.event('DOWN',1),self.event('UP',0)],
               [self.event('DOWN',1)],
               [self.event('DOWN',1),self.event('MOVE',0),self.event('UP',0)],
               [self.event('DOWN',1),self.event('POINTER_UP(0)',0)],
               [self.event('DOWN',1),self.event('POINTER_DOWN(1)',2),self.event('UP',0)],
               [self.event('DOWN',1,source='0x2002',tool='MOUSE(3)'),self.event('UP',0)],
               [self.event('DOWN',1),self.event('UP',0),'AnvilTouchProbe: WARNING stale'],
               [self.event('DOWN',1)+' tool=MOUSE(3)',self.event('UP',0)],
               ['AnvilTouchProbe: ACTION_UNKNOWN activeAfter=0']]
        for events in cases:
            with self.subTest(events=events),self.assertRaises(RuntimeError):probe.touch_events(events)

    def test_framework_recreation_cancel_is_explicit_and_terminal_only(self):
        cancel='AnvilTouchProbe: 2 ACTION_CANCEL source=0x1002 device=0 raw=(0.0,0.0) id=0(0.0,0.0) tool=UNKNOWN(0) activeAfter=0'
        down=self.event('DOWN',1)
        with self.assertRaises(RuntimeError):probe.touch_events([down,cancel])
        self.assertEqual(probe.touch_events([down,cancel],True),[down,cancel])
        for bad in (cancel.replace('CANCEL','UP'),cancel.replace('device=0','device=-1'),
                    cancel.replace('source=0x1002','source=0x2002'),cancel.replace('raw=(0.0,0.0)','raw=(2.0,0.0)')):
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):probe.touch_events([down,bad],True)
        with self.assertRaises(RuntimeError):probe.touch_events([cancel],True)

    def test_v3_profile_preserves_contract_order_and_explicit_hold_mode(self):
        row=probe.v3_point(key=19,kind=2,source=0,end_mode=2,duration=200)
        self.assertEqual(len(row),19)
        self.assertEqual(row[:5],(19,32000,32000,1,2))
        self.assertEqual(row[7:11],(200,1,0,2))
        profile=probe.v3_profile('r-fixture',[row])
        self.assertTrue(profile.startswith('ANVIL_KEYMAP 3 r-fixture org.anvildroid.touchprobe 1\n'))
        self.assertEqual(tuple(map(int,profile.splitlines()[1].split())),row)
        for rows in ([],[(1,)*18],[(1,)*18+('2',)]):
            with self.subTest(rows=rows),self.assertRaises(RuntimeError):probe.v3_profile('r-fixture',rows)
        with self.assertRaises(RuntimeError):probe.v3_point(unrecognized=1)

    def test_finger_samples_ignore_mapper_logs_and_preserve_pointer_identity(self):
        rows=['AnvilKeymap: touch package=example action=0 pointer=1 xy=4,5',
              'AnvilTouchProbe: 4 ACTION_DOWN source=0x1002 id=7(12.5,90.0) tool=FINGER(1) activeAfter=1',
              'AnvilTouchProbe: 5 ACTION_MOVE source=0x1002 id=7(22.5,90.0) tool=FINGER(1) activeAfter=1']
        self.assertEqual(probe.finger_samples(rows),[('DOWN',7,12.5,90.0),('MOVE',7,22.5,90.0)])

    def test_look_rebase_accepts_clipped_move_then_lift_without_reverse_drag(self):
        def row(action,x):
            return f'AnvilTouchProbe: 1 ACTION_{action} id=0({x},90.0) tool=FINGER(1) activeAfter=1'
        probe.assert_look_rebase([row('MOVE',80),row('UP',80),row('DOWN',50)],50,90)
        probe.assert_look_rebase([row('UP',80),row('DOWN',50)],50,90)
        for rows in ([row('MOVE',80),row('MOVE',50),row('UP',50),row('DOWN',50)],
                     [row('UP',80),row('MOVE',50),row('DOWN',50)],
                     [row('UP',80),row('DOWN',60)]):
            with self.subTest(rows=rows),self.assertRaises(RuntimeError):probe.assert_look_rebase(rows,50,90)

    def test_v4_profile_preserves_contract_and_scope(self):
        row=probe.v4_point(key=23,kind=1,up_key=23,left_key=36,down_key=37,right_key=38,
                           walk_key=42,walk_radius=40)
        self.assertEqual(len(row),31)
        self.assertEqual(row[19:25],(23,36,37,38,42,40))
        profile=probe.v4_profile('r-fixture',[row])
        self.assertTrue(profile.startswith('ANVIL_KEYMAP 4 r-fixture org.anvildroid.touchprobe 1\n'))
        self.assertEqual(tuple(map(int,profile.splitlines()[1].split())),row)
        for rows in ([],[(1,)*30],[(1,)*30+('2',)]):
            with self.subTest(rows=rows),self.assertRaises(RuntimeError):probe.v4_profile('r-fixture',rows)
        with self.assertRaises(RuntimeError):probe.v4_point(unrecognized=1)

    def test_editor_click_targets_follow_compact_and_full_layout(self):
        compact=probe.editor_targets(448,670)
        self.assertEqual(compact['add'],(201,27))
        self.assertEqual(compact['save'],(333,57))
        self.assertEqual(compact['cancel'],(399,57))
        self.assertEqual(compact['bind_key'],(302,111))
        portrait=probe.editor_targets(464,824)
        self.assertEqual(portrait['add'],(217,27))
        self.assertEqual(portrait['save'],(349,57))
        self.assertEqual(portrait['cancel'],(415,57))
        self.assertEqual(portrait['bind_key'],(318,111))
        full=probe.editor_targets(900,700)
        self.assertEqual(full['add'],(653,27))
        self.assertEqual(full['bind_key'],(754,111))
        self.assertEqual(full['save'],(785,57))
        self.assertEqual(full['cancel'],(851,57))
        for dimensions in [(200,700),(448,300)]:
            with self.assertRaises(RuntimeError):probe.editor_targets(*dimensions)

    def test_editor_attachment_requires_exact_scoped_app_geometry(self):
        row='AnvilKeymapOverlay: attached waydroid.anvildroid.r-fixture.org.anvildroid.touchprobe origin=0,0 size=448x670'
        self.assertEqual(probe.editor_attachment([row],'r-fixture'),(0,0,448,670))
        bare=row.replace('waydroid.anvildroid.r-fixture.','').replace('448x670','464x824')
        self.assertEqual(probe.editor_attachment([bare],'r-fixture'),(0,0,464,824))
        for bad in [bare.replace('touchprobe origin','touchprobe.evil origin'),
                    bare.replace('touchprobe origin','other origin'),row.replace('r-fixture','r-other'),row.replace('origin=0,0','origin=-1,0'),row.replace('448x670','200x300')]:
            with self.assertRaises(RuntimeError):probe.editor_attachment([bad],'r-fixture')

    def test_passive_content_uses_actual_narrow_fit_without_relaxing_editor(self):
        row='AnvilKeymapOverlay: attached org.anvildroid.touchprobe origin=209,0 size=285x448'
        with self.assertRaises(RuntimeError):probe.editor_attachment([row],'r-fixture')
        geometry=probe.editor_attachment([row],'r-fixture',interactive=False)
        self.assertEqual(geometry,(209,0,285,448))
        self.assertEqual(probe.editor_frame(geometry,713,486,False),(5,33))
        for bad in (row.replace('touchprobe origin','other origin'),
                    row.replace('285x448','0x448'),row.replace('285x448','8193x448')):
            with self.assertRaises(RuntimeError):probe.editor_attachment([bad],'r-fixture',interactive=False)

    def test_capture_requires_both_actual_protocol_capabilities(self):
        protocols={'zwp_relative_pointer_manager_v1':1,'zwp_pointer_constraints_v1':1}
        probe.require_capture_protocols(protocols)
        for bad in ({},{'zwp_relative_pointer_manager_v1':1},
                    {**protocols,'zwp_pointer_constraints_v1':0},
                    {**protocols,'zwp_pointer_constraints_v1':True}):
            with self.subTest(protocols=bad),self.assertRaises(RuntimeError):probe.require_capture_protocols(bad)

    def test_capture_relative_oracle_rejects_wrong_direction_or_axis(self):
        def row(x,y):
            return f'AnvilTouchProbe: 1 ACTION_MOVE id=0({x},{y}) tool=FINGER(1) activeAfter=1'
        probe.require_relative_direction([row(108,90)],100,90)
        for rows in ([],[row(100,90)],[row(92,90)],[row(108,98)]):
            with self.subTest(rows=rows),self.assertRaises(RuntimeError):probe.require_relative_direction(rows,100,90)

    def test_editor_recovery_accepts_timed_tap_before_keyup_or_hold_after_keyup(self):
        down=self.event('DOWN',1);up=self.event('UP',0)
        self.assertFalse(probe.gesture_complete([]))
        self.assertFalse(probe.gesture_complete([down]))
        # Timed tap terminal can already be in the keydown-stage snapshot.
        keydown_stage=[down,up];keyup_stage=[]
        self.assertTrue(probe.gesture_complete(keydown_stage+keyup_stage))
        # Hold terminal arrives only in the keyup-stage snapshot.
        keydown_stage=[down];keyup_stage=[up]
        self.assertTrue(probe.gesture_complete(keydown_stage+keyup_stage))
        with self.assertRaises(RuntimeError):probe.gesture_complete([down,down,up])

    def test_editor_frame_accepts_raw_and_centered_fitted_content(self):
        for nested,host in [(True,(464,824)),(False,(474,862))]:
            expected=(0,0) if nested else (5,33)
            for geometry in [(0,0,464,824),(0,47,464,729),(0,48,464,729),(52,0,360,824)]:
                with self.subTest(nested=nested,geometry=geometry):
                    self.assertEqual(probe.editor_frame(geometry,*host,nested),expected)
            for geometry in [(-1,0,464,824),(0,0,465,824),(0,49,464,729),
                             (10,47,444,729),(0,20,464,729),(53,0,360,824)]:
                with self.subTest(nested=nested,geometry=geometry),self.assertRaises(RuntimeError):
                    probe.editor_frame(geometry,*host,nested)
        # Native host dimensions must still accommodate fixed CSD extents;
        # treating the fitted size as a new arbitrary frame is rejected.
        with self.assertRaises(RuntimeError):probe.editor_frame((0,47,464,729),464,824,False)

    def test_independent_focus_setup_release_must_not_deliver_mapped_contact(self):
        probe.require_no_mapped_contact(['AnvilKeymapOverlay: attached app origin=0,0 size=448x704'],'setup')
        probe.require_no_mapped_contact([self.event('UP',0,source='0x2002',tool='MOUSE(3)')],'setup')
        for rows in ([self.event('UP',0)],[self.event('DOWN',1)],
                     ['AnvilKeymap: touch package=org.anvildroid.touchprobe action=1 pointer=0 xy=1,2']):
            with self.subTest(rows=rows),self.assertRaises(RuntimeError):probe.require_no_mapped_contact(rows,'setup')

    def capture_trace(self):
        prefix='[2026-10-18 16:59:53.906][proto] client 0xabc (PID 204) '
        return '\n'.join(prefix+event for event in [
            'rq zwp_relative_pointer_manager_v1@8.get_relative_pointer(new id zwp_relative_pointer_v1@66, wl_pointer@3)',
            'rq zwp_pointer_constraints_v1@9.lock_pointer(new id zwp_locked_pointer_v1@30, wl_surface@53, wl_pointer@3, nil, 1)',
            'ev zwp_locked_pointer_v1@30.locked()',
            'ev zwp_relative_pointer_v1@66.relative_motion(1, 2, 8.000000, 0.000000, 8.000000, 0.000000)',
            'rq zwp_locked_pointer_v1@30.destroy()',
            'ev zwp_locked_pointer_v1@30.unlocked()',
            'ev wl_pointer@3.motion(10, 225.000000, 332.000000)'])

    def test_capture_trace_proves_scoped_grant_relative_suppression_and_restore(self):
        trace=self.capture_trace()
        witness=probe.capture_protocol_witness(trace,53)
        self.assertEqual(witness['client'],'0xabc')
        self.assertEqual(witness['pointer_id'],3)
        self.assertTrue(witness['absolute_motion_suppressed'])
        self.assertTrue(witness['absolute_motion_restored'])
        # Unrelated clients and pointer resources can move during this lock.
        unrelated='[proto] client 0xdef (PID 66) ev wl_pointer@3.motion(1, 2, 3)\n'
        rows=trace.splitlines();rows.insert(4,unrelated.strip())
        self.assertTrue(probe.capture_protocol_witness('\n'.join(rows),53))
        child='[proto] client 0xabc (PID 204) rq wl_subcompositor@2.get_subsurface(new id wl_subsurface@8, wl_surface@53, wl_surface@56)'
        witness=probe.capture_protocol_witness(child+'\n'+trace,56)
        self.assertEqual(witness['locked_surface_ancestry'],[53,56])
        with self.assertRaises(RuntimeError):probe.capture_protocol_witness(trace,56)
        with self.assertRaises(RuntimeError):probe.capture_protocol_witness(child.replace('0xabc','0xdef')+'\n'+trace,56)

    def test_capture_trace_rejects_false_grants_motion_leaks_and_missing_restore(self):
        trace=self.capture_trace()
        cases=[trace.replace('wl_surface@53','wl_surface@54'),
               trace.replace('ev zwp_locked_pointer_v1@30.locked()','ev zwp_locked_pointer_v1@31.locked()'),
               trace.replace('ev zwp_relative_pointer_v1@66.relative_motion','ev zwp_relative_pointer_v1@67.relative_motion'),
               trace.replace('rq zwp_locked_pointer_v1@30.destroy()',
                             'ev wl_pointer@3.motion(1, 2, 3)\n[proto] client 0xabc (PID 204) rq zwp_locked_pointer_v1@30.destroy()'),
               trace.replace('ev wl_pointer@3.motion','ev wl_pointer@4.motion'),
               trace.replace('client 0xabc (PID 204) ev zwp_locked_pointer_v1@30.locked()',
                             'client 0xdef (PID 204) ev zwp_locked_pointer_v1@30.locked()')]
        for bad in cases:
            with self.subTest(trace=bad),self.assertRaises(RuntimeError):probe.capture_protocol_witness(bad,53)

    def test_display_cleanup_is_reverse_order_and_bounded(self):
        events=[]
        class Child:
            def __init__(self,name):self.name=name
            def poll(self):return None
            def terminate(self):events.append(('terminate',self.name))
            def wait(self,timeout):events.append(('wait',self.name,timeout))
        live.close_displays([Child('xvfb'),Child('weston')])
        self.assertEqual(events,[('terminate','weston'),('wait','weston',5),('terminate','xvfb'),('wait','xvfb',5)])

if __name__=='__main__':unittest.main()
