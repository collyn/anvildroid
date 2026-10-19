"""Unprivileged ARM source collection: bounded input, no installer execution."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('arm_source', Path(__file__).resolve().parents[1] / 'services/runtime-arm-source.py')
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, length=None, url='https://example.com/arm.zip'):
        super().__init__(data)
        self.headers = {} if length is None else {'Content-Length': length}
        self.url = url

    def geturl(self):
        return self.url


class ArmSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_folder_produces_zip_with_relative_paths(self):
        library = self.root / 'system/lib64/libndk_translation.so'
        library.parent.mkdir(parents=True)
        library.write_bytes(b'ELF fixture')
        (self.root / 'readme.txt').write_text('source')
        output = io.BytesIO()
        source.collect('folder', self.root, output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {'system/lib64/libndk_translation.so', 'readme.txt'})
            self.assertEqual(archive.read('system/lib64/libndk_translation.so'), b'ELF fixture')

    def test_folder_never_executes_installer(self):
        marker = self.root / 'executed'
        script = self.root / 'install.sh'
        script.write_text(f'#!/bin/sh\ntouch {marker}\n')
        script.chmod(0o755)
        source.collect('folder', self.root, io.BytesIO())
        self.assertFalse(marker.exists())

    def test_folder_rejects_file_and_directory_symlinks(self):
        for target in ('/etc/passwd', '/tmp'):
            with self.subTest(target=target):
                link = self.root / 'link'
                link.symlink_to(target)
                try:
                    with self.assertRaises(OSError):
                        source.pack_folder(self.root, io.BytesIO())
                finally:
                    link.unlink()

    def test_rejects_fifo_without_blocking(self):
        os.mkfifo(self.root / 'pipe')
        with self.assertRaisesRegex(ValueError, 'regular files'):
            source.pack_folder(self.root, io.BytesIO())
        with self.assertRaisesRegex(ValueError, 'regular ARM archive'):
            source.collect('archive', self.root / 'pipe', io.BytesIO())

    def test_folder_limits_entries_and_expanded_bytes(self):
        (self.root / 'one').write_bytes(b'12345')
        with patch.object(source, 'MAX_EXPANDED', 4):
            with self.assertRaisesRegex(ValueError, 'size limit'):
                source.pack_folder(self.root, io.BytesIO())
        with patch.object(source, 'MAX_ENTRIES', 0):
            with self.assertRaisesRegex(ValueError, 'too many entries'):
                source.pack_folder(self.root, io.BytesIO())

    def test_empty_folder_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'empty'):
            source.pack_folder(self.root, io.BytesIO())

    def test_archive_copy_is_bounded(self):
        archive = self.root / 'payload.zip'
        archive.write_bytes(b'12345')
        with patch.object(source, 'MAX_ARCHIVE', 4):
            with self.assertRaisesRegex(ValueError, 'regular ARM archive'):
                source.collect('archive', archive, io.BytesIO())
        output = io.BytesIO()
        source.collect('archive', archive, output)
        self.assertEqual(output.getvalue(), b'12345')

    def test_builtin_uses_pinned_source_and_digest(self):
        with patch.object(source, 'download') as download:
            output = io.BytesIO()
            source.collect('builtin', '', output, 'ignored')
            download.assert_called_once_with(source.SOURCE_URL, output, source.SOURCE_SHA256)

    def test_catalog_version_pins_houdini_download_and_rejects_unknown_version(self):
        houdini = next(item for item in source.CATALOG if item['engine'] == 'libhoudini' and not item.get('variant'))
        with patch.object(source, 'download') as download:
            output = io.BytesIO()
            source.collect('builtin', houdini['sha256'], output)
            download.assert_called_once_with(houdini['url'], output, houdini['sha256'])
            with self.assertRaisesRegex(ValueError,'Unknown ARM catalog version'):
                source.collect('builtin','unknown',output)

    def test_catalog_covers_helper_releases_with_unique_variant_identities(self):
        self.assertEqual(len(source.CATALOG), 14)  # 13 helper entries + legacy HPE.
        self.assertEqual(len({entry['sha256'] for entry in source.CATALOG}), 14)
        self.assertEqual(sum(bool(entry.get('variant')) for entry in source.CATALOG), 5)
        self.assertEqual({sdk for entry in source.CATALOG for sdk in entry['android_sdks']}, {30, 33})
        for entry in source.CATALOG:
            self.assertRegex(entry['revision'], r'^[a-f0-9]{40}$')
            self.assertTrue(entry['url'].endswith('/' + entry['revision']))
            self.assertRegex(entry['sha256'], r'^[a-f0-9]{64}$')
            if entry.get('variant'):
                self.assertNotEqual(entry['sha256'], entry['upstream_sha256'])
                self.assertRegex(entry['patch_sha256'], r'^[a-f0-9]{64}$')

    def variant_fixture(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.comment = b'original comment'
            archive.writestr('system/lib64/libhoudini.so', b'old-library')
        raw = raw.getvalue()
        marked = source.mark_variant(raw, 'fixture-patched')
        metadata = dict(variant='fixture-patched', patches=[(0, '6f6c64', '6e6577')],
                        upstream_sha256=hashlib.sha256(raw).hexdigest(),
                        sha256=hashlib.sha256(marked).hexdigest(), url='https://example.com/pinned.zip',
                        patch_sha256=hashlib.sha256(b'new-library').hexdigest())
        return raw, marked, metadata

    def test_variant_collection_preserves_zip_members_and_has_stable_checksum(self):
        raw, marked, metadata = self.variant_fixture()
        def download(url, output, expected):
            self.assertEqual((url, expected), (metadata['url'], metadata['upstream_sha256']))
            output.write(raw)
        with patch.object(source, 'CATALOG', [metadata]), patch.object(source, 'download', side_effect=download):
            output = io.BytesIO()
            source.collect('builtin', metadata['sha256'], output)
            self.assertEqual(output.getvalue(), marked)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ['system/lib64/libhoudini.so'])
                self.assertEqual(archive.read(archive.namelist()[0]), b'old-library')
                self.assertIn(b'fixture-patched', archive.comment)
        self.assertEqual(source.mark_variant(marked, 'fixture-patched'), marked)

    def test_variant_collection_rejects_changed_upstream_or_variant_identity(self):
        raw, _, metadata = self.variant_fixture()
        for changes, expected in [({'upstream_sha256':'0'*64}, 'upstream'), ({'sha256':'0'*64}, 'variant')]:
            entry = dict(metadata, **changes)
            with self.subTest(changes=changes), patch.object(source, 'CATALOG', [entry]), \
                    patch.object(source, 'download', side_effect=lambda url, output, checksum: output.write(raw)):
                output = io.BytesIO()
                with self.assertRaisesRegex(ValueError, expected): source.collect('builtin', entry['sha256'], output)
                self.assertEqual(output.getvalue(), b'')

    def test_patch_checks_original_bytes_and_complete_output_hash(self):
        _, _, metadata = self.variant_fixture()
        self.assertEqual(source.patch_library(b'old-library', metadata), b'new-library')
        for data, entry, expected in [(b'bad-library', metadata, 'byte mismatch'),
                                      (b'old-library', dict(metadata, patch_sha256='0'*64), 'checksum mismatch'),
                                      (b'o', metadata, 'byte mismatch'),
                                      (b'old-library', dict(metadata, patches=[(-1,'6f','6e')]), 'byte mismatch')]:
            with self.subTest(expected=expected), self.assertRaisesRegex(RuntimeError, expected):
                source.patch_library(data, entry)

    def test_variant_marker_rejects_invalid_or_oversize_archives(self):
        raw, _, _ = self.variant_fixture()
        for blob in (b'bad ZIP', raw[:-1], raw + b'trailing'):
            with self.assertRaisesRegex(ValueError, 'Invalid'): source.mark_variant(blob, 'fixture')
        with patch.object(source, 'MAX_ARCHIVE', len(raw)):
            with self.assertRaisesRegex(ValueError, 'size limit'): source.mark_variant(raw, 'x' * 100)

    def test_https_only_including_redirects(self):
        invalid = ('http://example.com/payload.zip', 'file:///etc/passwd',
                   'https://user:pass@example.com/payload.zip', 'https://example.com/x#fragment',
                   'https://example.com:bad/x', 'https://example.com/\nx')
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                source.https_url(url)
        request = source.urllib.request.Request('https://example.com/arm.zip')
        with self.assertRaises(ValueError):
            source.HTTPSRedirect().redirect_request(request, None, 302, '', {}, 'http://example.com/arm.zip')

    def test_download_checks_hash_length_and_size(self):
        data = b'payload'
        digest = hashlib.sha256(data).hexdigest()
        cases = ((Response(data, '7'), digest, None),
                 (Response(data, '7'), '0' * 64, 'SHA-256 mismatch'),
                 (Response(data, '9'), '', 'interrupted'),
                 (Response(data, str(source.MAX_ARCHIVE + 1)), '', 'exceeds'),
                 (Response(data, 'nonsense'), '', 'invalid'))
        for response, expected, error in cases:
            with self.subTest(error=error), patch.object(source.urllib.request, 'build_opener') as build:
                build.return_value.open.return_value = response
                output = io.BytesIO()
                if error:
                    with self.assertRaisesRegex((RuntimeError, ValueError), error):
                        source.download(response.url, output, expected)
                else:
                    self.assertEqual(source.download(response.url, output, expected), digest)
                    self.assertEqual(output.getvalue(), data)

    def test_missing_length_does_not_bypass_download_limit(self):
        with patch.object(source, 'MAX_ARCHIVE', 4), patch.object(source.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = Response(b'12345')
            with self.assertRaisesRegex(ValueError, 'size limit'):
                source.download('https://example.com/arm.zip', io.BytesIO())

    def test_deadline_and_invalid_digest_fail_before_reading(self):
        with patch.object(source.urllib.request, 'build_opener') as build:
            with self.assertRaisesRegex(ValueError, '64 hexadecimal'):
                source.download('https://example.com/arm.zip', io.BytesIO(), 'invalid')
            build.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            source.copy_bounded(io.BytesIO(b'payload'), io.BytesIO(), 100, deadline=0)

    def test_cli_writes_archive_to_seekable_descriptor(self):
        (self.root / 'bridge.so').write_bytes(b'fixture')
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(['/usr/bin/python3', '-I', str(Path(source.__file__)),
                                     'folder', str(self.root), ''], stdout=output,
                                    stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            output.seek(0)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('bridge.so'), b'fixture')


if __name__ == '__main__':
    unittest.main()
