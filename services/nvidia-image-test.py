#!/usr/bin/env python3
"""NVIDIA image admission checks, without starting a GPU or Android."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
spec = importlib.util.spec_from_file_location('nvidia', Path(__file__).with_name('runtime-nvidia.py'))
nv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nv)

class Images(unittest.TestCase):
    def image(self, root, sdk):
        files = set(nv.PAYLOAD_FILES['guest32'] + nv.PAYLOAD_FILES['guest64'])
        files.update(('vendor/lib64/hw/gralloc.minigbm_gbm_mesa.so',
                      'vendor/lib64/vendor.waydroid.display@1.0.so',
                      'vendor/lib64/vendor.waydroid.window@1.2.so'))
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        (root / 'system/bin').mkdir(parents=True)
        (root / 'system/lib64').mkdir()
        (root / 'system/build.prop').write_text(f'ro.build.version.sdk={sdk}\n')
        (root / 'system/bin/surfaceflinger').write_bytes(b'vendor.waydroid.display@1.0')
        (root / 'vendor/lib64/hw/hwcomposer.waydroid.so').write_bytes(
            b'vendor.waydroid.display@1.0 vendor.waydroid.window@1.0 '
            b'persist.waydroid.multi_windows wl_shm zwp_linux_dmabuf_v1')

    def test_legacy_and_modern_mounts_preserve_surfaceflinger(self):
        for sdk in (33, 36):
            with self.subTest(sdk=sdk), tempfile.TemporaryDirectory() as d:
                root = Path(d); self.image(root, sdk)
                mounts = nv.image_mounts(root, native_composer=sdk != 33)
                self.assertEqual(len(mounts), len(nv.PAYLOAD_FILES['guest32'] + nv.PAYLOAD_FILES['guest64']) - (sdk != 33))
                self.assertFalse(any('surfaceflinger' in str(target) for _, target in mounts))
                hwc = root / 'vendor/lib64/hw/hwcomposer.waydroid.so'
                self.assertEqual(hwc in [target for _, target in mounts], sdk == 33)

    def test_modern_requires_native_composer(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.image(root, 36)
            with self.assertRaisesRegex(RuntimeError, 'verified native Waydroid composer'):
                nv.image_mounts(root)

    def test_reject_missing_display_or_composer_contract(self):
        for name in ('system/bin/surfaceflinger', 'vendor/lib64/hw/hwcomposer.waydroid.so'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                root = Path(d); self.image(root, 36); (root / name).write_bytes(b'incompatible')
                with self.assertRaisesRegex(RuntimeError, 'native Waydroid per-app composer ABI'):
                    nv.image_mounts(root, native_composer=True)

    def test_missing_hal_or_symlink_mount_target(self):
        for name in ('vendor/lib64/vendor.waydroid.window@1.2.so',
                     'vendor/lib64/hw/vulkan.virtio.so'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                root = Path(d); self.image(root, 36)
                target = root / name; target.unlink()
                if 'vulkan' in name:
                    other = root / 'other'; other.touch(); target.symlink_to(other)
                with self.assertRaises(RuntimeError):
                    nv.image_mounts(root, native_composer=True)

    def test_unknown_sdk_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.image(root, 37)
            with self.assertRaisesRegex(RuntimeError, 'SDK 33-36'):
                nv.image_mounts(root, native_composer=True)

    def test_mesa_only_image_missing_angle_targets_are_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.image(root, 36)
            angle = root / 'vendor/lib/egl/libEGL_angle.so'; angle.unlink()
            with self.assertRaisesRegex(RuntimeError, 'mount target'):
                nv.image_mounts(root, native_composer=True)
            mounts = nv.image_mounts(root, native_composer=True, allow_missing_angle=True)
            self.assertIn(angle, [target for _, target in mounts])
            self.assertFalse(angle.exists())  # Admission never edits the image.
            angle.symlink_to(root / 'missing')
            with self.assertRaisesRegex(RuntimeError, 'mount target'):
                nv.image_mounts(root, native_composer=True, allow_missing_angle=True)

if __name__ == '__main__': unittest.main()
