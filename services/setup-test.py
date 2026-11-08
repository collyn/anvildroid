"""Setup contracts without package installation, host initialization or Android."""
import importlib.util
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load(ROOT / 'scripts/setup-waydroid.py', 'setup_helper_test')


def verify_staged_setup(stage):
    """Consume the Rust-produced stage, persist it, then discard its source."""
    staged = load(stage / 'scripts/setup-waydroid.py', 'staged_setup_contract')
    with tempfile.TemporaryDirectory(prefix='anvil-setup-persisted-') as directory:
        store = Path(directory)
        with patch.object(staged, 'STORE', store), \
                patch.object(staged, 'INIT_LOG', store / 'init.log'), \
                patch.object(staged.subprocess, 'Popen') as spawn:
            staged.spawn_init(42)
        command = spawn.call_args.args[0]
        assert command[3:] == ['--run-init', '--lock-fd', '42']
        assert spawn.call_args.kwargs['pass_fds'] == (42,)
        shutil.rmtree(stage)
        persisted = Path(command[2]).parent
        # Import real consumer modules after the original stage is gone. -I
        # prevents checkout/PYTHONPATH from supplying a missing dependency.
        probe = '''import importlib.util, pathlib, sys
root = pathlib.Path(sys.argv[1])
for name in ('runtime-provision', 'setup-waydroid'):
    spec = importlib.util.spec_from_file_location(name, root / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
print('Persisted setup consumers import without the original stage: PASS')
'''
        subprocess.run([sys.executable, '-I', '-B', '-c', probe, str(persisted)],
                       cwd=directory, check=True, timeout=15)


class SetupTests(unittest.TestCase):
    def test_native_package_includes_detached_setup_dependencies(self):
        script = (ROOT / 'scripts/stage-package.sh').read_text()
        match = re.search(r'for f in ([^;]+); do install[^\n]+/services/\$f', script)
        self.assertIsNotNone(match)
        self.assertFalse(set(helper.SETUP_MODULES) - set(match[1].split()))

    def test_host_tools_do_not_initialize_or_download_android(self):
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper, 'ensure_host_packages') as packages, \
                patch.object(helper, 'ensure_managed_support') as support, \
                patch.object(helper.subprocess, 'Popen') as process, \
                patch.object(helper, 'write_status') as status:
            helper.run_init()
        packages.assert_called_once_with()
        support.assert_called_once_with()
        process.assert_not_called()
        self.assertEqual(status.call_args.args[1], 'done')

    def test_template_repair_is_only_done_when_needed(self):
        for available in (True, False):
            with self.subTest(available=available):
                provision = SimpleNamespace(managed_support_available=Mock(side_effect=[available, True]))
                spec = SimpleNamespace(loader=Mock())
                with patch.object(helper.importlib.util, 'spec_from_file_location', return_value=spec), \
                        patch.object(helper.importlib.util, 'module_from_spec', return_value=provision), \
                        patch.object(helper.Path, 'read_text', return_value='ID=ubuntu\n'), \
                        patch.object(helper.shutil, 'which', return_value='/usr/bin/apt-get'), \
                        patch.object(helper, 'write_status'), patch.object(helper, 'apt') as apt:
                    helper.ensure_managed_support()
                if available:
                    apt.assert_not_called()
                else:
                    self.assertEqual(apt.call_args.args, ('install', '-y', '--no-install-recommends', '--reinstall', 'waydroid'))

    def test_missing_dependency_never_starts_a_partial_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(helper, 'STORE', root), \
                    patch.object(helper, 'HOST_MODULE', root / 'missing/runtime-host.py'), \
                    patch.object(helper.subprocess, 'Popen') as spawn:
                with self.assertRaises(FileNotFoundError):
                    helper.spawn_init(42)
            spawn.assert_not_called()
            self.assertEqual(list((root / 'setup').iterdir()), [])

    def test_system_waydroid_options_are_rejected_before_touching_host(self):
        for argument in (['--host-only'], ['--image-id', 'a' * 64], ['--flavor', 'GAPPS']):
            with self.subTest(argument=argument), patch.object(sys, 'argv', ['setup-waydroid.py', *argument]), \
                    patch.object(helper, 'ensure_directory') as directory:
                with self.assertRaises(SystemExit) as error:
                    helper.main()
                self.assertEqual(error.exception.code, 2)
                directory.assert_not_called()

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--stage':
        verify_staged_setup(Path(sys.argv[2]))
    else:
        unittest.main()
