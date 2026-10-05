"""Bounded Android sparse decoding and read-only filesystem selection."""
import struct
import zlib

BLOCK = 1024 * 1024


def filesystem(header):
    if header[1024:1028] == b'\xe2\xe1\xf5\xe0': return 'erofs'
    if header[1080:1082] == b'\x53\xef': return 'ext4'
    raise RuntimeError('Unknown image filesystem. Select system.img/vendor.img, not super.img or a compressed archive.')


def mount_options(path):
    with open(path, 'rb') as stream: kind = filesystem(stream.read(4096))
    return ('-t', kind, '-o', 'loop,ro,noload' if kind == 'ext4' else 'loop,ro')


def image_stream(source, size, limit, check):
    """Return expanded size and chunks; do not trust sparse output lengths."""
    def read(length):
        value = source.read(length)
        if len(value) != length: raise RuntimeError('Truncated Android image')
        return value
    prefix = read(min(size, 28))
    if prefix[:4] != b'\x3a\xff\x26\xed':
        if not 0 < size <= limit: raise RuntimeError('Image exceeds size limit')
        def raw():
            check()
            first = prefix + read(min(size - len(prefix), BLOCK - len(prefix)))
            filesystem(first)
            yield first
            remaining = size - len(first)
            while remaining:
                check()
                chunk = read(min(remaining, BLOCK)); remaining -= len(chunk)
                yield chunk
        return size, raw()
    if len(prefix) != 28: raise RuntimeError('Truncated sparse header')
    _, major, minor, file_header, chunk_header, block_size, blocks, chunks, checksum = struct.unpack('<I4H4I', prefix)
    if (major != 1 or minor != 0 or not 28 <= file_header <= 4096 or not 12 <= chunk_header <= 4096
            or not 4096 <= block_size <= BLOCK or block_size % 4 or not 0 < blocks * block_size <= limit
            or not 0 < chunks <= 1000000):
        raise RuntimeError('Invalid or oversized Android sparse image')
    read(file_header - 28)
    def sparse():
        consumed, produced, crc = file_header, 0, 0
        for _ in range(chunks):
            check()
            header = read(chunk_header); consumed += chunk_header
            kind, reserved, count, total = struct.unpack('<2H2I', header[:12])
            length = count * block_size
            if produced + length > blocks * block_size: raise RuntimeError('Sparse image exceeds declared size')
            payload = total - chunk_header
            if kind == 0xcac4:
                if count or payload != 4: raise RuntimeError('Invalid sparse checksum chunk')
                if struct.unpack('<I', read(4))[0] != crc: raise RuntimeError('Sparse image checksum mismatch')
                consumed += 4
                continue
            expected = length if kind == 0xcac1 else 4 if kind == 0xcac2 else 0 if kind == 0xcac3 else -1
            if not length or payload != expected or expected < 0: raise RuntimeError('Invalid sparse chunk')
            fill = read(4) if kind == 0xcac2 else b'\0' * 4
            remaining = length
            while remaining:
                check()
                amount = min(BLOCK, remaining)
                chunk = read(amount) if kind == 0xcac1 else fill * (amount // 4)
                if not produced: filesystem(chunk)
                crc = zlib.crc32(chunk, crc)
                yield chunk
                produced += amount; remaining -= amount
            consumed += payload
        if produced != blocks * block_size or consumed != size: raise RuntimeError('Incomplete or trailing sparse image data')
        if checksum and checksum != crc: raise RuntimeError('Sparse image checksum mismatch')
    return blocks * block_size, sparse()
