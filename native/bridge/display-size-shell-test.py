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
    def test_task_snapshot_viewport_is_exact_active_default_and_drops_stale_state(self):
        functions = SOURCE[SOURCE.index('publish_task_snapshot()'):SOURCE.index('# Fresh capability token')]
        viewport = ("DisplayViewport{type=INTERNAL, valid=true, isActive=true, displayId=0, "
                    "uniqueId='local:0', physicalPort=0, orientation=1, "
                    "logicalFrame=Rect(0, 0 - 2064, 3264), physicalFrame=Rect(0, 0 - 2064, 3264), "
                    "deviceWidth=2064, deviceHeight=3264}")
        external = viewport.replace('type=INTERNAL', 'type=EXTERNAL').replace('displayId=0', 'displayId=2').replace('orientation=1', 'orientation=3')
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / 'fixture').write_text('taskId=26 bounds=[0,32][448,736]\n')
            script = '''
cmd() { cat fixture; }
dumpsys() { [ ! -f fail ] || return 1; cat display; }
snapshot_published=0 last_snapshot=
''' + functions + '\npublish_task_snapshot\n'
            for dump, expected in [
                ('mViewports=[' + viewport + ', ' + external + ']', '# anvil-viewport 1 2064 3264 2064 3264\n'),
                ('mViewports=[' + external + ', ' + viewport + ']', '# anvil-viewport 1 2064 3264 2064 3264\n'),
                (viewport.replace('valid=true', 'valid=false'), ''),
                (viewport.replace('isActive=true', 'isActive=false'), ''),
                ('mViewports=[' + viewport + ', ' + viewport + ']', ''),
            ]:
                (root / 'display').write_text(dump + '\n')
                result = subprocess.run(['sh', '-c', script], cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / 'tasks').read_text(), expected + 'taskId=26 bounds=[0,32][448,736]\n')
            (root / 'display').write_text('mViewports=[' + viewport + ']\n')
            # Same helper lifetime: failed read clears the previous header.
            result = subprocess.run(['sh', '-c', script + 'touch fail\npublish_task_snapshot\n'], cwd=root,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'tasks').read_text(), 'taskId=26 bounds=[0,32][448,736]\n')

    def test_task_snapshot_publishes_changes_atomically_and_retries_failure(self):
        functions = SOURCE[SOURCE.index('publish_task_snapshot()'):SOURCE.index('# Fresh capability token')]
        script = '''
cmd() { [ ! -f fail ] || return 1; cat fixture; }
snapshot_published=0 last_snapshot=
'''
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / 'fixture').write_text('taskId=12 bounds=[0,0][720,1280]\n')
            script += functions + '''
publish_task_snapshot || exit 1
ln tasks retained
publish_task_snapshot || exit 2
[ tasks -ef retained ] || exit 3
echo changed > fixture
touch fail
if publish_task_snapshot; then exit 4; fi
[ tasks -ef retained ] || exit 5
rm fail
publish_task_snapshot || exit 6
[ "$(cat tasks)" = changed ] || exit 7
[ ! tasks -ef retained ] || exit 8
rm tasks
publish_task_snapshot || exit 9
[ "$(cat tasks)" = changed ] || exit 10
# An empty first result must replace a snapshot left by a prior helper.
snapshot_published=0 last_snapshot=
: > fixture
publish_task_snapshot || exit 11
[ "$(cat tasks)" = '' ] || exit 12
'''
            result = subprocess.run(['sh', '-c', script], cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_resize_keeps_android16_origin_and_exact_dimensions(self):
        functions = SOURCE[SOURCE.index('resize_app_task()'):SOURCE.index('# Fresh capability token')]
        snapshot = ('RootTask id=12 bounds=[609,134][1374,1055]\n'
                    '  taskId=12: com.android.settings/.Settings bounds=[609,134][1374,1055] userId=0\n')
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory, 'tasks').write_text(snapshot)
            script = 'getprop() { echo "$SDK"; }; cmd() { echo "$*"; };\n' + functions
            for sdk, expected in [('36', 'activity task resize 12 609 134 1889 854'),
                                  ('33', 'activity task resize 12 0 0 1280 720')]:
                result = subprocess.run(['sh', '-c', script + '\nresize_app_task 12 1280 720'],
                                        cwd=directory, env={'PATH': '/usr/bin:/bin', 'SDK': sdk},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
            # Never apply another task's coordinates or guessed (0,0) bounds.
            result = subprocess.run(['sh', '-c', script + '\nresize_app_task 99 1280 720'],
                                    cwd=directory, env={'PATH': '/usr/bin:/bin', 'SDK': '36'},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')

    def run_helper(self, command, full='', scaled=False, noscale=False, android16=False):
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
getprop() { case "$1" in waydroid.anvil_full_ui) printf '%s' "$FULL";; waydroid.active_apps) printf '%s' "$ACTIVE";; waydroid.anvil_single_display) printf '%s' "$SINGLE";; esac; }
dumpsys() { if [ "$1" = window ]; then printf '%s' "$NOSCALE"; return; fi; echo 'mViewports=[DisplayViewport{type=INTERNAL, displayId=0, orientation=0, logicalFrame=Rect(0, 0 - 720, 1280), physicalFrame=Rect(280, 0 - 1000, 1280), deviceWidth=1280, deviceHeight=1280}]'; }
'''
            if scaled:
                script = script.replace('Rect(280, 0 - 1000, 1280)', 'Rect(128, 0 - 1152, 1280)')
            if android16:
                script = script.replace('orientation=0, logicalFrame=',
                                        'orientation=0, densityDpi=180, xDpi=180.0, yDpi=180.0, logicalFrame=')
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

    def test_android_16_density_fields_do_not_hide_projection(self):
        result, files = self.run_helper('\nresize_display 720 1280 canvas\n', android16=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files['display-state'], '720 1280 1280 1280 280 0 720 1280\n')

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

    def test_app_windows_clear_persisted_single_display_override(self):
        result, files = self.run_helper('\necho stale > display-state\nbootstrap_display\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files['calls'], 'size reset\nscaling auto\n')
        self.assertNotIn('display-state', files)

    def test_single_display_bootstrap_keeps_canvas(self):
        result, files = self.run_helper('\nSINGLE=true\nresize_display() { echo "$*" > request; }\nbootstrap_display\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files['request'], '1280 720 canvas\n')

    def test_bootstrap_preserves_active_full_ui(self):
        result, files = self.run_helper('\nbootstrap_display\n', full='active')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('calls', files)

if __name__ == '__main__':
    unittest.main()
