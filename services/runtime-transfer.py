"""Bounded JSON + one caller-opened regular file; no privileged path lookup."""
import array
import fcntl
import json
import os
import socket
import stat

MAX_APK = 2 * 1024 * 1024 * 1024

def apk_size(fd):
    if fd is None: raise ValueError('APK file descriptor required')
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_APK:
        raise ValueError('APK must be a regular file between 1 byte and 2 GiB')
    if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY:
        raise ValueError('APK descriptor must be readable')
    if os.pread(fd, 4, 0) != b'PK\x03\x04': raise ValueError('File is not an APK archive')
    return info.st_size

def receive(connection, limit=16384):
    descriptors = []
    try:
        data = bytearray()
        while b'\n' not in data:
            part, ancillary, flags, _ = connection.recvmsg(min(4096, limit + 1 - len(data)), socket.CMSG_SPACE(16 * array.array('i').itemsize), socket.MSG_CMSG_CLOEXEC)
            for level, kind, raw in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    values = array.array('i')
                    values.frombytes(raw[:len(raw) - len(raw) % values.itemsize])
                    descriptors.extend(values)
            if flags & socket.MSG_CTRUNC or len(descriptors) > 2: raise ValueError('Too many file descriptors')
            if not part: raise ValueError('Incomplete request')
            data.extend(part)
            if len(data) > limit: raise ValueError('Request too large')
        if not data.endswith(b'\n') or b'\n' in data[:-1]: raise ValueError('One request per connection required')
        request = json.loads(data)
        if isinstance(request, dict) and request.get('op') == 'image_import_folder':
            if len(descriptors) != 2: raise ValueError('Folder import requires system and vendor descriptors')
            result = tuple(descriptors)
            descriptors.clear()
            return request, result
        if len(descriptors) > 1: raise ValueError('Expected at most one file descriptor')
        return request, descriptors.pop() if descriptors else None
    finally:
        for fd in descriptors: os.close(fd)

def send(connection, payload, fd):
    data = json.dumps(payload).encode() + b'\n'
    count = connection.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])
    if count < len(data): connection.sendall(data[count:])
