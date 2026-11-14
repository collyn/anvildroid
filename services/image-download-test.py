#!/usr/bin/env python3
"""Segmented, resumable image downloads against a local range-capable server."""
import hashlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
import zipfile

SERVICES = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SERVICES / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


download = load('runtime_download', 'runtime-download.py')
# The real cache is root-owned and only talks to the official HTTPS mirrors.
download.trusted_url = lambda url: url
download.Downloads.check_file = staticmethod(lambda path: None)
download.CHUNK = 64 * 1024
download.BACKOFF = 0.01


class Mirror:
    """Serves named payloads; can ignore ranges or cut the transfer short."""
    def __init__(self):
        mirror = self
        self.files, self.ranges, self.budget, self.requests, self.slow, self.paths = {}, True, None, [], set(), []
        self.lock = threading.Lock()

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *args): pass

            def do_GET(self):
                data = mirror.files.get(self.path)
                if data is None: return self.send_error(404)
                header = self.headers.get('Range')
                if header and mirror.ranges:
                    start, end = (int(part) for part in header[len('bytes='):].split('-'))
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
                    body = data[start:end + 1]
                else:
                    start, body = 0, data
                    self.send_response(200)
                with mirror.lock:
                    mirror.requests.append((start, len(body)))
                    mirror.paths.append(self.path)
                    if mirror.budget is not None:
                        allowed = min(len(body), mirror.budget)
                        mirror.budget -= allowed
                        body = body[:allowed]
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    if self.path in mirror.slow:
                        for offset in range(0, len(body), 1024):
                            self.wfile.write(body[offset:offset + 1024])
                            time.sleep(0.05)
                    else: self.wfile.write(body)
                except ConnectionError: pass  # the client stopped reading

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def entry(self, name, data):
        self.files['/' + name] = data
        return {'url': f'http://127.0.0.1:{self.server.server_address[1]}/{name}', 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest(), 'filename': name}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class SegmentedDownloadTest(unittest.TestCase):
    def setUp(self):
        self.mirror = Mirror()
        self.addCleanup(self.mirror.close)
        directory = tempfile.TemporaryDirectory(prefix='anvil-download-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = os.urandom(download.CHUNK * 9 + 1234)
        self.entry = self.mirror.entry('image.zip', self.data)
        self.partial = self.root / ('.partial-' + self.entry['sha256'])
        self.cancel = threading.Event()

    def fetch(self, segments=4):
        seen = []
        download.download_segmented(self.entry, self.partial, self.cancel, seen.append, segments)
        return seen

    def test_chunks_from_parallel_connections_form_the_exact_archive(self):
        seen = self.fetch()
        self.assertEqual(self.partial.read_bytes(), self.data)
        self.assertEqual(seen[-1], len(self.data))
        self.assertEqual(len(self.mirror.requests), 10)
        self.assertEqual(download.resumed_bytes(self.partial, len(self.data)), len(self.data))

    def test_interrupted_download_resumes_without_fetching_finished_chunks(self):
        self.mirror.budget = download.CHUNK * 4 + 100
        with self.assertRaisesRegex(RuntimeError, 'retry to resume'): self.fetch()
        kept = download.resumed_bytes(self.partial, len(self.data))
        self.assertEqual(kept, download.CHUNK * 4 + 100)
        self.assertLess(kept, len(self.data))
        self.mirror.budget, self.mirror.requests = None, []
        seen = self.fetch()
        self.assertEqual(self.partial.read_bytes(), self.data)
        self.assertEqual(sum(size for _, size in self.mirror.requests), len(self.data) - kept)
        self.assertGreaterEqual(seen[0], kept)

    def test_a_record_for_another_archive_size_is_ignored(self):
        self.fetch()
        sidecar = download.sidecar_path(self.partial)
        state = json.loads(sidecar.read_text())
        sidecar.write_text(json.dumps(dict(state, bytes=state['bytes'] + 1)))
        self.assertEqual(download.resume_state(self.partial, len(self.data)), (set(), {}))
        sidecar.write_text(json.dumps(dict(state, done=[99])))
        self.assertEqual(download.resume_state(self.partial, len(self.data)), (set(), {}))
        sidecar.write_text(json.dumps(dict(state, done=[], started={'0': download.CHUNK + 1})))
        self.assertEqual(download.resume_state(self.partial, len(self.data)), (set(), {}))

    def test_cancel_keeps_the_partial_file(self):
        self.cancel.set()
        with self.assertRaises(download.Cancelled): self.fetch()
        self.assertTrue(self.partial.exists())

    def test_mirror_without_ranges_is_reported_for_fallback(self):
        self.mirror.ranges = False
        with self.assertRaises(download.RangeUnsupported): self.fetch()
        download.download_archive(self.entry, self.partial, self.cancel, lambda size: None)
        self.assertEqual(self.partial.read_bytes(), self.data)
        self.assertFalse(download.sidecar_path(self.partial).exists())

    def test_official_link_is_also_tried_through_each_known_mirror(self):
        official = 'https://sourceforge.net/projects/waydroid/files/images/system.zip/download'
        candidates = download.mirror_candidates(official)
        self.assertEqual(candidates[0], official)
        self.assertEqual(candidates[1:], [official + '?use_mirror=' + name for name in download.MIRRORS])
        self.assertEqual(download.mirror_candidates(self.entry['url']), [self.entry['url']])
        self.assertEqual(download.mirror_candidates(official + '?x=1'), [official + '?x=1'])

    def test_connections_go_to_the_mirror_that_delivers_fastest(self):
        slow = self.mirror.entry('slow.zip', self.data)['url']
        self.mirror.slow.add('/slow.zip')
        fast, self.entry['url'] = self.entry['url'], slow  # the official link leads to the slow mirror
        with mock.patch.object(download, 'mirror_candidates', lambda url: [url, fast]), \
                mock.patch.object(download, 'PROBE_SECONDS', 0.5):
            self.fetch()
        self.assertEqual(self.partial.read_bytes(), self.data)
        self.assertEqual(self.mirror.paths.count('/slow.zip'), 1)  # compared once, never used

    def test_a_chosen_mirror_that_stops_answering_is_left_for_the_official_link(self):
        gone = self.entry['url'].replace('image.zip', 'gone.zip')
        with mock.patch.object(download, 'fastest_mirrors', lambda *args: [gone]):
            self.fetch()
        self.assertEqual(self.partial.read_bytes(), self.data)

    def test_cancel_during_the_mirror_comparison_stops_the_download(self):
        self.mirror.slow.add('/image.zip')
        threading.Timer(0.2, self.cancel.set).start()
        with mock.patch.object(download, 'mirror_candidates', lambda url: [url, url]):
            started = time.monotonic()
            with self.assertRaises(download.Cancelled): self.fetch()
        self.assertLess(time.monotonic() - started, 2)


class CacheRunTest(unittest.TestCase):
    def archive(self, kind, size):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as output:
            output.writestr(kind + '.img', os.urandom(size))
        return buffer.getvalue()

    def setUp(self):
        self.mirror = Mirror()
        self.addCleanup(self.mirror.close)
        directory = tempfile.TemporaryDirectory(prefix='anvil-download-test-')
        self.addCleanup(directory.cleanup)
        cache = download.Downloads.__new__(download.Downloads)
        cache.root = Path(directory.name)
        cache.lock, cache.cancelled, cache.blocked = threading.RLock(), threading.Event(), None
        cache.state_path, cache.library_path, cache.library = cache.root / 'download.json', cache.root / 'library.json', {}
        cache.state = {'status': 'Downloading', 'owner_uid': os.getuid(), 'images': {}}
        self.cache = cache
        self.variant = {'flavor': 'VANILLA',
                        'system': self.mirror.entry('system.zip', self.archive('system', download.CHUNK * 3 + 7)),
                        'vendor': self.mirror.entry('vendor.zip', self.archive('vendor', download.CHUNK + 5))}

    def test_failed_run_resumes_and_publishes_verified_archives(self):
        stale = self.cache.root / ('.partial-' + 'f' * 64)
        stale.write_bytes(b'left over from an older image')
        self.mirror.budget = download.CHUNK * 2
        self.cache.run(self.variant, 2)
        self.assertEqual(self.cache.state['status'], 'Failed')
        self.assertIn('retry to resume', self.cache.state['error'])
        self.assertFalse(stale.exists())
        self.assertTrue(self.cache.partial(self.variant['system']).exists())
        self.mirror.budget = None
        self.cache.state.update(status='Downloading', error=None)
        self.cache.run(self.variant, 2)
        self.assertEqual(self.cache.state['status'], 'Ready', self.cache.state.get('error'))
        for kind in ('system', 'vendor'):
            entry = self.variant[kind]
            self.assertEqual((self.cache.root / (entry['sha256'] + '.zip')).read_bytes(),
                             self.mirror.files['/' + kind + '.zip'])
        self.assertEqual(list(self.cache.root.glob('.partial-*')), [])

    def test_corrupt_archive_is_discarded_instead_of_resumed(self):
        self.variant['system']['sha256'] = '0' * 64
        self.cache.run(self.variant, 2)
        self.assertEqual(self.cache.state['status'], 'Failed')
        self.assertIn('SHA-256', self.cache.state['error'])
        self.assertEqual(list(self.cache.root.glob('.partial-*')), [])

    def image(self, key, owner, system, vendor):
        for sha in (system, vendor): (self.cache.root / (sha + '.zip')).write_bytes(b'archive')
        self.cache.library[key] = {'library_id': key, 'owner_uid': owner, 'status': 'Ready', 'flavor': 'VANILLA',
                                   'images': {'system': {'sha256': system}, 'vendor': {'sha256': vendor}}}

    def archives(self):
        return sorted(path.name[0] for path in self.cache.root.glob('*.zip'))

    def test_deleting_an_image_keeps_archives_another_image_shares(self):
        me, other = os.getuid(), os.getuid() + 1
        self.image('mine', me, 'a' * 64, 'b' * 64)
        self.image('second', me, 'c' * 64, 'b' * 64)
        self.image('theirs', other, 'd' * 64, 'e' * 64)
        self.cache.state = dict(self.cache.library['mine'])
        with self.assertRaisesRegex(RuntimeError, 'not found'): self.cache.delete(me, 'theirs', {})
        with self.assertRaisesRegex(RuntimeError, 'Phone still has to be set up'):
            self.cache.delete(me, 'mine', {'a' * 64: 'Phone'})
        self.assertEqual(self.archives(), ['a', 'b', 'c', 'd', 'e'])
        status = self.cache.delete(me, 'mine', {'b' * 64: 'Tablet'})  # the shared archive stays for Tablet
        self.assertEqual(self.archives(), ['b', 'c', 'd', 'e'])
        self.assertEqual([item['library_id'] for item in status['available_images']], ['second'])
        self.assertEqual(status['status'], 'Idle')
        self.assertEqual(sorted(json.loads(self.cache.library_path.read_text())), ['second', 'theirs'])

    def test_download_in_progress_keeps_the_cache_as_it_is(self):
        self.image('mine', os.getuid(), 'a' * 64, 'b' * 64)
        with self.assertRaisesRegex(RuntimeError, 'Wait for'): self.cache.delete(os.getuid(), 'mine', {})
        with self.assertRaisesRegex(RuntimeError, 'Wait for'): self.cache.clear(os.getuid(), {})
        self.assertEqual(self.archives(), ['a', 'b'])

    def test_clearing_removes_unfinished_downloads_and_keeps_every_image(self):
        me, other = os.getuid(), os.getuid() + 1
        self.image('mine', me, 'a' * 64, 'b' * 64)
        self.image('theirs', other, 'd' * 64, 'b' * 64)
        # 'f': the system half of a pair whose vendor download never finished.
        for name in ('f' * 64 + '.zip', '9' * 64 + '.zip', '.partial-' + '1' * 64, '.partial-' + '1' * 64 + '.json'):
            (self.cache.root / name).write_bytes(b'left over')
        self.cache.state = {'status': 'Cancelled', 'owner_uid': me, 'error': 'Download cancelled'}
        self.assertEqual(sorted(path.name[:9] for path in self.cache.unfinished({'9' * 64: 'Old device'})),
                         ['.partial-', '.partial-', 'fffffffff'])
        self.cache.clear(me, {'9' * 64: 'Old device'})
        self.assertEqual(self.archives(), ['9', 'a', 'b', 'd'])
        self.assertEqual(list(self.cache.root.glob('.partial-*')), [])
        self.assertEqual(sorted(self.cache.library), ['mine', 'theirs'])
        self.assertEqual(self.cache.state['status'], 'Idle')
        self.assertEqual(self.cache.unfinished(), [self.cache.root / ('9' * 64 + '.zip')])

    def test_clearing_keeps_the_selected_ready_image(self):
        self.image('mine', os.getuid(), 'a' * 64, 'b' * 64)
        self.cache.state = dict(self.cache.library['mine'])
        self.cache.clear(os.getuid(), {})
        self.assertEqual((self.cache.state['status'], self.archives()), ('Ready', ['a', 'b']))

if __name__ == '__main__':
    unittest.main()
