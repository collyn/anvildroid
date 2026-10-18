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
