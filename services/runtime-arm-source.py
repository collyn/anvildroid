"""Collect ARM payloads as the desktop user, before crossing the controller boundary.

No installer scripts are executed. The controller must independently validate
the resulting archive before publishing an Android overlay.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.parse
import urllib.request
import zipfile

MAX_ARCHIVE = 256 * 1024**2
MAX_EXPANDED = 512 * 1024**2
MAX_FILE = 128 * 1024**2
MAX_ENTRIES = 4096
CHUNK = 1024 * 1024
SOURCE_URL = ('https://codeload.github.com/supremegamers/'
              'vendor_google_proprietary_ndk_translation-prebuilt/zip/'
              '68734c52556d3d7a6db34c603dd9276915c29f2f')
SOURCE_SHA256 = 'a142d1586c9eafb5edf62277110f2128bc03066179a6783bcaa33ee322e1cbd0'
CATALOG = [
    {'engine': 'libndk', 'version': '0.2.3', 'label': 'libndk_translation 0.2.3',
     'sha256': SOURCE_SHA256, 'url': SOURCE_URL, 'android': 'Android 13', 'experimental': True},
    {'engine': 'libhoudini', 'version': '14.0.0 GoogleGame com1.3',
     'label': 'libhoudini 14.0.0 GoogleGame com1.3',
     'sha256': '2e82cdc88ddc4d418f7fb861aeebb7c49c83a91b97f57da10510b5e1146a4ed5',
     'url': 'https://codeload.github.com/supremegamers/vendor_intel_proprietary_houdini/zip/2f8f088671182e17e67321e098e8411a3972a628',
     'android': 'Android 13 community package; Intel x86 CPU', 'experimental': True},
]


def https_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError('Invalid ARM download URL') from error
    if (not isinstance(value, str) or len(value) > 8192
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or parsed.scheme != 'https' or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or port == 0):
        raise ValueError('Use a direct HTTPS archive URL without credentials or a fragment')
    return value


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return super().redirect_request(request, fp, code, message, headers, https_url(url))


def copy_bounded(source, output, limit, deadline=None):
    digest = hashlib.sha256()
    size = 0
    while True:
        if deadline is not None and time.monotonic() > deadline:
            raise RuntimeError('ARM download timed out; retry when the connection is available')
        block = source.read(min(CHUNK, limit - size + 1))
        if not block:
            break
        size += len(block)
        if size > limit:
            raise ValueError('ARM payload exceeds its size limit')
        output.write(block)
        digest.update(block)
    if size == 0:
        raise ValueError('ARM payload is empty')
    return size, digest.hexdigest()


def download(url, output, expected_sha256=''):
    if expected_sha256 and not re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha256):
        raise ValueError('SHA-256 must contain exactly 64 hexadecimal characters')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), HTTPSRedirect())
    request = urllib.request.Request(https_url(url), headers={'User-Agent': 'AnvilDroid/0.1'})
    deadline = time.monotonic() + 180
    with opener.open(request, timeout=15) as response:
        https_url(response.geturl())
        if response.status != 200:
            raise RuntimeError('ARM archive server did not return a complete file')
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isdecimal() or not 0 < int(length) <= MAX_ARCHIVE):
            raise ValueError('ARM archive size is invalid or exceeds 256 MiB')
        size, digest = copy_bounded(response, output, MAX_ARCHIVE, deadline)
        if length is not None and size != int(length):
            raise RuntimeError('ARM download was interrupted; retry')
    if expected_sha256 and digest != expected_sha256.lower():
        raise ValueError('ARM archive SHA-256 mismatch; nothing was installed')
    return digest


def regular_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARCHIVE:
            raise ValueError('Choose a regular ARM archive between 1 byte and 256 MiB')
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def pack_folder(path, output):
    """Walk held directory descriptors; never follow a link after enumeration."""
    total = 0
    count = 0
    root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            def visit(directory, prefix='', depth=0):
                nonlocal total, count
                if depth > 16:
                    raise ValueError('ARM folder is nested too deeply')
                before = os.fstat(directory)
                for name in sorted(os.listdir(directory)):
                    count += 1
                    if count > MAX_ENTRIES:
                        raise ValueError('ARM folder has too many entries')
                    if name in ('.', '..') or '\\' in name or any(ord(c) < 32 for c in name):
                        raise ValueError('Unsafe ARM folder filename')
                    child = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=directory)
                    try:
                        info = os.fstat(child)
                        relative = prefix + name
                        if stat.S_ISDIR(info.st_mode):
                            visit(child, relative + '/', depth + 1)
                        elif stat.S_ISREG(info.st_mode):
                            if info.st_size > MAX_FILE or total + info.st_size > MAX_EXPANDED:
                                raise ValueError('ARM folder exceeds its size limit')
                            entry = zipfile.ZipInfo(relative)
                            entry.compress_type = zipfile.ZIP_DEFLATED
                            entry.external_attr = (stat.S_IFREG | 0o644) << 16
                            with os.fdopen(os.dup(child), 'rb') as stream, archive.open(entry, 'w') as target:
                                # Empty metadata files are harmless; bridges are validated by the controller.
                                size = copy_bounded(stream, target, min(MAX_FILE, MAX_EXPANDED - total))[0] if info.st_size else 0
                            after = os.fstat(child)
                            if (size != info.st_size or info.st_mtime_ns != after.st_mtime_ns
                                    or info.st_ctime_ns != after.st_ctime_ns):
                                raise ValueError('ARM source changed during import; select it again')
                            total += size
                        else:
                            raise ValueError('ARM folders may contain only regular files and directories')
                    finally:
                        os.close(child)
                after = os.fstat(directory)
                if before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns:
                    raise ValueError('ARM source changed during import; select it again')
            visit(root)
        if total == 0:
            raise ValueError('ARM folder is empty')
        try:
            packed_size = output.tell()
        except (OSError, AttributeError):
            packed_size = None  # Controller enforces final descriptor size.
        if packed_size is not None and packed_size > MAX_ARCHIVE:
            raise ValueError('Packed ARM archive exceeds 256 MiB')
    finally:
        os.close(root)


def collect(kind, source, output, expected_sha256=''):
    if kind == 'builtin':
        entry = next((entry for entry in CATALOG if entry['sha256'] == source), None) if source else CATALOG[0]
        if entry is None:
            raise ValueError('Unknown ARM catalog version')
        download(entry['url'], output, entry['sha256'])
    elif kind == 'url':
        download(source, output, expected_sha256)
    elif kind == 'folder':
        pack_folder(source, output)
    elif kind == 'archive':
        with regular_file(source) as stream:
            copy_bounded(stream, output, MAX_ARCHIVE)
    else:
        raise ValueError('Unknown ARM source type')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 4:
            raise ValueError('Expected source type, source and optional SHA-256')
        collect(sys.argv[1], sys.argv[2], sys.stdout.buffer, sys.argv[3])
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
