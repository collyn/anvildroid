#!/usr/bin/env python3
"""Inert safety/oracle checks; never connect to a display or Android guest."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

spec=importlib.util.spec_from_file_location('detached',Path(__file__).with_name('detached-editor.py'))
detached=importlib.util.module_from_spec(spec);spec.loader.exec_module(detached)


class DetachedSafety(unittest.TestCase):
    def test_worker_guard_runs_before_any_input_or_guest_command(self):
        guard=Mock(side_effect=RuntimeError('worker replaced'));run=Mock()
        with self.assertRaisesRegex(RuntimeError,'worker replaced'):
            detached.exercise({'v3_guard':guard,'run':run})
        guard.assert_called_once_with();run.assert_not_called()

    def test_missing_wm_focus_accepts_only_exact_nonzero_xids(self):
        self.assertEqual(detached.wm_focus('1048578'),['windowfocus','--sync','1048578'])
        self.assertEqual(detached.wm_focus('0x100002'),['windowfocus','--sync','0x100002'])
        for window in ('root','0','0x0','--active','42 43','42;false',None):
            with self.subTest(window=window),self.assertRaises(RuntimeError):detached.wm_focus(window)

    def test_canvas_focus_uses_visible_csd_below_weston_shell_panel(self):
        self.assertEqual(detached.visible_canvas_header((0,40,448,744)),(80,36))
        self.assertEqual(detached.visible_canvas_header((100,34,548,738)),(180,33))
        for canvas in ((0,32,448,736),(0,33,448,737),(0,40,100,744)):
            with self.subTest(canvas=canvas),self.assertRaisesRegex(RuntimeError,'safe visible header'):
                detached.visible_canvas_header(canvas)

    def test_disappearing_xid_requires_fresh_scoped_absence(self):
        error=subprocess.CalledProcessError(1,['xdotool','getwindowgeometry','42'],stderr=b'X Error: BadWindow (invalid Window parameter)')
        find=Mock(side_effect=[['42'],[]]);inspect=Mock(side_effect=error)
        self.assertEqual(detached.native_panel_snapshot(find,inspect,missing=True),(None,None))
        self.assertEqual(find.call_count,2);inspect.assert_called_once_with('42')
        find=Mock(side_effect=[['42'],['43']]);inspect=Mock(side_effect=[error,(600,64,900,801)])
        self.assertEqual(detached.native_panel_snapshot(find,inspect,missing=True),('43',(600,64,900,801)))
        self.assertEqual(inspect.call_args_list[-1].args,('43',))

    def test_disappearing_xid_does_not_hide_presence_geometry_or_owner_errors(self):
        error=subprocess.CalledProcessError(1,['xdotool'],stderr=b'BadWindow')
        find=Mock(return_value=['42'])
        with self.assertRaises(subprocess.CalledProcessError):
            detached.native_panel_snapshot(find,Mock(side_effect=error),missing=False)
        find.assert_called_once_with()
        with self.assertRaises(subprocess.CalledProcessError):
            detached.native_panel_snapshot(Mock(side_effect=[['42'],['42']]),Mock(side_effect=error),missing=True)
        with self.assertRaisesRegex(RuntimeError,'exact scoped'):
            detached.native_panel_snapshot(Mock(side_effect=[['42'],['42','43']]),Mock(side_effect=error),missing=True)
        for error in (subprocess.CalledProcessError(1,['xdotool'],stderr=b'BadDrawable'),
                      RuntimeError('Panel WM_CLASS owner mismatch'),KeyError('WIDTH')):
            find=Mock(return_value=['42'])
            with self.subTest(error=error),self.assertRaises(type(error)):
                detached.native_panel_snapshot(find,Mock(side_effect=error),missing=True)
            find.assert_called_once_with()

    def test_async_marker_cleanup_rechecks_guard_and_waits_for_empty(self):
        guard=Mock();read=Mock(side_effect=['generation package 7 123\n','generation package 7 123\n',''])
        with patch.object(detached.time,'sleep') as sleep:
            self.assertEqual(detached.settled_marker(read,guard,False),'')
        self.assertEqual(guard.call_count,3);self.assertEqual(sleep.call_count,2)
        guard=Mock();read=Mock(return_value='generation package 7 123\n')
        self.assertEqual(detached.settled_marker(read,guard,True),'generation package 7 123\n')
        read.assert_called_once_with();guard.assert_called_once_with()

    def test_async_marker_cleanup_refuses_orphan_and_worker_replacement(self):
        read=Mock(return_value='still owned\n')
        with patch.object(detached.time,'monotonic',side_effect=[0,6]),self.assertRaisesRegex(RuntimeError,'Orphan'):
            detached.settled_marker(read,Mock(),False)
        read.assert_called_once_with()
        read=Mock()
        with self.assertRaisesRegex(RuntimeError,'worker changed'):
            detached.settled_marker(read,Mock(side_effect=RuntimeError('worker changed')),False)
        read.assert_not_called()

    def test_panel_scope_rejects_injection_and_ends_with_distinct_role(self):
        self.assertEqual(detached.panel_identity('r-ab12','org.anvildroid.touchprobe'),
                         'waydroid.anvildroid.r-ab12.org.anvildroid.touchprobe.keymap-editor')
        for runtime,package in [('r-fixture;false','org.test'),('personal','org.test'),
                                ('r-fixture','org.test;false'),('r-fixture','')]:
            with self.subTest(runtime=runtime,package=package),self.assertRaises(RuntimeError):
                detached.panel_identity(runtime,package)

    def test_scene_exact_owner_ignores_android_and_other_runtime(self):
        identity=detached.panel_identity('r-a','org.test')
        def view(owner,x=600,y=64,w=300,h=737):
            return f'\n\tView 1 role xdg_toplevel of {owner},\nposition: ({x}, {y}) -> ({x+w}, {y+h})\n'
        scene=view('waydroid.anvildroid.r-a.org.test')+view(identity)+view(identity.replace('r-a','r-b'))
        self.assertEqual(detached.scene_panel(scene,identity),(600,64,900,801))
        with self.assertRaisesRegex(RuntimeError,'unambiguous'):
            detached.scene_panel(scene+view(identity),identity)
        with self.assertRaisesRegex(RuntimeError,'unambiguous'):
            detached.scene_panel(view(identity+'.other'),identity)

    def test_scene_unmapped_missing_and_invalid_geometry(self):
        identity=detached.panel_identity('r-a','org.test')
        base=f'\n\tView 1 role xdg_toplevel of {identity},\n'
        self.assertIsNone(detached.scene_panel(base+'[view is not mapped!]',identity,missing=True))
        with self.assertRaisesRegex(RuntimeError,'unavailable'):detached.scene_panel(base,identity)
        with self.assertRaisesRegex(RuntimeError,'too small'):
            detached.scene_panel(base+'position: (0, 0) -> (299, 737)',identity)

    def test_drag_destination_is_visible_and_disjoint(self):
        for canvas in [(5,32,453,736),(900,32,1348,736)]:
            x,y=detached.destination(canvas,300,737)
            panel=(x,y,x+300,y+737)
            self.assertTrue(detached.outside(panel,canvas))
            self.assertGreaterEqual(x,8);self.assertLessEqual(x+300,1592)
            self.assertLessEqual(y+737,992)
        with self.assertRaisesRegex(RuntimeError,'No visible'):
            detached.destination((0,32,1600,1000),300,737)
        self.assertFalse(detached.outside((400,64,700,801),(5,32,453,736)))

    def test_canvas_placement_avoids_existing_markers(self):
        joystick=[0]*31;joystick[1:3]=[16000,40000];joystick[4]=1;joystick[11]=9000
        point,end=detached.canvas_points([joystick],448,704)
        center=(16000*447/65535,40000*703/65535)
        for candidate in (point,end):
            self.assertGreater((candidate[0]-center[0])**2+(candidate[1]-center[1])**2,60**2)
        self.assertEqual((end[0]-point[0],end[1]-point[1]),(20,34))

    def test_scoped_wm_fixture_compiles_without_opening_display(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'fixture.c';source.write_text(detached.MOVE_FIXTURE)
            subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsyntax-only',str(source)],
                           check=True,capture_output=True,timeout=20)
        self.assertIn('e.xclient.window==target',detached.MOVE_FIXTURE)
        self.assertIn('strcmp(cls.res_name,argv[2])',detached.MOVE_FIXTURE)
        self.assertNotIn('SubstructureRedirectMask',detached.MOVE_FIXTURE)


if __name__=='__main__':unittest.main()
