#!/usr/bin/env python3
"""Inert checks: reject unsafe input routing and ambiguous surface coordinates."""
import copy
import importlib.util
from pathlib import Path
import unittest

def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

probe=load('probe-android-controls')
live=load('live-android')

class Safety(unittest.TestCase):
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
