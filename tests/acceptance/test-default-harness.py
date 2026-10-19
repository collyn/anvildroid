"""Inert safety regressions for disposable Default launcher; no namespaces."""
import importlib.util
import json
import io
import errno
import os
import tarfile
import hashlib
from types import SimpleNamespace
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('default_acceptance', Path(__file__).with_name('default-android.py'))
harness = importlib.util.module_from_spec(spec); spec.loader.exec_module(harness)


class DefaultAcceptanceTests(unittest.TestCase):
    def test_scope_collection_race_requires_positive_disappearance(self):
        cgroup=Path('/sys/fs/cgroup/disposable.scope')
        for code in (errno.ENOENT,errno.ENODEV):
            with patch.object(Path,'read_text',side_effect=OSError(code,'collected')),patch.object(Path,'exists',return_value=False):
                self.assertFalse(harness.scope_populated(cgroup))
            with patch.object(Path,'read_text',side_effect=OSError(code,'still exists')),patch.object(Path,'exists',return_value=True):
                with self.assertRaises(OSError):harness.scope_populated(cgroup)
        with patch.object(Path,'read_text',return_value='populated 1\n'):
            self.assertTrue(harness.scope_populated(cgroup))

    def test_controls_provenance_is_exact_and_rejects_changed_helpers(self):
        names=('default-controls.py','probe-controls-recovery.py','probe-editor-isolation.py','probe-android-controls.py')
        expected={name:harness.digest(harness.SCRIPT.with_name(name)) for name in names}
        self.assertIsNone(harness.verify_controls({}))
        self.assertEqual(harness.verify_controls({'controls_helpers':expected}),expected)
        self.assertEqual(harness.verify_controls({'controls_helpers':expected,'controls_backend':'native-x11'}),expected)
        with self.assertRaisesRegex(RuntimeError,'backend'):
            harness.verify_controls({'controls_helpers':expected,'controls_backend':'personal-display'})
        with self.assertRaisesRegex(RuntimeError,'Incomplete'):
            harness.verify_controls({'controls_helpers':{'../external.py':'0'*64}})
        with self.assertRaisesRegex(RuntimeError,'changed'):
            harness.verify_controls({'controls_helpers':{**expected,names[0]:'0'*64}})

    def test_arm_inputs_are_all_or_none(self):
        from itertools import product
        for flags in product((None, Path('/tmp/input')), repeat=3):
            args = SimpleNamespace(arm_archive=flags[0], arm_probe32=flags[1], arm_probe64=flags[2])
            if any(flags) and not all(flags):
                with self.assertRaisesRegex(RuntimeError, 'required together'): harness.arm_inputs(args)
            else: self.assertEqual(harness.arm_inputs(args), flags if all(flags) else None)

    def test_arm_provenance_pins_archive_and_each_private_regular_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = {}
            for name in ('arm.zip', 'arm32.apk', 'arm64.apk'):
                (root / name).write_bytes(name.encode()); (root / name).chmod(0o600)
                expected[name] = harness.digest(root / name)
            arm = SimpleNamespace(SHA256=expected['arm.zip'])
            original = Path.lstat
            def root_owned(path, *args, **kwargs):
                result = list(original(path, *args, **kwargs)); result[4] = 0
                return os.stat_result(result)
            with patch.object(harness, 'load_module', return_value=arm), patch.object(Path, 'lstat', root_owned):
                self.assertEqual(harness.verify_arm_inputs(root, {'arm_inputs': expected}), expected)
                self.assertIsNone(harness.verify_arm_inputs(root, {}))
                with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                    harness.verify_arm_inputs(root, {'arm_inputs': {'arm.zip': expected['arm.zip']}})
                with self.assertRaisesRegex(RuntimeError, 'production-pinned'):
                    harness.verify_arm_inputs(root, {'arm_inputs': {**expected, 'arm.zip': '0' * 64}})
                for name in expected:
                    path = root / name
                    path.write_bytes(b'tampered')
                    with self.assertRaisesRegex(RuntimeError, 'changed'): harness.verify_arm_inputs(root, {'arm_inputs': expected})
                    path.write_bytes(name.encode()); path.chmod(0o644)
                    with self.assertRaisesRegex(RuntimeError, 'Unsafe'): harness.verify_arm_inputs(root, {'arm_inputs': expected})
                    path.chmod(0o600); os.link(path, root / 'alias')
                    with self.assertRaisesRegex(RuntimeError, 'Unsafe'): harness.verify_arm_inputs(root, {'arm_inputs': expected})
                    (root / 'alias').unlink()
                path = root / 'arm32.apk'; path.unlink(); path.symlink_to(root / 'arm64.apk')
                with self.assertRaisesRegex(RuntimeError, 'Unsafe'): harness.verify_arm_inputs(root, {'arm_inputs': expected})

    def test_arm_staging_copies_only_explicit_inputs_and_refuses_unpinned_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp); root = directory / 'fixture'; root.mkdir()
            sources = []
            for name in ('source.zip', 'source32.apk', 'source64.apk'):
                path = directory / name; path.write_bytes(name.encode()); sources.append(path)
            args = SimpleNamespace(arm_archive=sources[0], arm_probe32=sources[1], arm_probe64=sources[2])
            with patch.object(harness, 'load_module', return_value=SimpleNamespace(SHA256='wrong')):
                with self.assertRaisesRegex(RuntimeError, 'production-pinned'): harness.stage_arm_inputs(root, {}, args)
            self.assertEqual(list(root.iterdir()), [])
            with patch.object(harness, 'load_module', return_value=SimpleNamespace(SHA256=harness.digest(sources[0]))):
                value = {}; harness.stage_arm_inputs(root, value, args)
            for name, source in zip(('arm.zip', 'arm32.apk', 'arm64.apk'), sources):
                self.assertEqual((root / name).read_bytes(), source.read_bytes())
                self.assertNotEqual((root / name).stat().st_ino, source.stat().st_ino)
                self.assertEqual((root / name).stat().st_mode & 0o777, 0o600)
            self.assertEqual(set(value['arm_inputs']), {'arm.zip', 'arm32.apk', 'arm64.apk'})

    def test_arm_overlay_verifies_installed_bytes_against_verified_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / 'work/overlay/system/lib/bridge.so'
            target.parent.mkdir(parents=True); target.write_bytes(b'verified'); target.chmod(0o644)
            manifest = {'files': {'system/lib/bridge.so': harness.digest(target)}}
            arm = SimpleNamespace(SHA256='pinned', verify=lambda *args: manifest)
            original = Path.lstat
            def root_owned(path, *args, **kwargs):
                result = list(original(path, *args, **kwargs)); result[4] = 0
                return os.stat_result(result)
            with patch.object(harness, 'load_module', return_value=arm), patch.object(Path, 'lstat', root_owned):
                checksum = harness.verify_arm_overlay(root, {'arm_inputs': True})
                self.assertEqual(len(checksum), 64)
                self.assertIsNone(harness.verify_arm_overlay(root, {}))
                target.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'overlay changed'): harness.verify_arm_overlay(root, {'arm_inputs': True})
                target.chmod(0o666)
                with self.assertRaisesRegex(RuntimeError, 'Unsafe'): harness.verify_arm_overlay(root, {'arm_inputs': True})

    def test_removed_source_rejects_exact_mounts_including_backend(self):
        for malformed in ('', '   ', '42 1 0:5 / /data rw', '42 1 0:5 / /data rw missing separator fields'):
            with self.subTest(malformed=malformed), self.assertRaises(RuntimeError):
                harness.verify_removed_mounts(malformed)
        for target in harness.REMOVED_TARGETS:
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                harness.verify_removed_mounts('42 1 0:5 / ' + target + ' rw - tmpfs source rw')
        harness.verify_removed_mounts('42 1 0:5 / /data/media rw - tmpfs source rw')
        harness.verify_removed_mounts('42 1 0:5 / /sdcard/AnvilDroid/Acceptance-other rw - tmpfs source rw')
        # Decode mountinfo escapes before exact comparison, not substring matching.
        with self.assertRaises(RuntimeError):
            harness.verify_removed_mounts('42 1 0:5 / /sdcard/AnvilDroid/Acceptan\\143e rw - tmpfs source rw')
    def test_removed_target_allows_only_absent_or_ordinary_empty_directory(self):
        harness.verify_removed_target('absent', True, False)
        harness.verify_removed_target('directory', True, False)
        for kind, empty, sentinel in [('directory', False, False), ('symlink', True, False),
                                     ('other', True, False), ('directory', True, True), ('absent', True, True)]:
            with self.subTest(kind=kind, empty=empty, sentinel=sentinel), self.assertRaises(RuntimeError):
                harness.verify_removed_target(kind, empty, sentinel)
    def test_roots_reject_personal_relative_and_symlink_paths(self):
        for path in ('/var/lib/waydroid', '/tmp/anvil-live-foo', '/tmp/anvil-default-', 'anvil-default-foo', '/tmp/a/../anvil-default-good', '/tmp/anvil-default-space name', '/tmp/anvil-default-x;cmd'):
            with self.subTest(path=path), self.assertRaises(RuntimeError): harness.validate_root(Path(path), False)
        with tempfile.TemporaryDirectory() as temp:
            alias = Path('/tmp/anvil-default-alias-fixture')
            alias.symlink_to(temp)
            try:
                with self.assertRaises(RuntimeError): harness.validate_root(alias, False)
            finally: alias.unlink()
    def test_transient_scope_has_bounds_and_delegation(self):
        command = harness.scope_command(Path('/tmp/anvil-default-test'), {'scope': 'anvil-default-test.scope'}, 'preflight')
        for value in ('Delegate=yes', 'MemoryMax=6G', 'MemorySwapMax=0', 'CPUQuota=400%', 'TasksMax=2048', 'OOMPolicy=kill', '--preflight'):
            self.assertIn(value, command)
        self.assertNotIn('--run', command)
        controls = harness.scope_command(Path('/tmp/anvil-default-test'),
            {'scope': 'anvil-default-test.scope', 'controls_helpers': {'pinned': 'fixture'}}, 'preflight')
        self.assertIn('TasksMax=4096', controls)
        self.assertIn('MemoryMax=6G', controls)
    def audit(self, text):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('config', 'config_nodes', 'config_session'): (root / name).write_text(text if name == 'config' else '')
            return harness.audit_lxc(root)
    def test_external_hooks_namespaces_and_mounts_rejected(self):
        for line in ('lxc.hook.pre-start = /host-command', 'lxc.include = /etc/lxc/default.conf',
                     'lxc.namespace.share.net = 1', 'lxc.mount.fstab = /etc/fstab',
                     'lxc.mount.entry = /run/systemd/private run/systemd/private none bind 0 0',
                     'lxc.mount.entry = /mnt/private data none bind 0 0',
                     'lxc.mount.entry = /home/other/data data none bind 0 0',
                     'lxc.mount.entry = /var/lib/waydroid/../../personal data none bind 0 0',
                     'lxc.rootfs.path = /var/lib/personal', 'lxc.cgroup.dir = ../../system.slice'):
            with self.subTest(line=line), self.assertRaises(RuntimeError): self.audit(line)
    def test_global_kernel_capabilities_rejected(self):
        for capability in harness.DROPPED_CAPS:
            with self.subTest(capability=capability), self.assertRaises(RuntimeError): self.audit('lxc.cap.keep = sys_admin ' + capability)
    def test_automatic_mounts_expose_only_private_writable_cgroup(self):
        self.audit('lxc.mount.auto = cgroup:rw sys:ro proc')
        for value in ('cgroup:rw sys:rw proc', 'cgroup-full:rw sys:ro proc', 'cgroup:ro sys:ro proc',
                      'cgroup:rw sys:ro proc:mixed', 'cgroup:rw sys:ro proc /host'):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.audit('lxc.mount.auto = ' + value)
    def test_private_sources_and_stock_hook_permitted(self):
        result = self.audit('lxc.rootfs.path = /var/lib/waydroid/rootfs\nlxc.hook.post-stop = /dev/null\nlxc.cap.keep = sys_admin setuid\nlxc.cgroup.dir = android\nlxc.mount.entry = /dev/anvil-fixture-binder dev/binder none bind 0 0\nlxc.mount.entry = /home/acceptance/data data none rbind 0 0')
        self.assertEqual(set(result), {'config', 'config_nodes', 'config_session'})
    def test_preflight_cannot_start_guest(self):
        source = Path(harness.__file__).read_text()
        inside = source.split('def inside(args):', 1)[1].split('def main():', 1)[0]
        self.assertNotIn('proxy.Start(', inside)
        self.assertIn('if args.run:', inside)
        self.assertEqual(inside.count('run_lifecycle('), 1)
    def test_init_cgroup_must_be_task_android_descendant(self):
        for path in ('/android', '/android/payload', '/supervisor/android'):
            self.assertEqual(harness.validate_init_cgroup('0::' + path), path)
        for path in ('/', '/supervisor', '/android-elsewhere', '/android/../host', '/system.slice'):
            with self.assertRaises(RuntimeError): harness.validate_init_cgroup('0::' + path)
    def test_absolute_mount_target_only_allows_exact_stock_private_tmpfs(self):
        self.audit('lxc.mount.entry = tmpfs /run/xdg none create=dir 0 0')
        for line in ('lxc.mount.entry = tmpfs /etc none create=dir 0 0',
                     'lxc.mount.entry = /home/acceptance/data /run/xdg none bind 0 0',
                     'lxc.mount.entry = tmpfs /run/xdg none create=dir,rw 0 0'):
            with self.assertRaises(RuntimeError): self.audit(line)
    def test_package_and_image_provenance_checks_real_bytes_and_link_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'controller.deb').write_bytes(b'pinned-package')
            payload = root / 'package/usr/local/lib/anvildroid-controller'; payload.mkdir(parents=True)
            code = b'print("pinned runtime")\n'; (payload / 'runtime.py').write_bytes(code); (payload / 'runtime.py').chmod(0o644)
            images = root / 'work/images'; images.mkdir(parents=True)
            for name in ('system.img', 'vendor.img'):
                (images / name).write_bytes(name.encode()); (images / name).chmod(0o644)
            value = {'package_sha256': harness.digest(root / 'controller.deb'),
                     'images': {name: {'copy_sha256': harness.digest(images / name)} for name in ('system.img', 'vendor.img')}}
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w') as output:
                member = tarfile.TarInfo('./usr/local/lib/anvildroid-controller/runtime.py'); member.size = len(code)
                output.addfile(member, io.BytesIO(code))
            def process(*a, **k): return SimpleNamespace(stdout=io.BytesIO(archive.getvalue()), wait=lambda **k: 0, poll=lambda: 0)
            original = Path.lstat
            def fixture_owned(path, *a, **k):
                result = list(original(path, *a, **k)); result[4] = 0
                return os.stat_result(result)
            with patch.object(harness.subprocess, 'Popen', side_effect=process), patch.object(Path, 'lstat', fixture_owned):
                baseline = harness.verify_copies(root, value)
                self.assertEqual(baseline['copied_images']['system.img']['sha256'], value['images']['system.img']['copy_sha256'])
                (payload / 'runtime.py').write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'package bytes differ'): harness.verify_copies(root, value)
                (payload / 'runtime.py').write_bytes(code)
                (images / 'system.img').write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'image bytes changed'): harness.verify_copies(root, value)
                (images / 'system.img').write_bytes(b'system.img')
                os.link(images / 'system.img', images / 'alias')
                with self.assertRaisesRegex(RuntimeError, 'Unsafe copied image'): harness.verify_copies(root, value)
                (images / 'alias').unlink()
                (images / 'system.img').unlink(); (images / 'system.img').symlink_to(images / 'vendor.img')
                with self.assertRaisesRegex(RuntimeError, 'Unsafe copied image'): harness.verify_copies(root, value)
    def test_compiled_device_filter_rejects_unrelated_device_identities(self):
        program = harness.device_program([(2, 1, 3), (2, 505, 9), (1, 7, 22), (2, 136, None), (2, 1, 11, 1)])
        instructions = [struct.unpack_from('<BBhi', program, index) for index in range(0, len(program), 8)]
        def evaluate(kind, major, minor, access):
            registers = [0] * 11; context = {0: access << 16 | kind, 4: major, 8: minor}; pc = 0
            while True:
                opcode, dstsrc, offset, immediate = instructions[pc]; target = dstsrc & 15
                if opcode == 0x61: registers[target] = context[offset]
                elif opcode == 0x54: registers[target] &= immediate
                elif opcode == 0x74: registers[target] >>= immediate
                elif opcode == 0x55:
                    if registers[target] != immediate: pc += offset
                elif opcode == 0xb7: registers[target] = immediate
                elif opcode == 0x95: return registers[0]
                else: raise AssertionError('Unexpected instruction')
                pc += 1
        for access in range(8):
            self.assertEqual(evaluate(2, 1, 11, access), int(access == 1))
            for kind, major, minor in ((1, 1, 11), (2, 2, 11), (2, 1, 12)):
                self.assertEqual(evaluate(kind, major, minor, access), 0)
        for access in (1, 2, 4, 7):
            for kind, major, minor in ((2, 1, 3), (2, 505, 9), (1, 7, 22), (2, 136, 123)):
                self.assertEqual(evaluate(kind, major, minor, access), 1)
            for kind, major, minor in ((2, 10, 237), (1, 7, 0), (1, 7, 23), (2, 505, 10), (1, 8, 0), (1, 1, 3)):
                self.assertEqual(evaluate(kind, major, minor, access), 0)
    def test_network_sysctl_policy_is_an_exact_file_allowlist(self):
        self.assertEqual(harness.NET_SYSCTLS, ('/proc/sys/net/ipv4/ip_forward',))
        self.assertEqual(harness.NET_SYSCTL_TREES, ('/proc/sys/net/ipv6/conf',))
    def test_only_explicit_test_hook_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('config', 'config_nodes', 'config_session'): (root / name).write_text('')
            (root / 'config_session').write_text('lxc.mount.entry = /run/dbus/system_bus_socket run/bus none bind 0 0')
            with self.assertRaises(RuntimeError): harness.audit_lxc(root, hook='/tmp/audit-start')
            (root / 'config_session').write_text('')
            (root / 'config').write_text('lxc.hook.pre-start = /tmp/audit-start')
            harness.audit_lxc(root, hook='/tmp/audit-start')
            with self.assertRaises(RuntimeError): harness.audit_lxc(root, hook='/tmp/other')
    def test_module_caps_remain_dropped_at_outer_launcher(self):
        source = Path(harness.__file__).read_text()
        self.assertIn('--bounding-set=-sys_module,-sys_time,-sys_boot,-sys_rawio', source)
        self.assertNotIn("run('modprobe'", source)


if __name__ == '__main__': unittest.main()
