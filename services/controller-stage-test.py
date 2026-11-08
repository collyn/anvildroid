#!/usr/bin/env python3
"""Controller payload contract: bundle -> GUI stage -> installer.

  --make-fixture DIR   write a disposable bundle containing every manifest path
  --stage DIR          install from a GUI-produced stage into a temporary destination

Without arguments, runs unit tests for the payload manifest checks. Nothing here
touches systemd, AppArmor, Waydroid or Android.
"""
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_fixture(root):
    """Bundle fixture: real Python sources, inert binaries with valid headers."""
    def write(relative, data, mode=0o644):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
    for path in (ROOT / 'services').glob('*.py'):
        write('services/' + path.name, path.read_bytes())
    for name in ('controller-payload.txt', 'start-runtime-controller.py', 'install-runtime-controller.py', 'apk-align.py'):
        write('scripts/' + name, (ROOT / 'scripts' / name).read_bytes(), 0o755)
    write('scripts/install-runtime-apparmor.py', b'def install(): raise AssertionError("host AppArmor must not be touched")\n')
    elf = bytearray(64); elf[:6] = b'\x7fELF\x02\x01'; elf[18:20] = b'\x3e\x00'
    for name in ('libanvildroid-runtime-window.so', 'libanvildroid-path-guard.so'):
        write('target/native/' + name, bytes(elf), 0o755)
    write('target/native/overlay-key.p12', b'fixture key')
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('classes.dex', b'dex\nfixture')
    for name in ('target/ime/AnvilDroidIme.apk', 'target/shutdown/anvildroid-shutdown.jar',
                 'target/input-diagnostic/scrcpy-server-cancel.jar'):
        write(name, archive.getvalue())
    for name in ('anvildroid-apps.rc', 'anvildroid-tasks.rc', 'anvildroid-apps.sh', 'anvildroid-tasks.sh',
                 'anvildroid-keymap.rc', 'anvildroid-keymap.sh'):
        write('native/' + name, b'fixture ' + name.encode() + b'\n')
    write('native/menu-font-OFL.txt', b'Fixture font license\n')
    write('native/overlay/AndroidManifest.xml', b'<manifest/>\n')
    write('native/overlay/res/values/strings.xml', b'<resources/>\n')


def install_from(stage):
    """Run the staged installer exactly as start-runtime-controller.py would."""
    installer = load(stage / 'scripts/install-runtime-controller.py', 'staged_installer')
    policy = type('Policy', (), {'install': staticmethod(lambda: 'fixture AppArmor: unchanged')})
    with tempfile.TemporaryDirectory(prefix='anvil-controller-dest-') as directory:
        destination = Path(directory) / 'controller'
        with patch.object(installer, 'trusted_directory', side_effect=lambda path, strict=True: path.mkdir(parents=True, exist_ok=True)), \
                patch.object(installer, 'load_apparmor_installer', return_value=policy):
            installer.install_controller_files(stage, destination=destination)
        for name in installer.MODULES:
            assert (destination / name).read_bytes() == (stage / 'services' / name).read_bytes(), name
        for name in ('libanvildroid-window.so', 'libanvildroid-path-guard.so', 'AnvilDroidIme.apk',
                     'anvildroid-shutdown.jar', 'scrcpy-server-cancel.jar', 'provision/anvildroid-keymap.sh',
                     'provision/menu-font-OFL.txt', 'provision/overlay/res/values/strings.xml'):
            assert (destination / name).is_file(), f'Not installed: {name}'
    print('Controller stage installs without the original bundle: PASS')


class PayloadManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='anvil-payload-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        make_fixture(self.root)
        self.installer = load(ROOT / 'scripts/install-runtime-controller.py', 'payload_installer')

    def test_complete_fixture_passes_and_optional_x11_may_be_absent(self):
        self.installer.check_payload(self.root)

    def test_each_required_path_is_named_before_installing(self):
        for relative, tree, optional in self.installer.payload_entries(self.root):
            if optional or relative == 'scripts/controller-payload.txt':
                continue
            with self.subTest(relative=relative):
                backup = self.root.parent / (self.root.name + '-backup')
                shutil.copytree(self.root, backup)
                try:
                    path = self.root / relative
                    shutil.rmtree(path) if tree else path.unlink()
                    with patch.object(self.installer, 'replace', side_effect=AssertionError('wrote before validation')), \
                            patch.object(self.installer, 'load_apparmor_installer', side_effect=AssertionError('policy before validation')):
                        with self.assertRaisesRegex(RuntimeError, 'missing: .*' + relative.split('/')[-1]):
                            self.installer.install_controller_files(self.root, destination=self.root / 'unused')
                finally:
                    shutil.rmtree(self.root)
                    backup.rename(self.root)

    def test_manifest_covers_every_installer_read(self):
        # Every source path literal used by install_controller_files is covered by a manifest entry.
        import re
        source = (ROOT / 'scripts/install-runtime-controller.py').read_text()
        body = source[source.index('def install_controller_files'):source.index('def systemd_payload')]
        entries = self.installer.payload_entries(ROOT)
        covered = lambda path: any(path == rel or path.startswith(rel + '/') or rel.startswith(path + '/') for rel, _, _ in entries)
        for path in re.findall(r"source_root / '([^']+)'", body):
            self.assertTrue(covered(path), path)

    def test_manifest_rejects_escaping_paths(self):
        (self.root / 'scripts/controller-payload.txt').write_text('../etc/passwd\n')
        with self.assertRaisesRegex(RuntimeError, 'Invalid'):
            self.installer.check_payload(self.root)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--make-fixture':
        make_fixture(Path(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == '--stage':
        install_from(Path(sys.argv[2]))
    else:
        unittest.main()
