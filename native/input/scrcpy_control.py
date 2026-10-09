"""Pinned scrcpy 3.3.1 control protocol for diagnostics, not production routing.

Socket delivery is not an Android acknowledgement. Stock scrcpy cannot satisfy
touch-session's CANCEL/reset contract, so this module must not be installed as
its sink until the receiver lifecycle and task routing gates are implemented.
"""
import socket
import struct

VERSION = '3.3.1'
SERVER_SHA256 = 'a0f70b20aa4998fbf658c94118cd6c8dab6abbb0647a3bdab344d70bc1ebcbb8'
DIAGNOSTIC_SHA256 = 'da318a3948eecddf5d7780a84b27b3cd0c2d41f4266963c81203d3426eb691d3'
TOUCH_PACKET = struct.Struct('>BBQIIHHHII')


def server_arguments(scid):
    if type(scid) is not int or not 0 <= scid <= 0x7fffffff:
        raise ValueError('Invalid scrcpy session ID')
    return [VERSION, f'scid={scid:08x}', 'tunnel_forward=true',
            'video=false', 'audio=false', 'control=true',
            'clipboard_autosync=false', 'power_on=false', 'cleanup=false',
            'send_device_meta=false', 'send_dummy_byte=true']


def socket_name(scid):
    server_arguments(scid)
    return f'\0scrcpy_{scid:08x}'


def touch_packet(action, pointer_id, x, y, width, height):
    """Encode an Android display-space finger event; no implicit window scaling.

    DOWN=0, UP=1, MOVE=2. Positive IDs avoid scrcpy's special mouse/finger IDs.
    Caller owns pointer transitions, verified focus, geometry and lifetime.
    CANCEL is deliberately rejected: it does not reset stock server pointers.
    """
    values = (action, pointer_id, x, y, width, height)
    if any(type(value) is not int for value in values):
        raise ValueError('Touch fields must be integers')
    if action not in (0, 1, 2) or not 0 <= pointer_id < 10:
        raise ValueError('Unsupported action or pointer ID')
    if not (1 <= width <= 8192 and 1 <= height <= 8192 and
            0 <= x < width and 0 <= y < height):
        raise ValueError('Touch outside verified Android display bounds')
    return TOUCH_PACKET.pack(2, action, pointer_id, x, y, width, height,
                             0 if action == 1 else 65535, 0, 0)


def verify_handshake(connection, expected_pid, expected_uid=2000):
    """Caller connects in the pinned Android network namespace with a timeout.

    Require the supervised process identity, not only a random socket name.
    This validates the receiver; stock scrcpy still needs sender authentication
    for a production deployment with untrusted Android applications.
    """
    if expected_pid <= 0 or expected_uid < 0:
        raise ValueError('Invalid expected peer')
    peer = struct.unpack('3i', connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
    if peer[:2] != (expected_pid, expected_uid):
        raise PermissionError('scrcpy receiver identity changed')
    if connection.recv(1) != b'\0':
        raise ConnectionError('Invalid scrcpy control handshake')
    return peer
