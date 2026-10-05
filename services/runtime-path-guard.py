"""Axion 2.8 pathname compatibility for its pinned Android 16 ARM bridge."""
import hashlib
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile

BRIDGE_SHA256 = 'fbadc774c989534a567e6af8fd16d2c00727b1f1d9cc778bf538d6b59ed9776d'
LIBRARY = 'libanvildroid-path-guard.so'
# Same-length undefined names preserve Android packed-relocation symbol indices.
IMPORTS = {b'stat': b'asta', b'lstat': b'alsta', b'open': b'aopn',
           b'openat': b'aopnat', b'__open_2': b'__aopn_2'}


def sections(blob):
    if blob[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', blob, 18)[0] != 62:
        raise RuntimeError('Path guard requires x86_64 ELF')
    offset = struct.unpack_from('<Q', blob, 40)[0]
    size, count, names = struct.unpack_from('<HHH', blob, 58)
    if size != 64 or not 0 < count <= 128 or names >= count or offset + size * count > len(blob):
        raise RuntimeError('Invalid native bridge sections')
    entries = [struct.unpack_from('<IIQQQQIIQQ', blob, offset + size * n) for n in range(count)]
    table = entries[names]
    strings = blob[table[4]:table[4] + table[5]]
    return {strings[item[0]:].split(b'\0', 1)[0].decode('ascii'): item for item in entries}


def patch_imports(blob):
    if hashlib.sha256(blob).hexdigest() != BRIDGE_SHA256:
        raise RuntimeError('Unrecognized ARM bridge; path guard not applied')
    data = bytearray(blob)
    table = sections(blob)
    symbols, strings, versions = (table[name] for name in ('.dynsym', '.dynstr', '.gnu.version'))
    found = set()
    for index in range(symbols[5] // 24):
        offset, _, _, section, _, _ = struct.unpack_from('<IBBHQQ', data, symbols[4] + index * 24)
        start = strings[4] + offset
        end = data.index(0, start, strings[4] + strings[5])
        name = bytes(data[start:end])
        if section == 0 and name in IMPORTS:
            data[start:end] = IMPORTS[name]
            struct.pack_into('<H', data, versions[4] + index * 2, 1)
            found.add(name)
    if found != IMPORTS.keys():
        raise RuntimeError('Incomplete ARM bridge path imports')
    return bytes(data)


def verify_layout(original, patched):
    before, after = sections(original), sections(patched)
    # patchelf's rename operation reorders symbols without fixing APS2. Never
    # use it; only add a dependency and verify code/relocations and symbol order.
    for name in ('.text', '.rela.dyn', '.relr.dyn', '.rela.plt', '.gnu.hash'):
        a, b = before[name], after[name]
        if a[3] != b[3] or original[a[4]:a[4]+a[5]] != patched[b[4]:b[4]+b[5]]:
            raise RuntimeError('ELF tool changed native bridge code or relocations')
    def entries(blob, table):
        sym, strings = table['.dynsym'], table['.dynstr']
        data = blob[strings[4]:strings[4]+strings[5]]
        result = []
        for index in range(sym[5] // 24):
            offset, info, other, section, value, size = struct.unpack_from('<IBBHQQ', blob, sym[4]+index*24)
            end = data.index(0, offset)
            result.append((data[offset:end], info, other, bool(section), value, size))
        return result
    if entries(original, before) != entries(patched, after):
        raise RuntimeError('ELF tool changed native bridge symbol order')


def prepare(instance, system_root, assets):
    props = (system_root / 'system/build.prop').read_text()
    if not re.search(r'^ro.axion.device=', props, re.M) or not re.search(r'^ro.build.version.sdk=36$', props, re.M):
        return None
    bridge = system_root / 'system/lib64/libndk_translation.so'
    if not bridge.is_file() or bridge.stat().st_size > 16 * 1024**2:
        return None
    original = bridge.read_bytes()
    if hashlib.sha256(original).hexdigest() != BRIDGE_SHA256:
        return None  # Never rewrite an unknown native bridge.
    helper = assets / LIBRARY
    info = helper.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 128 * 1024:
        raise RuntimeError('Untrusted pathname compatibility helper')
    tool = shutil.which('patchelf')
    if not tool:
        raise RuntimeError('Axion ARM compatibility requires patchelf; reinstall the current AnvilDroid package')
    with tempfile.TemporaryDirectory(prefix='.path-guard-', dir=instance) as directory:
        output = Path(directory) / 'libndk_translation.so'
        renamed = patch_imports(original)
        output.write_bytes(renamed)
        result = subprocess.run([tool, '--add-needed', LIBRARY, str(output)],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError('Could not prepare Axion ARM pathname compatibility: ' + result.stderr[-300:])
        patched = output.read_bytes()
        verify_layout(renamed, patched)
        # Controller-owned copies are bind-mounted before Android starts. Image
        # files and user data remain untouched; stopping removes the mounts.
        library = instance / LIBRARY
        shutil.copyfile(helper, library)
        library.chmod(0o644)
        destination = instance / 'path-guard-ndk.so'
        shutil.copyfile(output, destination)
        destination.chmod(0o644)
    return destination, library
