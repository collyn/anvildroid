#!/usr/bin/env python3
"""Disposable portable staging and installer file checks; no systemd or Android."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('portable_installer', ROOT / 'scripts/install-runtime-controller.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class PortablePackagingTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='anvil-portable-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write('Cargo.toml', b'[workspace.package]\nversion = "1.2.3"\n')
        self.write('README.md', b'Disposable packaging fixture\n')
        for name in installer.MODULES:
            self.write('services/' + name, (ROOT / 'services' / name).read_bytes())
        for name in ('package-portable.sh', 'build-x11-payload.py', 'release-version.py',
                     'install-portable.sh', 'install-runtime-controller.py',
                     'start-runtime-controller.py', 'setup-waydroid.py'):
            self.write('scripts/' + name, (ROOT / 'scripts' / name).read_bytes())
        # Pure fixture policy: the file-copy installer must never inspect host AppArmor.
        self.write('scripts/install-runtime-apparmor.py', b'def install(): return "Fixture AppArmor: no host changes"\n')
        for name in ('anvildroid-controller.service', '50-anvildroid.conf'):
            self.write('packaging/systemd/' + name, (ROOT / 'packaging/systemd' / name).read_bytes())
        for name in ('anvildroid-apps.rc', 'anvildroid-apps.sh', 'anvildroid-tasks.rc',
                     'anvildroid-tasks.sh', 'anvildroid-keymap.rc', 'anvildroid-keymap.sh'):
            self.write('native/' + name, b'fixture provision asset\n')
        self.write('native/overlay/AndroidManifest.xml', b'<manifest/>\n')
        self.write('native/menu-font-OFL.txt', b'Fixture license\n')
        self.write('native/x11/fixture.cpp', b'// fixture build source\n')
        self.write('target/release/anvildroid-gui', b'fixture GUI\n', 0o755)
        self.write('target/release/anvildroid', b'fixture CLI\n', 0o755)
        elf = bytearray(64); elf[:6] = b'\x7fELF\x02\x01'; elf[18:20] = b'\x3e\x00'
        for name in ('libanvildroid-path-guard.so', 'libanvildroid-runtime-window.so', 'libanvildroid-window.so'):
            self.write('target/native/' + name, elf)
        self.write('target/native/overlay-key.p12', b'fixture key')
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z: z.writestr('classes.dex', b'dex\nfixture')
        for name in ('target/ime/AnvilDroidIme.apk', 'target/shutdown/anvildroid-shutdown.jar',
                     'target/input-diagnostic/scrcpy-server-cancel.jar'):
            self.write(name, archive.getvalue())
        self.write('fake-bin/cargo', b'#!/bin/sh\nexit 0\n', 0o755)
        # A verified, inert backend fixture: payload is copied/hashed, never executed.
        self.payload = self.root / 'target/native-x11/package'
        sources = {'sanitized':False, 'sources':{'fixture.cpp':hashlib.sha256(b'// fixture build source\n').hexdigest()}}
        self.write('target/native-x11/package/source-manifest.json', json.dumps(sources).encode())
        self.write('target/native-x11/package/anvildroid-x11-host', elf, 0o755)
        self.write('target/native-x11/package/runtime-x11.py', (ROOT / 'services/runtime-x11.py').read_bytes())
        self.write('target/native-x11/package/NOTICE', b'Fixture native notice\n')
        self.write('target/native-x11/package/menu-font-OFL.txt', b'Fixture font license\n')
        self.manifest()

    def write(self, relative, data, mode=0o644):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data); target.chmod(mode)
        return target

    def manifest(self, directory=None):
        directory = directory or self.payload
        value = dict(format=3, backend='native-x11', experimental=True, android_sdks=[33,34,35,36],
                     surface_protocol=1, capabilities=['app-windows','shared-bridge','wayland-surface-tree'],
                     files={name:hashlib.sha256((directory/name).read_bytes()).hexdigest()
                            for name in ('anvildroid-x11-host','runtime-x11.py','source-manifest.json')})
        (directory / 'manifest.json').write_text(json.dumps(value))
        (directory / 'manifest.json').chmod(0o644)

    def bundle(self):
        environment = dict(os.environ, PATH=str(self.root/'fake-bin')+os.pathsep+os.environ['PATH'])
        environment.pop('CARGO_TARGET_DIR', None)
        return subprocess.run(['sh', str(self.root/'scripts/package-portable.sh'), 'fixture'],
                              cwd=self.root, env=environment, capture_output=True, text=True, timeout=30)

    def bundled_payload(self):
        target = self.root / 'native-x11'
        shutil.copytree(self.payload, target)
        return target

    def file_install(self, root=None):
        destination = self.root / 'installed/controller'
        # Retain actual file copies and atomic replacement, substituting only the
        # root-ownership requirement inside this unprivileged disposable fixture.
        with patch.object(installer, 'trusted_directory', side_effect=lambda path,strict=True:path.mkdir(parents=True,exist_ok=True)):
            installer.install_controller_files(root or self.root, destination=destination)
        return destination

    @unittest.skipIf(os.getuid()==0, 'Native payload development fixtures require an unprivileged owner')
    def test_portable_stages_default_hook_modules_native_payload_and_complete_checksums(self):
        result = self.bundle()
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        release = self.root / 'target/releases/fixture'
        for name in installer.MODULES:
            self.assertEqual((release/'services'/name).read_bytes(), (self.root/'services'/name).read_bytes())
        self.assertEqual((release/'packaging/systemd/50-anvildroid.conf').read_bytes(),
                         (ROOT/'packaging/systemd/50-anvildroid.conf').read_bytes())
        self.assertTrue(os.access(release/'native-x11/anvildroid-x11-host', os.X_OK))
        self.assertEqual((release/'native-x11/runtime-x11.py').read_bytes(), (release/'services/runtime-x11.py').read_bytes())
        verified = subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=release,capture_output=True,text=True,timeout=10)
        self.assertEqual(verified.returncode,0,verified.stdout+verified.stderr)
        installed = self.file_install(release)
        self.assertEqual((installed/'runtime-default-shares.py').read_bytes(), (ROOT/'services/runtime-default-shares.py').read_bytes())
        self.assertEqual((installed/'native-x11/manifest.json').read_bytes(), (release/'native-x11/manifest.json').read_bytes())
        self.assertTrue(os.access(installed/'native-x11/anvildroid-x11-host', os.X_OK))

    @unittest.skipIf(os.getuid()==0, 'Native payload development fixtures require an unprivileged owner')
    def test_portable_rejects_changed_native_sources_or_missing_payload_without_release(self):
        for invalid in ('stale-source','missing-host'):
            with self.subTest(invalid=invalid):
                if invalid=='stale-source': self.write('native/x11/fixture.cpp', b'// changed after build\n')
                else:
                    self.write('native/x11/fixture.cpp', b'// fixture build source\n')
                    (self.payload/'anvildroid-x11-host').unlink()
                result = self.bundle()
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertFalse((self.root/'target/releases/fixture').exists())
                self.assertEqual(list((self.root/'target').glob('.portable.*')),[])

    @unittest.skipIf(os.getuid()==0, 'Native payload development fixtures require an unprivileged owner')
    def test_installer_auto_detects_bundled_native_payload(self):
        self.bundled_payload()
        installed = self.file_install()
        for name in ('manifest.json','runtime-x11.py','source-manifest.json','anvildroid-x11-host','NOTICE','menu-font-OFL.txt'):
            self.assertEqual((installed/'native-x11'/name).read_bytes(), (self.root/'native-x11'/name).read_bytes())

    @unittest.skipIf(os.getuid()==0, 'Native payload development fixtures require an unprivileged owner')
    def test_invalid_bundled_payload_fails_before_controller_destination_is_written(self):
        target = self.bundled_payload()
        for invalid in ('hash','controller-version','sanitizer'):
            with self.subTest(invalid=invalid):
                shutil.rmtree(target); shutil.copytree(self.payload,target)
                if invalid=='hash': (target/'anvildroid-x11-host').write_bytes(b'changed')
                elif invalid=='controller-version':
                    (target/'runtime-x11.py').write_bytes((target/'runtime-x11.py').read_bytes()+b'\n# older controller\n')
                    self.manifest(target)
                else:
                    source = json.loads((target/'source-manifest.json').read_text()); source['sanitized']=True
                    (target/'source-manifest.json').write_text(json.dumps(source)); self.manifest(target)
                with self.assertRaises(RuntimeError): self.file_install()
                self.assertFalse((self.root/'installed').exists())

    def test_systemd_file_helper_installs_default_hook_without_system_commands(self):
        directory = self.root / 'installed/systemd'
        units = installer.systemd_payload(self.root)
        with patch.object(installer, 'trusted_directory', side_effect=lambda path,strict=True:path.mkdir(parents=True,exist_ok=True)), \
                patch.object(installer.subprocess, 'run', side_effect=AssertionError('System command forbidden')):
            installer.install_systemd_files(units,directory=directory)
        for relative,data in units.items():
            self.assertEqual((directory/relative).read_bytes(),data)
            self.assertEqual((directory/relative).stat().st_mode&0o777,0o644)
        hook = (directory/'waydroid-container.service.d/50-anvildroid.conf').read_text()
        self.assertIn('ExecStart=/usr/bin/python3 -B /usr/local/lib/anvildroid-controller/runtime-system.py',hook)
        self.assertIn('RuntimeDirectoryMode=0700',hook)

    def test_missing_default_hook_fails_before_installing_systemd_files(self):
        (self.root/'packaging/systemd/50-anvildroid.conf').unlink()
        with self.assertRaises(FileNotFoundError): installer.systemd_payload(self.root)
        self.assertFalse((self.root/'installed').exists())


if __name__ == '__main__': unittest.main()
