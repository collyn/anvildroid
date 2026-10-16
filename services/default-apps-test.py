import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'native/anvildroid-apps.sh'


class CompanionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='anvil-app-companion-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        bindir = self.root / 'bin'
        bindir.mkdir()
        for name in ('am', 'dumpsys'):
            command = bindir / name
            command.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$TRACE"\necho Success\n')
            command.chmod(0o755)
        pm = bindir / 'pm'
        pm.write_text('''#!/usr/bin/python3
import os, sys
from pathlib import Path
root = Path(os.environ['FIXTURE'])
args = sys.argv[1:]
with (root/'trace').open('a') as trace: trace.write('pm ' + ' '.join(args) + '\\n')
mode = (root/'mode').read_text().strip()
if args[:2] == ['list', 'packages']:
    if mode == 'query-fail': sys.exit(1)
    if '-3' not in args or mode != 'system':
        if (root/'installed').exists():
            print('package:com.example.test' + ('.other' if mode == 'similar' else ''))
elif args[:1] == ['uninstall']:
    if mode == 'failure': print('Failure [DELETE_FAILED_INTERNAL_ERROR]')
    else:
        if mode != 'sticky': (root/'installed').unlink(missing_ok=True)
        print('Success')
elif args[:1] == ['install']: print('Success')
else: sys.exit(2)
''')
        pm.chmod(0o755)
        (self.root/'mode').write_text('normal')
        (self.root/'installed').touch()
        script = SCRIPT.read_text().replace('base=/data/waydroid_tmp/anvildroid', 'base=' + str(self.root))
        self.process = subprocess.Popen(['/bin/sh'], stdin=subprocess.PIPE,
            env={**os.environ, 'PATH': str(bindir) + ':' + os.environ['PATH'],
                 'TRACE': str(self.root/'trace'), 'FIXTURE': str(self.root)})
        self.process.stdin.write(script.encode())
        self.process.stdin.close()
        self.addCleanup(self.stop)
        for _ in range(100):
            if (self.root/'apps').is_dir(): break
            time.sleep(.01)
        self.counter = 0

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=3)

    def job(self, action, package):
        self.counter += 1
        name = 'job-' + str(self.counter)
        temporary = self.root/'apps'/(name+'.staging')
        temporary.mkdir()
        (temporary/'action').write_text(action+'\n')
        (temporary/'package').write_text(package+'\n')
        (temporary/'input.apk').write_bytes(b'fixture')
        temporary.rename(self.root/'apps'/(name+'.ready'))
        done = self.root/'apps'/(name+'.working')
        for _ in range(200):
            if (done/'status').exists():
                return ((done/'status').read_text().strip(), (done/'output').read_text())
            time.sleep(.01)
        self.fail('companion timeout')

    def trace(self):
        path = self.root/'trace'
        return path.read_text() if path.exists() else ''

    def test_queue_validation_and_protected_components(self):
        for action, package in [('force-stop', 'com.android.settings'),
                                ('uninstall', 'cu.axel.smartdock'),
                                ('uninstall', 'org.anvildroid.ime'),
                                ('details', 'com.test;id'), ('shell', 'com.example.test')]:
            self.assertEqual(self.job(action, package)[0], '2')
        self.assertEqual(self.trace(), '')
        for action in ('install', 'force-stop', 'details'):
            self.assertEqual(self.job(action, 'com.example.test')[0], '0')
        self.assertIn('install -r --user 0', self.trace())
        self.assertIn('force-stop --user 0 com.example.test', self.trace())

    def test_uninstall_confirms_third_party_and_actual_removal(self):
        status, output = self.job('uninstall', 'com.example.test')
        self.assertEqual((status, output.strip()), ('0', 'Success'))
        self.assertFalse((self.root/'installed').exists())
        self.assertIn('pm uninstall --user 0 com.example.test\n', self.trace())

    def test_system_and_partial_package_matches_never_uninstall(self):
        for mode in ('system', 'similar', 'query-fail'):
            with self.subTest(mode=mode):
                (self.root/'mode').write_text(mode)
                self.assertNotEqual(self.job('uninstall', 'com.example.test')[0], '0')
                self.assertNotIn('pm uninstall', self.trace())
                self.assertTrue((self.root/'installed').exists())

    def test_android_failure_or_retained_package_cannot_report_success(self):
        for mode, message in [('failure', 'DELETE_FAILED_INTERNAL_ERROR'),
                              ('sticky', 'still reports the app installed')]:
            with self.subTest(mode=mode):
                (self.root/'mode').write_text(mode)
                status, output = self.job('uninstall', 'com.example.test')
                self.assertNotEqual(status, '0')
                self.assertIn(message, output)
                self.assertTrue((self.root/'installed').exists())


if __name__ == '__main__':
    unittest.main()
