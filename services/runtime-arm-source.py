"""Collect ARM payloads as the desktop user, before crossing the controller boundary.

No installer scripts are executed. The controller must independently validate
the resulting archive before publishing an Android overlay.
"""
import hashlib
import os
import re
import stat
import struct
import sys
import tempfile
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
# Release facts and patch offsets checked against waydroid-helper/extensions
# a2c9f98548a9f1aa9cf25f1ea56347e1e4930e29. No upstream shell code is run.
def _release(engine, version, revision, digest, android, cpu, *, variant=None,
             variant_sha256=None, patches=(), patch_sha256=None):
    repository = ('vendor_google_proprietary_ndk_translation-prebuilt' if engine == 'libndk'
                  else 'vendor_intel_proprietary_houdini')
    result = {'engine': engine, 'version': version, 'label': engine + ' / ' + version,
              'revision': revision, 'sha256': variant_sha256 or digest,
              'url': 'https://codeload.github.com/supremegamers/' + repository + '/zip/' + revision,
              'android': 'Android ' + str(android) + '; ' + cpu,
              'android_sdks': [30 if android == 11 else 33], 'cpu': cpu,
              'source_name': 'supremegamers GitHub', 'experimental': True}
    if engine == 'libndk': result['library_version'] = '0.2.3'
    if variant:
        result.update(variant=variant, upstream_sha256=digest, patches=list(patches),
                      patch_sha256=patch_sha256)
    return result


CATALOG = [
    _release('libndk', 'Zork R136-16238.47.0 (0.2.3)',
             '68734c52556d3d7a6db34c603dd9276915c29f2f', SOURCE_SHA256, 13, 'Intel / AMD'),
    _release('libndk', 'Zork R125-15853.53.0 (0.2.3)',
             'faece8cc787a520193545116501cad40534063fc',
             '235b31e75cd62fe18a6d11f4772df096c767d0d0252ef7eb7a2aeb56448ba915', 13, 'Intel / AMD'),
    _release('libndk', 'Guybrush R119-15633.69.0 (patched)',
             '0c6b0aad45498bbdb22eb1311b145d08ff4ce1fc',
             '6733a22db198acccead8b41074e51751c9d4831039cb0df6c6309a6dc92b3196', 11, 'Intel / AMD',
             variant='ndk-guybrush-r119-patched',
             variant_sha256='362f1ed508399f43bd7df984aa06c6ce76046c3aba0f4e356e092bf8d9618bd8',
             patches=((0x20dd21, '83e2fa', '83e2ff'), (0x20dc26, '83e2fa', '83e2ff')),
             patch_sha256='8cbff44e42f10f40c2c9823763c89acbac432323c0a1adb5bf1f2124d0ee1f0f'),
    _release('libndk', 'Guybrush R119-15633.69.0',
             '0c6b0aad45498bbdb22eb1311b145d08ff4ce1fc',
             '6733a22db198acccead8b41074e51751c9d4831039cb0df6c6309a6dc92b3196', 11, 'Intel / AMD'),
    _release('libhoudini', 'HPE 14.0.0 GoogleGame com1.3 (patched)',
             '2f8f088671182e17e67321e098e8411a3972a628',
             '2e82cdc88ddc4d418f7fb861aeebb7c49c83a91b97f57da10510b5e1146a4ed5', 13, 'Intel',
             variant='houdini-hpe-14-com1.3-patched',
             variant_sha256='9291685c7a99bf870f6b650f3d13a779f491be4187e7f0f620d35bb4ede1db1f',
             patches=((0xe61d5, '0f83680d0000', '909090909090'),),
             patch_sha256='da7448b3531865034e0e36425f18c4b55bf70f632518a272d469804370c13232'),
    _release('libhoudini', 'HPE 14.0.0b GoogleGame com1.2 (patched)',
             'debc3dc91cf12b5c5b8a1c546a5b0b7bf7f838a8',
             '7c78abbcced1f7c5c1cc794a0afb4010a2e5bb0bb18eadd6de3af2fe13e9dc25', 13, 'Intel',
             variant='houdini-hpe-14-com1.2-patched',
             variant_sha256='ba3a9e435735c0e859fbdb6cc702cdb8ff06d34277b79f48eb950b57c5757baa',
             patches=((0xe6085, '0f83680d0000', '909090909090'),),
             patch_sha256='847be255d03886974ff0cff09d09b7530301d24671ce0f80172ab3a0629d23b8'),
    _release('libhoudini', 'HPE 14.0.0b GoogleGame com1.0',
             '7e21ea3f63bd89e9e8af54e32da41bd8b65c93a1',
             '2459a1d39ffde2d19ce3d45196f2d92c222f7ba09db2048f26449eb310c3bd84', 13, 'Intel'),
    _release('libhoudini', 'Octopus 39598', '9ae65e9231a3103a25c18ff57b57e4870430d7b4',
             '213cd55a73f9c0f5290d5d99d600aeddc74da279b81f8d1b8b21206f9b4314e5', 13, 'Intel'),
    _release('libhoudini', 'Octopus 39489', '5643480a2c504bd0ee4739a6354fefc70d032ad4',
             '8464456aa108ee50205ed9e530b109cb4ebdc603f0b41471de5867cd13b1dfc3', 13, 'Intel'),
    _release('libhoudini', 'Brya 38795 (patched)', '0488b666dfdb5378b98067aea50b4508db12ce3a',
             '433641b72cd69d233d35c048082987db838ddd850b9ad457141df2c8a1494b4f', 11, 'Intel / AMD',
             variant='houdini-brya-38795-patched',
             variant_sha256='3d09c3f14d9a7794714659ed732a0acfe938ed7c8a8d716279b84ed0bdfbe8ae',
             patches=((0x30a5b1, '48b8fbffffff', '48b8ffffffff'), (0x30dea0, 'e8db04eeff', '9090909090')),
             patch_sha256='7211435eb3a0b38382e906e0388f47661017920c5e89942773df811a2ef3745f'),
    _release('libhoudini', 'Brya 38795', '0488b666dfdb5378b98067aea50b4508db12ce3a',
             '433641b72cd69d233d35c048082987db838ddd850b9ad457141df2c8a1494b4f', 11, 'Intel / AMD'),
    _release('libhoudini', 'WSA11 38765 (patched)', 'cf7f970f6004f0c329b0464e3d65f9b0e2baea91',
             'dc4e559acdc7d6c42d7d0e44c97e268074d23a4789f59c218a12ee5509aa774c', 11, 'Intel / AMD',
             variant='houdini-wsa11-38765-patched',
             variant_sha256='3e4c1a57898eeecf3c7bb397610bf18c412dba9b460a325d1f769cfbab439678',
             patches=((0x3062a5, '48b8fbffffff', '48b8ffffffff'), (0x3099d6, '83e0fb', '83e0ff'),
                      (0x309b42, 'e8892feeff', '9090909090')),
             patch_sha256='37add0f39f2c249c01466b00aa8d220ba101a1d7d199b1c2e34ed916757d47fc'),
    _release('libhoudini', 'WSA11 38765', 'cf7f970f6004f0c329b0464e3d65f9b0e2baea91',
             'dc4e559acdc7d6c42d7d0e44c97e268074d23a4789f59c218a12ee5509aa774c', 11, 'Intel / AMD'),
    # Preserve the identity of packages imported before variant support.
    _release('libhoudini', 'HPE 14.0.0 GoogleGame com1.3 (legacy, unpatched)',
             '2f8f088671182e17e67321e098e8411a3972a628',
             '2e82cdc88ddc4d418f7fb861aeebb7c49c83a91b97f57da10510b5e1146a4ed5', 13, 'Intel'),
]


