"""Package source migration rewrites only AnvilDroid entries."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('migrate', HERE.parent / 'scripts/migrate-package-source.py')
migrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate)


class Migration(unittest.TestCase):
    def test_rewrites_old_sources_and_leaves_other_repositories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apt = root / 'etc/apt/sources.list.d/anvildroid.list'
            apt.parent.mkdir(parents=True)
            apt.write_text(f'deb [arch=amd64 signed-by=/etc/apt/keyrings/anvildroid.gpg] {migrate.OLD} stable main\n')
            pacman = root / 'etc/pacman.conf'
            pacman.write_text(f'[core]\nServer = {migrate.OLD}/elsewhere\n\n[anvildroid]\nSigLevel = Required\nServer = {migrate.OLD}/arch/x86_64\n')
            migrate.main(root)
            self.assertIn(migrate.NEW + ' stable main', apt.read_text())
            text = pacman.read_text()
            self.assertIn(f'[core]\nServer = {migrate.OLD}/elsewhere', text)           # other sections untouched
            self.assertIn(f'Server = {migrate.NEW}/arch/x86_64', text)
            before = apt.read_text()
            migrate.main(root)                                                       # idempotent
            self.assertEqual(apt.read_text(), before)

    def test_missing_files_are_fine(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(migrate.main(Path(directory)), 0)


if __name__ == '__main__':
    unittest.main()
