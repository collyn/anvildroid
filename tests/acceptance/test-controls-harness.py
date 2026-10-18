#!/usr/bin/env python3
"""Inert checks: reject unsafe input routing and ambiguous surface coordinates."""
import copy
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

probe=load('probe-android-controls')
live=load('live-android')

class Safety(unittest.TestCase):
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
        for scene in [self.scene()+self.scene(),self.scene(suffix='.evil'),
                      self.scene(extra='[view is not mapped!]'),self.scene('(-1, 0) -> (448, 703)'),
                      self.scene('(0, 0) -> (1700, 1000)'),self.scene('(0, 0) -> (200, 300)')]:
            with self.subTest(scene=scene),self.assertRaises(RuntimeError):probe.scene_rectangle(scene,'r-test')

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

    def test_editor_click_targets_follow_compact_and_full_layout(self):
        compact=probe.editor_targets(448,670)
        self.assertEqual(compact['add'],(54,640))
        self.assertEqual(compact['save'],(306,640))
        self.assertEqual(compact['cancel'],(390,640))
        self.assertEqual(compact['bind_key'],(56,47))
        portrait=probe.editor_targets(464,824)
        self.assertEqual(portrait['add'],(55,794))
        self.assertEqual(portrait['save'],(316,794))
        self.assertEqual(portrait['cancel'],(403,794))
        self.assertEqual(portrait['bind_key'],(58,47))
        full=probe.editor_targets(900,700)
        self.assertEqual(full['add'],(768,148))
        self.assertEqual(full['bind_key'],(715,383))
        self.assertEqual(full['save'],(768,652))
        self.assertEqual(full['cancel'],(870,30))
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
