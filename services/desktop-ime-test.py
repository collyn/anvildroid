"""Companion upgrades must verify the installed APK, including timed-out installs."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ime_worker_check', Path(__file__).with_name('runtime-worker.py'))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class CompanionUpgrade(unittest.TestCase):
    def scenario(self, installed_hash, timeout=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            instance = root / 'instance'
            instance.mkdir()
            data = b'new companion'
            (instance / 'desktop-ime.apk').write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            calls = []

            def shell(*args, **kwargs):
                calls.append(args)
                if args[:3] == ('pm', 'list', 'packages'):
                    return 'package:org.anvildroid.ime'
                if args[:2] == ('pm', 'path'):
                    return 'package:/data/app/test/base.apk'
                if args[0] == 'sha256sum':
                    return (digest if installed_hash == 'new' else '0' * 64) + '  base.apk'
                if args[:2] == ('pm', 'install'):
                    if timeout:
                        raise subprocess.TimeoutExpired(args, 30)
                    return 'Success'
                return 'org.anvildroid.ime/.BridgeIme'

            if timeout:
                with self.assertRaisesRegex(RuntimeError, 'did not finish'):
                    worker.configure_desktop_ime(instance, root / 'work', shell, True)
                self.assertFalse((instance / 'desktop-ime.sha256').exists())
            else:
                worker.configure_desktop_ime(instance, root / 'work', shell, True)
                self.assertEqual((instance / 'desktop-ime.sha256').read_text(), digest)
            return [args for args in calls if args[:2] == ('pm', 'install')]

    def test_old_companion_is_upgraded(self):
        self.assertEqual(len(self.scenario('old')), 1)

    def test_completed_install_is_reused(self):
        self.assertEqual(self.scenario('new'), [])

    def test_timeout_is_not_marked_successful(self):
        self.assertEqual(len(self.scenario('old', timeout=True)), 1)


if __name__ == '__main__':
    unittest.main()
