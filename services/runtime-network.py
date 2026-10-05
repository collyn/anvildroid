"""Private bridge uplink; no host interfaces, routes or firewall mutations."""
import ipaddress
import json
import os
import select
import subprocess


def policy(host_addresses):
    blocked = ['0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
               '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24', '192.0.2.0/24',
               '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24',
               '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4']
    blocked += [str(ipaddress.IPv4Address(address)) for address in host_addresses]
    # Filter bridged frames before slirp sees them. DNS alias may resolve to a
    # host loopback resolver: permit only its DNS port, never arbitrary services.
    return '''table bridge anvil_uplink {
      set blocked { type ipv4_addr; flags interval; auto-merge; elements = { %s }; }
      chain forward {
        type filter hook forward priority -200; policy drop;
        ether type arp accept
        iifname "anviltap" ether type ip accept
        oifname "anviltap" ether type ip udp sport 68 udp dport 67 ip daddr 255.255.255.255 accept
        oifname "anviltap" ip daddr 192.0.2.3 udp dport 53 accept
        oifname "anviltap" ip daddr 192.0.2.3 tcp dport 53 accept
        oifname "anviltap" ip daddr @blocked drop
        oifname "anviltap" ether type ip accept
      }
    }
    ''' % ', '.join(blocked)


class Uplink:
    def __init__(self):
        self.process = None
        self.exit_fd = None

    def start(self, host_fd):
        if os.fstat(host_fd).st_ino == os.stat('/proc/self/ns/net').st_ino:
            raise RuntimeError('Uplink requires a private network namespace')
        private = os.open('/proc/self/ns/net', os.O_RDONLY)
        ready_r, ready_w = os.pipe()
        exit_r, self.exit_fd = os.pipe()
        enter = ['nsenter', '--net=/proc/self/fd/' + str(host_fd)]
        try:
            addresses = subprocess.run(enter + ['ip', '-j', '-4', 'addr', 'show'],
                                       pass_fds=(host_fd,), capture_output=True, text=True,
                                       check=True, timeout=3)
            local = [item['local'] for interface in json.loads(addresses.stdout)
                     for item in interface.get('addr_info', []) if item.get('family') == 'inet']
            subprocess.run(['nft', '-f', '-'], input=policy(local), text=True,
                           capture_output=True, check=True, timeout=3)
            # Make propagation private independently of the caller. Slirp's
            # root sandbox mounts /tmp before pivot_root; shared propagation
            # must never export those temporary mounts to the host.
            self.process = subprocess.Popen(['unshare', '--mount', '--propagation', 'private',
                *enter, 'slirp4netns', '--netns-type=path',
                '--cidr=192.0.2.0/24', '--disable-host-loopback', '--enable-sandbox',
                '--enable-seccomp', '--ready-fd=' + str(ready_w), '--exit-fd=' + str(exit_r),
                '/proc/self/fd/' + str(private), 'anviltap'],
                pass_fds=(host_fd, private, ready_w, exit_r), stdin=subprocess.DEVNULL)
            os.close(ready_w); ready_w = None
            if not select.select([ready_r], [], [], 8)[0] or os.read(ready_r, 1) != b'1':
                raise RuntimeError('Private network uplink failed to initialize')
            for args in [('link', 'set', 'lo', 'up'),
                         ('link', 'set', 'anviltap', 'master', 'anvilandroid'),
                         ('link', 'set', 'anviltap', 'up')]:
                subprocess.run(['ip', *args], check=True, capture_output=True, timeout=3)
            self.check()
        except Exception:
            self.close()
            raise
        finally:
            for fd in (private, ready_r, ready_w, exit_r):
                if fd is not None: os.close(fd)

    def check(self):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError('Private network uplink exited; stop and start this runtime')

    def close(self):
        if self.exit_fd is not None:
            os.close(self.exit_fd)
            self.exit_fd = None
        if self.process is not None:
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process = None
