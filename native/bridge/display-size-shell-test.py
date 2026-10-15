"""Exercise the real shell-UID display request parser with Android command fixtures."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'native/anvildroid-tasks.sh').read_text()
FUNCTIONS = SOURCE.split('# Only this shell-UID service')[1].split('# Fresh capability token')[0]
FUNCTIONS = '# Only this shell-UID service' + FUNCTIONS

class DisplaySizeTests(unittest.TestCase):
    def run_helper(self, command, full='', scaled=False, noscale=False):
        with tempfile.TemporaryDirectory() as directory:
            script = '''
wm() {
    printf '%s\\n' "$*" >> calls
    case "$*" in
        size) printf 'Physical size: 1280x1280\\nOverride size: 1000x600\\n';;
        fixed-to-user-rotation) printf 'disabled\\n';;
    esac
}
settings() { case "$*" in *accelerometer_rotation) echo 1;; *) echo 2;; esac; }
getprop() { case "$1" in waydroid.anvil_full_ui) printf '%s' "$FULL";; waydroid.active_apps) printf '%s' "$ACTIVE";; esac; }
dumpsys() { if [ "$1" = window ]; then printf '%s' "$NOSCALE"; return; fi; echo 'mViewports=[DisplayViewport{type=INTERNAL, displayId=0, orientation=0, logicalFrame=Rect(0, 0 - 720, 1280), physicalFrame=Rect(280, 0 - 1000, 1280), deviceWidth=1280, deviceHeight=1280}]'; }
'''
            if scaled:
                script = script.replace('Rect(280, 0 - 1000, 1280)', 'Rect(128, 0 - 1152, 1280)')
            result = subprocess.run(['sh', '-c', script + FUNCTIONS + command], cwd=directory,
                                    env={'PATH': '/usr/bin:/bin', 'FULL': full, 'NOSCALE': 'init=1280x1280 base=1000x600 noscale' if noscale else ''}, text=True, capture_output=True)
            files = {p.name: p.read_text() for p in pathlib.Path(directory).iterdir()}
            return result, files

    def test_portrait_uses_acknowledged_projection(self):
        result, files = self.run_helper('\nresize_display 720 1280 canvas\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files['display-state'], '720 1280 1280 1280 280 0 720 1280\n')
        self.assertIn('size 720x1280', files['calls'])
        self.assertIn('scaling off', files['calls'])

    def test_android_15_scaled_projection_still_acknowledges(self):
        result, files = self.run_helper('\nresize_display 720 1280 canvas\n', scaled=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files['display-state'], '720 1280 1280 1280 128 0 1024 1280\n')

    def test_default_full_ui_selector_is_accepted(self):
        result, files = self.run_helper('\nACTIVE=Waydroid\nresize_display 720 1280 full\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('display-state', files)

    def test_full_ui_saves_existing_configuration(self):
        result, files = self.run_helper('\nresize_display 720 1280 full\n', 'active')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files['display-restore'], '1000x600 1 2 disabled \n')

    def test_full_ui_saves_actual_scaling_mode(self):
        result, files = self.run_helper('\nresize_display 720 1280 full\n', 'active', noscale=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files['display-restore'], '1000x600 1 2 disabled 1\n')

    def test_leaving_full_ui_restores_previous_display(self):
        restore = SOURCE[SOURCE.index('    if [ "$snapshot_ticks" -eq 0 ] && [ "$display_full" -eq 1 ]'):SOURCE.index('    # Check resize requests')]
        result, files = self.run_helper('\nresize_display 720 1280 full\nFULL=\nsnapshot_ticks=0\n' + restore, 'active')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('size 1000x600', files['calls'])
        self.assertIn('user-rotation free', files['calls'])
        self.assertIn('fixed-to-user-rotation disabled', files['calls'])
        self.assertNotIn('display-state', files)
        self.assertNotIn('display-restore', files)

    def test_rejects_invalid_sizes_and_inactive_full_ui(self):
        for command in ['resize_display 0 720 canvas', 'resize_display 9000 720 canvas',
                        'resize_display 720 1280 full', 'resize_display "720;id" 1280 canvas']:
            result, files = self.run_helper('\n' + command + '\n')
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('calls', files)
            self.assertNotIn('display-state', files)

if __name__ == '__main__':
    unittest.main()
