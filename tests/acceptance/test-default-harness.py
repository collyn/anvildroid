"""Inert safety regressions for disposable Default launcher; no namespaces."""
import importlib.util
import json
import io
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
