#!/usr/bin/python3
"""Shader warm-up regression checks; fixtures only, no Android or GPU startup."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


system = module('runtime-system')


class ShaderCacheTests(unittest.TestCase):
    def test_flags_are_unique_idempotent_and_preserve_renderer_and_other_properties(self):
        original = ['keep=yes', 'debug.renderengine.backend=skiaglthreaded',
                    'service.sf.prime_shader_cache=1', 'service.sf.prime_shader_cache=true',
                    'debug.sf.prime_shader_cache=true', 'debug.sf.prime_shader_cache.hole_punch=true']
        result = system.gpu.surfaceflinger_properties(original)
        self.assertEqual(result, system.gpu.surfaceflinger_properties(result))
        self.assertIn('keep=yes', result)
        self.assertIn('debug.renderengine.backend=skiaglthreaded', result)
        self.assertIn('service.sf.prime_shader_cache=0', result)
        self.assertIn('debug.sf.prime_shader_cache.hole_punch=false', result)
        keys = [p.split('=', 1)[0] for p in result]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn('service.sf.prime_shader_cache=1', original)

    def test_default_nvidia_session_skips_cache_on_wayland_and_native_x11(self):
        for display in ('wayland-0', 'anvildroid-default-x11'):
            with self.subTest(display=display), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'waydroid.prop'
                original = 'keep=yes\nro.hardware.egl=mesa\nservice.sf.prime_shader_cache=1\n'
                tools = SimpleNamespace(
                    actions=SimpleNamespace(container_manager=SimpleNamespace(do_start=Mock(), freeze=Mock(), stop=Mock())),
                    helpers=SimpleNamespace(images=SimpleNamespace(mount_rootfs=Mock(),
                        make_prop=lambda args, session, path: Path(path).write_text(original))))
                integration = system.Integration(tools, Mock())
                with patch.object(system.existing, 'ExistingRuntime') as adapter:
                    adapter.return_value.desktop_overlay.return_value = False
                    integration.nvidia = True
                    integration.props({}, {'wayland_display':display, 'user_id':'1000'}, target)
                    props = target.read_text().splitlines()
                    self.assertIn('service.sf.prime_shader_cache=0', props)
                    self.assertNotIn('service.sf.prime_shader_cache=1', props)
                    self.assertIn('ro.hardware.egl=angle', props)
                    self.assertIn('debug.hwui.renderer=skiavk', props)
                    # The next non-NVIDIA session retains its original properties.
                    integration.nvidia = False
                    integration.props({}, {}, target)
                    self.assertEqual(target.read_text(), original)


if __name__ == '__main__':
    unittest.main()
