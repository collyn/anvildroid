"""Session-scoped ADB relays. Host listeners bind only IPv4 loopback.

Managed workers forward through a root-only Unix socket into their private
network namespace. ADB authentication remains the guest's responsibility.
"""
import ipaddress
import os
import select
import socket
import struct
import threading
import time


def guest_address(output):
    for value in output.split():
        try:
            address = ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError:
            continue
        if address.is_private and not address.is_loopback and not address.is_unspecified:
            return str(address)
    raise RuntimeError('Android has no IPv4 address yet; wait for boot and retry')


def configure(run, enabled):
    # Session property only: never persist TCP debugging or disable auth.
    run('/system/bin/setprop', 'service.adb.tcp.port', '5555' if enabled else '-1')
    run('/system/bin/setprop', 'ctl.restart', 'adbd')


class Relay:
    def __init__(self, target, path=None, alive=lambda: True):
        self.target, self.path, self.alive = target, path, alive
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.sockets = set()
        self.clients = 0
        self.error = ''
        self.server = socket.socket(socket.AF_UNIX if path else socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.server.bind(str(path) if path else ('127.0.0.1', 0))
            if path: os.chmod(path, 0o600)
            self.server.listen(16)
            self.server.settimeout(.5)
        except Exception:
            self.server.close()
            raise
        self.endpoint = None if path else '127.0.0.1:' + str(self.server.getsockname()[1])
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        next_check = 0
        while not self.stopping.is_set():
            try:
                if time.monotonic() >= next_check:
                    if not self.alive(): break
                    next_check = time.monotonic() + 2
                client, _ = self.server.accept()
                if self.path:
                    _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != 0:
                        client.close()
                        continue
                with self.lock:
                    if self.clients >= 16:
                        client.close()
                        continue
                    self.clients += 1
                    self.sockets.add(client)
                threading.Thread(target=self.pump, args=(client,), daemon=True).start()
            except socket.timeout:
                continue
            except (OSError, RuntimeError) as error:
                if not self.stopping.is_set(): self.error = str(error)
                break
        self.stopping.set()
        self.server.close()
        self.close_clients()

    def pump(self, client):
        upstream = None
        try:
            upstream = self.target()
            with self.lock:
                self.sockets.add(upstream)
            client.setblocking(False)
            upstream.setblocking(False)
            self.error = ''
            eof = set()
            shutdown = set()
            buffers = {client: bytearray(), upstream: bytearray()}
            peers = {client: upstream, upstream: client}
            while not self.stopping.is_set():
                # Backpressure prevents stalled IDEs from growing root memory.
                reads = [s for s in peers if s not in eof and len(buffers[peers[s]]) < 262144]
                writes = [s for s in peers if buffers[s]]
                ready, writable, _ = select.select(reads, writes, [], .5)
                for source in ready:
                    try: data = source.recv(65536)
                    except BlockingIOError: continue
                    if not data: eof.add(source)
                    else: buffers[peers[source]].extend(data)
                for dest in writable:
                    try: sent = dest.send(buffers[dest])
                    except BlockingIOError: continue
                    del buffers[dest][:sent]
                for source in eof:
                    dest = peers[source]
                    if not buffers[dest] and dest not in shutdown:
                        dest.shutdown(socket.SHUT_WR)
                        shutdown.add(dest)
                if len(eof) == 2 and not any(buffers.values()): return
        except (OSError, RuntimeError) as error:
            if not self.stopping.is_set(): self.error = str(error)
        finally:
            with self.lock:
                self.clients -= 1
                for item in (client, upstream):
                    if item:
                        self.sockets.discard(item)
                        item.close()

    def close_clients(self):
        with self.lock:
            for item in list(self.sockets):
                try: item.shutdown(socket.SHUT_RDWR)
                except OSError: pass

    def close(self):
        self.stopping.set()
        self.server.close()
        self.close_clients()
        self.thread.join(timeout=2)
        if self.path: self.path.unlink(missing_ok=True)

    def report(self):
        return {'enabled': not self.stopping.is_set(), 'endpoint': self.endpoint,
                'connections': self.clients, 'error': self.error}


def tcp_target(address):
    # Retry briefly while adbd restarts, without blocking the control service.
    last = None
    for _ in range(20):
        try: return socket.create_connection((address, 5555), timeout=.2)
        except OSError as error:
            last = error
            time.sleep(.1)
    raise RuntimeError('ADB daemon is not ready: ' + str(last))


def unix_target(path):
    result = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        result.settimeout(2)
        result.connect(str(path))
        _, uid, _ = struct.unpack('3i', result.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0: raise RuntimeError('Untrusted ADB worker')
        return result
    except Exception:
        result.close()
        raise
