#!/usr/bin/env python3
"""Native display recovery contract, with Android command fixtures."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_x11', Path(__file__).with_name('runtime-x11.py'))
x11 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(x11)


class Recovery(unittest.TestCase):
    def invoke(self, saved):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'display'
            if saved is not None:
                marker.write_text(saved)
            script = '''
wm() { printf 'wm %s\\n' "$*" >> calls; }
settings() { printf 'settings %s\\n' "$*" >> calls; }
''' + x11.DISPLAY_RESTORE.replace('f=/data/waydroid_tmp/anvildroid/x11-display-restore', 'f=' + str(marker))
            result = subprocess.run(['sh', '-c', script], cwd=root, capture_output=True, text=True)
            return result.returncode, (root / 'calls').read_text() if (root / 'calls').exists() else '', marker.exists()

    def test_absent_marker_does_not_change_wayland(self):
        self.assertEqual(self.invoke(None), (0, '', False))

    def test_restores_size_scaling_and_rotation(self):
        code, calls, marker = self.invoke('720x1280 1 1 2 disabled\n')
        self.assertEqual(code, 0)
        self.assertFalse(marker)
        for expected in ('wm size 720x1280', 'wm scaling off', 'wm user-rotation free',
                         'wm fixed-to-user-rotation disabled', 'settings put system user_rotation 2'):
            self.assertIn(expected, calls)

    def test_restores_unset_settings(self):
        code, calls, marker = self.invoke('reset null null null default\n')
        self.assertEqual(code, 0)
        self.assertFalse(marker)
        self.assertIn('wm size reset', calls)
        self.assertIn('settings delete global display_scaling_force', calls)
        self.assertIn('settings delete system accelerometer_rotation', calls)

    def test_rejects_corrupt_or_injected_marker(self):
        for saved in ('720x1280 1 1 0 enabled extra\n', '720x99999 0 1 0 default\n',
                      '720x1280 0 1 4 default\n', '$(id) 0 1 0 enabled\n'):
            code, calls, marker = self.invoke(saved)
            self.assertNotEqual(code, 0)
            self.assertEqual(calls, '')
            self.assertTrue(marker)


if __name__ == '__main__':
    unittest.main()
