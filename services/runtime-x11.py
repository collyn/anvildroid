"""Generation-scoped lifecycle for the experimental native X11 backend.

The caller supplies verified payload paths, creates private Android mounts and
owns the guest/renderer. This module never selects or changes a Wayland backend.
"""
import os
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import time


PAYLOAD_FILES = frozenset(('anvildroid-x11-host', 'runtime-x11.py', 'source-manifest.json'))


def load_payload(directory, *, trusted_owner=0):
    """Verify an explicitly supplied payload. Production requires root ownership.

    A nonzero trusted_owner is only for the unprivileged/disposable development
    harness, never inferred from a user-supplied manifest or runtime settings.
    """
    directory = Path(directory)
    def read(name, maximum):
        fd = os.open(directory / name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(fd)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != trusted_owner or
                    metadata.st_mode & 0o022 or not 0 < metadata.st_size <= maximum):
                raise RuntimeError('Unsafe native X11 payload file: ' + name)
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                return stream.read(maximum + 1)
        finally:
            os.close(fd)
    # Do not permit a writable parent to redirect a verified privileged launch.
    for path in (directory, *directory.parents):
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError('Native X11 payload path contains a symlink/non-directory')
        if trusted_owner == 0 and (metadata.st_uid != 0 or metadata.st_mode & 0o022):
            raise RuntimeError('Native X11 payload requires root-owned private install parents')
    encoded = read('manifest.json', 65536)
    try:
        manifest = json.loads(encoded)
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError('Invalid native X11 payload manifest') from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), dict):
        raise RuntimeError('Invalid native X11 payload manifest structure')
    if (manifest.get('format') != 3 or manifest.get('backend') != 'native-x11' or
            manifest.get('experimental') is not True or manifest.get('android_sdks') != [33, 34, 35, 36] or
            manifest.get('surface_protocol') != 1 or
            set(manifest.get('files', {})) != PAYLOAD_FILES or
            manifest.get('capabilities') != ['app-windows', 'shared-bridge', 'wayland-surface-tree']):
        raise RuntimeError('Unsupported native X11 payload contract')
    for name, digest in manifest['files'].items():
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise RuntimeError('Invalid native X11 payload hash')
        if hashlib.sha256(read(name, 128 * 1024**2)).hexdigest() != digest:
            raise RuntimeError('Native X11 payload hash mismatch: ' + name)
    return {name: directory / name for name in PAYLOAD_FILES}, hashlib.sha256(encoded).hexdigest()


INSTALL = Path('/usr/local/lib/anvildroid-controller/native-x11')


def selection(instance):
    path = instance / 'display-backend.json'
    value = json.loads(path.read_text()) if path.exists() else {'backend': 'auto', 'presenter': 'amd'}
    if (not isinstance(value, dict) or set(value) != {'backend', 'presenter'} or
            value['backend'] not in ('auto', 'legacy', 'native-x11') or
            value['presenter'] not in ('amd', 'intel', 'nvidia')):
        raise RuntimeError('Invalid display backend setting')
    return value


def resolve_backend(config, x11_desktop):
    """Automatic X11 must never silently launch the compatibility compositor."""
    resolved = dict(config)
    if resolved['backend'] == 'auto':
        resolved['backend'] = 'native-x11' if x11_desktop else 'legacy'
    return resolved


def preflight(instance, nvidia_rendering, *, defer_contract=False):
    report = json.loads((instance / 'support/compatibility.json').read_text())
    sdk = report.get('sdk')
    if sdk not in (33, 34, 35, 36):
        raise RuntimeError(f"Native X11 supports SDK 33-36; this image uses SDK {sdk}. Weston was not started")
    if nvidia_rendering and sdk != 33:
        raise RuntimeError(f'Native X11 NVIDIA/Venus requires SDK 33; this image uses SDK {sdk}. Select AMD or Intel in Graphics. Weston was not started')
    pending_contract = defer_contract and 'app_window_contract' not in report
    if not nvidia_rendering and ((not pending_contract and report.get('app_window_contract') != 'waydroid-hidl')
            or report.get('desktop_bridge') == 'native-wayland'):
        raise RuntimeError(f'Native X11 requires the Waydroid per-app composer contract (SDK {sdk}). Weston was not started')
    return load_payload(INSTALL)


# Used only when this backend left a recovery marker inside this guest. No
# host paths or shell input come from the desktop. Validate every saved token.
DISPLAY_RESTORE = r"""
f=/data/waydroid_tmp/anvildroid/x11-display-restore
[ -f "$f" ] || exit 0
[ ! -L "$f" ] || exit 1
read size scaling accel rotation fixed extra < "$f" || exit 1
[ -z "$extra" ] || exit 1
case "$size" in reset) ;; *[!0-9x]*|*x*x*|x*|*x|'') exit 1;; *)
    w=${size%x*}; h=${size#*x}
    [ "$w" -ge 64 ] && [ "$w" -le 8192 ] && [ "$h" -ge 64 ] && [ "$h" -le 8192 ] || exit 1;; esac
case "$scaling" in null|0|1) ;; *) exit 1;; esac
case "$accel" in null|0|1) ;; *) exit 1;; esac
case "$rotation" in null|0|1|2|3) ;; *) exit 1;; esac
case "$fixed" in default|disabled|enabled) ;; *) exit 1;; esac
wm size "$size" && wm fixed-to-user-rotation "$fixed" || exit 1
if [ "$scaling" = 1 ]; then wm scaling off; else wm scaling auto; fi || exit 1
if [ "$accel" = 1 ]; then wm user-rotation free; else r=$rotation; [ "$r" = null ] && r=0; wm user-rotation lock "$r"; fi || exit 1
restore_setting() {
    if [ "$3" = null ]; then settings delete "$1" "$2"; else settings put "$1" "$2" "$3"; fi
}
restore_setting global display_scaling_force "$scaling" &&
restore_setting system accelerometer_rotation "$accel" &&
restore_setting system user_rotation "$rotation" && rm "$f"
"""


