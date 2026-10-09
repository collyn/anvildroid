"""Run: python3 native/input/scrcpy-control-test.py"""
import unittest
from scrcpy_control import server_arguments, socket_name, touch_packet, verify_handshake


class ProtocolTest(unittest.TestCase):
    def test_packet_matches_scrcpy_331_layout(self):
        expected = bytes.fromhex(
            '02 00 0000000000000003 00000123 00000234 0780 0438 ffff 00000000 00000000')
        self.assertEqual(touch_packet(0, 3, 0x123, 0x234, 1920, 1080), expected)
        up = touch_packet(1, 3, 0x123, 0x234, 1920, 1080)
        self.assertEqual(len(up), 32)
        self.assertEqual(up[22:24], b'\0\0')

    def test_invalid_or_unrepresentable_events(self):
        for args in [(3, 0, 1, 1, 20, 20), (0, -1, 1, 1, 20, 20),
                     (0, 10, 1, 1, 20, 20), (0, 0, 20, 1, 20, 20),
                     (0, 0, -1, 1, 20, 20), (0, 0, 1, 1, 0, 20),
                     (0, 0, 1, 1, 8193, 20), (True, 0, 1, 1, 20, 20)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                touch_packet(*args)

    def test_control_only_and_fixed_width_socket_name(self):
        self.assertEqual(socket_name(1), '\0scrcpy_00000001')
        args = server_arguments(1)
        for flag in ['video=false', 'audio=false', 'control=true',
                     'clipboard_autosync=false', 'power_on=false']:
            self.assertIn(flag, args)
        for scid in [-1, 0x80000000, True, '1']:
            with self.assertRaises(ValueError):
                socket_name(scid)

    def test_peer_rejected_before_read(self):
        import struct
        class Peer:
            def getsockopt(self, *args):
                return struct.pack('3i', 42, 2000, 2000)
            def recv(self, count):
                raise AssertionError('Must not read from unverified process')
        with self.assertRaises(PermissionError):
            verify_handshake(Peer(), 43)

    def test_handshake_eof_is_failure(self):
        import struct
        class Peer:
            def getsockopt(self, *args):
                return struct.pack('3i', 42, 2000, 2000)
            def recv(self, count):
                return b''
        with self.assertRaises(ConnectionError):
            verify_handshake(Peer(), 42)


if __name__ == '__main__':
    unittest.main()
