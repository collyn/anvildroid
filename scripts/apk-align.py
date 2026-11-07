#!/usr/bin/env python3
"""Align stored APK entries to 4 bytes, or check an APK's alignment.

Android 11+ rejects packages targeting SDK 30+ whose resources.arsc is not
stored uncompressed on a 4-byte boundary. Some apksigner builds align while
signing and others preserve the input layout, so the IME build aligns before
signing and checks the signed result instead of relying on either behaviour.
"""
import struct
import sys
import zipfile


def misaligned(path, names=('resources.arsc',)):
    """Return entries in names that are compressed or not 4-byte aligned.

    names=None checks every stored entry, as zipalign would.
    """
    bad = []
    with open(path, 'rb') as stream, zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if names is not None and info.filename not in names:
                continue
            if info.compress_type != zipfile.ZIP_STORED:
                if names is not None:
                    bad.append(info.filename)
                continue
            stream.seek(info.header_offset)
            header = stream.read(30)
            if header[:4] != b'PK\x03\x04':
                raise ValueError('Invalid local header: ' + info.filename)
            name_length, extra_length = struct.unpack('<HH', header[26:30])
            if (info.header_offset + 30 + name_length + extra_length) % 4:
                bad.append(info.filename)
    return bad


def align(source, destination):
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(destination, 'w') as output:
        for info in archive.infolist():
            entry = zipfile.ZipInfo(info.filename, info.date_time)
            entry.external_attr = info.external_attr
            entry.compress_type = zipfile.ZIP_STORED if info.filename == 'resources.arsc' else info.compress_type
            if entry.compress_type == zipfile.ZIP_STORED:
                # zipalign-style zero padding in the local header extra field.
                start = output.fp.tell() + 30 + len(entry.filename.encode())
                entry.extra = b'\0' * (-start % 4)
            output.writestr(entry, archive.read(info))
    bad = misaligned(destination, None)
    if bad:
        raise RuntimeError('Could not align APK entries: ' + ', '.join(bad))


def main(argv):
    if len(argv) == 3 and argv[0] == 'align':
        align(argv[1], argv[2])
    elif len(argv) == 2 and argv[0] == 'check':
        bad = misaligned(argv[1])
        if bad:
            sys.exit(argv[1] + ': entries must be stored and 4-byte aligned: ' + ', '.join(bad))
    else:
        sys.exit('usage: apk-align.py align IN OUT | check APK')


if __name__ == '__main__':
    main(sys.argv[1:])