def mark_variant(blob, variant):
    """Give a recipe a stable archive identity without recompressing any files."""
    comment = ('AnvilDroid ARM variant v1: ' + variant).encode('ascii')
    end = blob.rfind(b'PK\x05\x06', max(0, len(blob) - 65557))
    if (len(blob) > MAX_ARCHIVE or end < 0 or end + 22 > len(blob)
            or end + 22 + struct.unpack_from('<H', blob, end + 20)[0] != len(blob)
            or len(comment) > 65535):
        raise ValueError('Invalid ARM variant archive')
    marked = blob[:end + 20] + struct.pack('<H', len(comment)) + comment
    if len(marked) > MAX_ARCHIVE: raise ValueError('ARM variant exceeds size limit')
    return marked


def patch_library(data, metadata):
    """Apply only the compiled-in recipe to an archive with a known checksum."""
    patched = bytearray(data)
    for offset, before, after in metadata['patches']:
        before, after = bytes.fromhex(before), bytes.fromhex(after)
        if offset < 0 or not before or len(before) != len(after) or patched[offset:offset + len(before)] != before:
            raise RuntimeError('ARM compatibility patch byte mismatch; nothing was installed')
        patched[offset:offset + len(before)] = after
    if hashlib.sha256(patched).hexdigest() != metadata['patch_sha256']:
        raise RuntimeError('ARM patched library checksum mismatch; nothing was installed')
    return bytes(patched)


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
        if entry.get('variant'):
            with tempfile.TemporaryFile() as temporary:
                download(entry['url'], temporary, entry['upstream_sha256'])
                temporary.seek(0)
                blob = temporary.read(MAX_ARCHIVE + 1)
            if hashlib.sha256(blob).hexdigest() != entry['upstream_sha256']:
                raise ValueError('ARM upstream archive SHA-256 mismatch')
            marked = mark_variant(blob, entry['variant'])
            if hashlib.sha256(marked).hexdigest() != entry['sha256']:
                raise ValueError('ARM variant archive SHA-256 mismatch')
            output.write(marked)
        else:
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
