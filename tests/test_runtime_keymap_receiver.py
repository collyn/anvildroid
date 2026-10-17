import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

spec=importlib.util.spec_from_file_location('receiver_existing',Path(__file__).resolve().parents[1]/'services/runtime-existing.py')
existing=importlib.util.module_from_spec(spec);spec.loader.exec_module(existing)

class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.runtime=existing.ExistingRuntime(Path('/store'),Path('/waydroid'))
        self.socket='000000: 00000002 00000000 00010000 0001 01 123 @scrcpy_00000000'

    def test_receiver_runs_as_shell_with_validated_classpath(self):
        android=Mock(side_effect=['','export BOOTCLASSPATH /system/framework/framework.jar:/apex/com.android.art/javalib/core-oj.jar',self.socket])
        process=Mock();process.poll.return_value=None
        with patch.object(self.runtime,'android',android),patch.object(existing.subprocess,'Popen',return_value=process) as start:
            self.runtime.ensure_keymap_receiver()
            args=start.call_args.args[0]
            self.assertEqual(args[args.index('--uid')+1],'2000')
            self.assertEqual(args[args.index('--gid')+1],'2000')
            self.assertIn('--clear-env',args)
            self.assertIn('BOOTCLASSPATH=/system/framework/framework.jar:/apex/com.android.art/javalib/core-oj.jar',args)
            self.assertEqual(args[-2:],['/system/bin/sh','/vendor/bin/anvildroid-keymap.sh'])
            self.runtime.ensure_keymap_receiver()
            start.assert_called_once()
            self.assertEqual(android.call_count,3)

    def test_socket_probe_does_not_steal_bridge_connection(self):
        with patch.object(self.runtime,'android',return_value=self.socket) as android,patch.object(existing.subprocess,'Popen') as start:
            self.runtime.ensure_keymap_receiver()
            android.assert_called_once_with('/system/bin/cat','/proc/net/unix')
            start.assert_not_called()

    def test_guest_classpath_cannot_supply_commands_or_host_paths(self):
        for value in ['export BOOTCLASSPATH /tmp/evil.jar','export BOOTCLASSPATH /system/../data/evil.jar',
                      'export BOOTCLASSPATH /system/a.jar;id','export BOOTCLASSPATH ',
                      'export BOOTCLASSPATH /system/a.jar\nexport BOOTCLASSPATH /system/b.jar']:
            with self.subTest(value=value),patch.object(self.runtime,'android',side_effect=['',value]),patch.object(existing.subprocess,'Popen') as start:
                with self.assertRaisesRegex(RuntimeError,'Invalid Android boot classpath'):
                    self.runtime.ensure_keymap_receiver()
                start.assert_not_called()

if __name__=='__main__':unittest.main()
