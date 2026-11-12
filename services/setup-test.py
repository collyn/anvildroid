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

    def test_missing_templates_never_reinstall_waydroid(self):
        for available in (True, False):
            with self.subTest(available=available):
                provision = SimpleNamespace(managed_support_available=Mock(return_value=available))
                spec = SimpleNamespace(loader=Mock())
                with patch.object(helper.importlib.util, 'spec_from_file_location', return_value=spec), \
                        patch.object(helper.importlib.util, 'module_from_spec', return_value=provision), \
                        patch.object(helper, 'apt') as apt, patch.object(helper, 'run_logged') as run:
                    if available:
                        helper.ensure_managed_support()
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'Reinstall AnvilDroid'):
                            helper.ensure_managed_support()
                apt.assert_not_called()
                run.assert_not_called()

    def test_package_manager_detection(self):
        cases = (({'ID': 'ubuntu', 'ID_LIKE': 'debian'}, 'apt-get', False, 'apt'),
                 ({'ID': 'fedora'}, 'dnf', False, 'dnf'),
                 ({'ID': 'nobara', 'ID_LIKE': 'rhel centos fedora'}, 'dnf', False, 'dnf'),
                 ({'ID': 'fedora'}, 'dnf', True, None),
                 ({'ID': 'bazzite', 'ID_LIKE': 'fedora'}, 'dnf', True, None),
                 ({'ID': 'opensuse-tumbleweed', 'ID_LIKE': 'opensuse suse'}, 'zypper', False, 'zypper'),
                 ({'ID': 'arch'}, 'pacman', False, 'pacman'),
                 ({'ID': 'cachyos', 'ID_LIKE': 'arch'}, 'pacman', False, 'pacman'),
                 ({'ID': 'gentoo'}, 'emerge', False, None))
        for release, tool, atomic, expected in cases:
            with self.subTest(release=release, atomic=atomic), \
                    patch.object(helper, 'OSTREE_MARKER', Mock(exists=Mock(return_value=atomic))), \
                    patch.object(helper.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name if name == tool else None):
                self.assertEqual(helper.package_manager(release), (expected, atomic))

    def test_fedora_installs_mapped_packages_without_release_numbers(self):
        commands = {'lxc-start': 'lxc', 'mount': 'mount', 'ip': 'iproute2', 'java': 'default-jre-headless', 'aapt': 'aapt'}
        with patch.object(helper.host, 'COMMAND_PACKAGES', commands), \
                patch.object(helper.host, 'missing_commands', side_effect=[list(commands), list(commands), []]), \
                patch.object(helper, 'package_manager', return_value=('dnf', False)), \
                patch.object(helper, 'install_waydroid', return_value=''), \
                patch.object(helper, 'write_status'), patch.object(helper, 'run_logged', return_value=True) as run:
            helper.ensure_host_packages()
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ['dnf', '-y', 'install'])
        self.assertEqual(sorted(command[3:]), ['iproute', 'java-headless', 'lxc', 'util-linux-core'])
        self.assertFalse(any(char.isdigit() for char in ' '.join(command)))

    def test_waydroid_is_optional_and_never_fails_setup(self):
        with patch.object(helper.shutil, 'which', return_value=None), patch.object(helper, 'write_status'), \
                patch.object(helper, 'INIT_LOG', Path(tempfile.mkdtemp()) / 'init.log'), \
                patch.object(helper, 'run_logged', return_value=False) as run:
            note = helper.install_waydroid('dnf', False)
        self.assertEqual(run.call_args.args[0], ['dnf', '-y', 'install', 'waydroid'])
        self.assertIn('optional', note)
        with patch.object(helper.shutil, 'which', return_value=None):
            self.assertIn('rpm-ostree install waydroid', helper.install_waydroid(None, True))

    def test_atomic_host_reports_rpm_ostree_for_missing_tools(self):
        with patch.object(helper.host, 'missing_commands', return_value=['lxc-start']), \
                patch.object(helper, 'package_manager', return_value=(None, True)):
            with self.assertRaisesRegex(RuntimeError, 'rpm-ostree install'):
                helper.ensure_host_packages()

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

class SelinuxPolicyTests(unittest.TestCase):
    policy = load(ROOT / 'scripts/install-runtime-selinux.py', 'selinux_policy_test')

    def test_hosts_without_selinux_are_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock()
            self.assertIn('not active', self.policy.install(Path(directory), '/usr/sbin/semodule', run))
            run.assert_not_called()

    def test_selinux_hosts_load_only_the_binder_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'enforce').write_text('1')
            loaded = []
            def run(command, **kwargs):
                loaded.append(Path(command[2]).read_text())
                self.assertEqual(command[:2], ['/usr/sbin/semodule', '-i'])
                self.assertTrue(command[2].endswith('/anvildroid.cil'))
            self.policy.install(Path(directory), '/usr/sbin/semodule', run)
        rules = [line.strip() for line in loaded[0].splitlines() if 'allow' in line]
        self.assertEqual(len(rules), 2)
        self.assertTrue(all(' self (binder (' in rule for rule in rules))
        self.assertTrue(all(line.startswith(('(optional', '    (allow', ';')) for line in loaded[0].splitlines()))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--stage':
        verify_staged_setup(Path(sys.argv[2]))
    else:
        unittest.main()
