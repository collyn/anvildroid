import importlib.util
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location('runtime_worker', Path(__file__).resolve().parents[2] / 'services/runtime-worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
class CanvasProperties(unittest.TestCase):
    def test_reserves_portrait_and_landscape_without_duplicates(self):
        props=worker.single_display_properties(['persist.waydroid.width=1280','persist.waydroid.height=720','other=keep'])
        self.assertIn('persist.waydroid.width=1280',props)
        self.assertIn('persist.waydroid.height=1280',props)
        self.assertIn('waydroid.anvil_single_display=true',props)
        self.assertIn('other=keep',props)
        self.assertEqual(worker.single_display_properties(props),props)
    def test_preserves_larger_capacity_and_rejects_invalid_allocations(self):
        self.assertIn('persist.waydroid.width=1920',worker.single_display_properties(['persist.waydroid.width=1920']))
        for value in ['0','bad','9000']:
            with self.assertRaises(RuntimeError):worker.single_display_properties(['persist.waydroid.width='+value])
        with self.assertRaises(RuntimeError):worker.single_display_properties(['persist.waydroid.width=8192','persist.waydroid.height=8192'])
if __name__ == '__main__':unittest.main()