def presenter_environment(device=None):
    """Select host presentation independently of Android's renderer GPU."""
    devices = []
    for node in sorted(Path('/sys/class/drm').glob('renderD*')):
        try:
            vendor = (node / 'device/vendor').read_text().strip()
            pci = (node / 'device').resolve().name
            if re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', pci):
                devices.append((vendor, pci))
        except OSError:
            continue
    vendors = {'amd': '0x1002', 'intel': '0x8086', 'nvidia': '0x10de'}
    if device is None:
        device = next((name for name, vendor in vendors.items()
                       if any(found == vendor for found, _ in devices)), None)
    if device not in vendors:
        raise RuntimeError('No supported native X11 presenter GPU is available')
    result = {}
    for vendor, pci in devices:
        if vendor == vendors[device]:
            result['DRI_PRIME'] = 'pci-' + pci.replace(':', '_').replace('.', '_')
            break
    if device == 'nvidia':
        result.update(__NV_PRIME_RENDER_OFFLOAD='1', __GLX_VENDOR_LIBRARY_NAME='nvidia')
    return result


class Session:
    def __init__(self, directory, executable, log_path, uid, gid, display, authority,
                 device, generation, environment=None, groups=None):
        if device not in ('amd', 'intel', 'nvidia'):
            raise ValueError('Unsupported X11 presenter GPU')
        if not re.fullmatch(r'[a-f0-9]{12,64}', generation):
            raise ValueError('Invalid X11 runtime generation')
        if not re.fullmatch(r':[0-9]{1,5}(?:\.[0-9]{1,2})?', display):
            raise ValueError('Native X11 requires a local display')
        if uid <= 0 or gid < 0:
            raise ValueError('Native X11 presenter must run as the desktop user')
        self.directory, self.log_path = Path(directory), Path(log_path)
        self.generation, self.process, self.state = generation, None, 'new'
        self.endpoint_ready = False
        self._identity = None
        self.directory.mkdir(mode=0o700)  # Refuse stale or shared generations.
        os.chown(self.directory, uid, gid)
        metadata = self.directory.lstat()
        self._identity = metadata.st_dev, metadata.st_ino
        self.command = [str(executable), '--socket', str(self.directory / 'wayland-probe')]
        self.environment = dict(environment or {}, DISPLAY=display, XAUTHORITY=str(authority),
                                XDG_SESSION_TYPE='x11', XDG_RUNTIME_DIR=str(self.directory))
        self.environment.update(presenter_environment(device))
        self.uid, self.gid, self.groups = uid, gid, groups
        self.stream = None

    def start(self):
        if self.state != 'new':
            raise RuntimeError('X11 generation cannot be started twice')
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, 'wb')
        options = dict(env=self.environment, user=self.uid, group=self.gid,
            stdin=subprocess.DEVNULL, stdout=self.stream, stderr=subprocess.STDOUT)
        if self.groups is not None:
            options['extra_groups'] = self.groups
        try:
            self.process = subprocess.Popen(self.command, **options)
            self.state = 'starting'
        except BaseException:
            self.state = 'failed'
            raise

    def evidence(self):
        try:
            with self.log_path.open('rb') as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 65536))
                return stream.read().decode('utf-8', errors='replace')
        except FileNotFoundError:
            return ''

    def check(self):
        if self.process is None:
            raise RuntimeError('X11 presenter has not started')
        code = self.process.poll()
        if code is not None:
            if self.state == 'stopped':
                return
            self.state = 'failed'
            raise RuntimeError('Native X11 presenter exited; stop this runtime generation (exit ' + str(code) + ')')
        text = self.evidence()
        self.endpoint_ready |= 'x11_adapter_ready protocol=1 ' in text
        if self.endpoint_ready:
            self.state = 'ready'

    def wait_listening(self, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            self.check()
            try:
                ready = stat.S_ISSOCK((self.directory / 'wayland-probe').lstat().st_mode)
            except FileNotFoundError:
                ready = False
            if ready:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('Native X11 listener startup timed out')
            time.sleep(.05)

    def close(self, stop_guest, stop_renderer):
        """Never tear down GPU processes while Android may still own buffers.

        A guest-stop failure is propagated with graphics retained. The caller's
        namespace/scope supervisor must then terminate the whole generation.
        """
        if self.state == 'stopped':
            return
        stop_guest()
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        finally:
            try:
                stop_renderer()
            finally:
                if self.stream is not None:
                    self.stream.close()
                self.state = 'stopped'
                # Only our known files and original directory. Never recurse
                # into a replacement directory or follow a substituted link.
                try:
                    fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                except FileNotFoundError:
                    fd = None
                try:
                    metadata = os.fstat(fd) if fd is not None else None
                    if metadata is not None and (metadata.st_dev, metadata.st_ino) == self._identity:
                        for name in ('wayland-probe', 'wayland-probe.lock'):
                            try:
                                os.unlink(name, dir_fd=fd)
                            except FileNotFoundError:
                                pass
                        # The parent's permissions are the caller's contract;
                        # never recursively remove unexpected directory content.
                        self.directory.rmdir()
                finally:
                    if fd is not None:
                        os.close(fd)
