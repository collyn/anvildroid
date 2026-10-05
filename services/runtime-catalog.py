"""Read-only official OTA discovery. Never downloads or activates Android images."""
import copy
import json
import platform
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = 'https://ota.waydro.id/'
CHANNELS = {
    'VANILLA': 'system/lineage/waydroid_x86_64/VANILLA.json',
    'GAPPS': 'system/lineage/waydroid_x86_64/GAPPS.json',
    'MAINLINE': 'vendor/waydroid_x86_64/MAINLINE.json',
}
MAX_METADATA = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        raise RuntimeError('Official OTA metadata unexpectedly redirected; check again later')


def fetch(channel):
    # No caller-selected URLs, proxies, redirects or unbounded metadata bodies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(BASE + CHANNELS[channel], timeout=10) as response:
        data = response.read(MAX_METADATA + 1)
    if len(data) > MAX_METADATA:
        raise RuntimeError('OTA metadata exceeds the size limit')
    return json.loads(data)


def latest(payload, channel):
    rows = payload.get('response') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise RuntimeError('Invalid or empty OTA image catalog')
    kind = 'vendor' if channel == 'MAINLINE' else 'system'
    directory = 'vendor/waydroid_x86_64' if kind == 'vendor' else 'system/lineage/waydroid_x86_64'
    entries = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError('Invalid OTA image entry')
        filename, version = row.get('filename'), row.get('version')
        if (not isinstance(filename, str) or not isinstance(version, str)
                or not re.fullmatch(r'\d+\.\d+', version)
                or not re.fullmatch(r'lineage-' + re.escape(version) + r'-\d{8}-' + channel + r'-waydroid_x86_64-' + kind + r'\.zip', filename)
                or row.get('romtype') != channel
                or type(row.get('datetime')) is not int or row['datetime'] <= 0
                or type(row.get('size')) is not int or not 0 < row['size'] <= 8 * 1024**3
                or not isinstance(row.get('id'), str) or not re.fullmatch(r'[0-9a-f]{64}', row['id'])
                or row.get('url') != f'https://sourceforge.net/projects/waydroid/files/images/{directory}/{filename}/download'):
            raise RuntimeError('OTA image source, checksum or metadata is invalid')
        entries.append({'filename': filename, 'version': version, 'sha256': row['id'],
                        'bytes': row['size'], 'published_at': row['datetime'], 'url': row['url']})
    return max(entries, key=lambda entry: entry['published_at'])


def discover():
    if platform.machine() != 'x86_64':
        raise RuntimeError('Image discovery currently supports x86_64 only')
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {channel: pool.submit(fetch, channel) for channel in CHANNELS}
        images = {channel: latest(task.result(), channel) for channel, task in tasks.items()}
    vendor = images['MAINLINE']
    return {'checked_at': int(time.time()), 'architecture': 'x86_64', 'source': BASE,
            'variants': [{'flavor': flavor, 'system': images[flavor], 'vendor': vendor,
                          'download_bytes': images[flavor]['bytes'] + vendor['bytes'],
                          'matching_versions': images[flavor]['version'] == vendor['version']}
                         for flavor in ('VANILLA', 'GAPPS')]}


class Catalog:
    """One bounded background check; status requests do not access the network."""
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {'status': 'Idle', 'report': None, 'error': None}
        self.last_attempt = None

    def request(self, refresh):
        if type(refresh) is not bool:
            raise RuntimeError('refresh must be a boolean')
        with self.lock:
            now = time.monotonic()
            if refresh and self.state['status'] != 'Checking':
                if self.last_attempt is not None and now - self.last_attempt < 30:
                    raise RuntimeError('Wait 30 seconds between image source checks')
                self.last_attempt = now
                self.state.update(status='Checking', error=None)
                threading.Thread(target=self.check, name='image-catalog', daemon=True).start()
            return copy.deepcopy(self.state)

    def check(self):
        try:
            report, error = discover(), None
        except Exception as failure:
            report, error = None, str(failure)[-512:]
        with self.lock:
            # Preserve the last successful report, explicitly marked stale by status.
            if report is not None:
                self.state['report'] = report
            self.state.update(status='Failed' if error else 'Ready', error=error)

    def selection(self, flavor, system_sha256, vendor_sha256):
        with self.lock:
            if self.state['status'] != 'Ready' or time.time() - self.state['report']['checked_at'] > 3600:
                raise RuntimeError('Check official images again before downloading')
            variant = next((item for item in self.state['report']['variants'] if item['flavor'] == flavor), None)
            if (variant is None or not variant['matching_versions']
                    or variant['system']['sha256'] != system_sha256 or variant['vendor']['sha256'] != vendor_sha256):
                raise RuntimeError('Selected images changed or are incompatible; check official images again')
            return copy.deepcopy(variant)
