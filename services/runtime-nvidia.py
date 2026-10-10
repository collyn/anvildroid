"""Experimental quinovax Venus backend; no host driver or global Waydroid edits."""
from pathlib import Path
import re
import hashlib
import json
import os
import pwd
import signal
import stat
import subprocess
import sys
import time


# Keep this list aligned with the upstream release layout. The guest has both
# Android ABI variants because ARM translation may launch 32-bit processes.
PAYLOAD_FILES = {
    'host': ('virgl_test_server', 'virgl_render_server', 'libvirglrenderer.so.1'),
    'guest32': (
        'vendor/lib/hw/vulkan.virtio.so',
        'vendor/lib/egl/libEGL_angle.so',
        'vendor/lib/egl/libGLESv1_CM_angle.so',
        'vendor/lib/egl/libGLESv2_angle.so',
    ),
    'guest64': (
        'vendor/lib64/hw/vulkan.virtio.so',
        'vendor/lib64/egl/libEGL_angle.so',
        'vendor/lib64/egl/libGLESv1_CM_angle.so',
        'vendor/lib64/egl/libGLESv2_angle.so',
        'vendor/lib64/libgbm_mesa_wrapper.so',
        'vendor/lib64/hw/hwcomposer.waydroid.so',
    ),
    'surfaceflinger': ('system/bin/surfaceflinger',),
}

UPSTREAM_REPO = 'quinovax/waydroid-nvidia'
# Local build of the quinovax dev host renderer (dev @ a2fb681b0e6c,
# virglrenderer base dc35e4d) with one extra patch: vkr_dispatch_vkCreateBuffer
# chains VkExternalMemoryBufferCreateInfo on every buffer, making the
# vkr-forced DMA_BUF/OPAQUE_FD memory export spec-conformant
# (VUID-VkBindBufferMemoryInfo-memory-02726). NVIDIA faults the GPU on draws
# touching buffers bound in violation (Xid 69 DRAW_VERTEX_ARRAY 0x274,
# upstream #10/#11), which blocks the skiagl app path on hybrid hosts.
# Guest files are unchanged from the dev-20261005-a2fb681b pin.
UPSTREAM_VERSION = 'devfix-20261010-vkr02726'
PAYLOAD_ROOT = Path('/usr/local/lib/anvildroid-controller/nvidia-devfix-20261010-vkr02726')
ARCHIVES = {
    'host-x86_64': '785a3a8b7f6ace07e497ef5e8975259828fddebca7174b70f9f33d9538c128b5',
    'guest-android-x86_64': '3e41c050e1c1fb0f319f870c80e196f33f4195914503ff0c6818c5e8078d26d5',
    'guest-prebuilts': 'ad557bde6a98a6d322c7e7a246b03a88d48d1d3d31bacb05480dff9332ae7a0f',
}



def _elf_class(path):
    with path.open('rb') as stream:
        header = stream.read(64)
    if (len(header) < 52 or header[:4] != bytes.fromhex('7f454c46')
            or header[4] not in (1, 2) or header[5:7] != b'\x01\x01'):
        raise RuntimeError('Invalid little-endian ELF header')
    bits = 32 if header[4] == 1 else 64
    if len(header) < (52 if bits == 32 else 64):
        raise RuntimeError('Truncated ELF header')
    if int.from_bytes(header[18:20], 'little') != (3 if bits == 32 else 62):
        raise RuntimeError('Expected x86/x86_64 ELF machine')
    return bits


def validate_payload(root):
    root = Path(root)
    errors, checked = [], []
    for group, names in PAYLOAD_FILES.items():
        for relative in names:
            path = root / relative if group == 'host' else root / 'guest' / relative
            try:
                for component in (path, *path.parents):
                    if component.is_symlink():
                        raise RuntimeError('Symlinked payload path')
                    if component == root: break
                if not path.is_file(): raise RuntimeError('Missing regular file')
                expected = 32 if group == 'guest32' else 64
                if _elf_class(path) != expected:
                    raise RuntimeError(f'Expected ELF{expected}')
                checked.append(str(path.relative_to(root)))
            except (OSError, RuntimeError, ValueError) as error:
                errors.append(f'{relative}: {error}')
    return {'ok': not errors, 'checked': checked, 'errors': errors}


def validate_archive_members(members):
    errors = []
    for member in members:
        name = member.name
        if name.startswith('/') or '..' in Path(name).parts:
            errors.append(f'{name}: path traversal')
        if not (member.isfile() or member.isdir()):
            errors.append(f'{name}: only regular files and directories are allowed')
    return errors


def preflight(root=Path('/')):
    def read(relative):
        try:
            return (root / relative).read_text().strip()
        except OSError:
            return ''

    version_text = read('proc/driver/nvidia/version')
    match = re.search(r'Kernel Module(?: for \S+)?\s+(\d+)\.(\d+)(?:\.(\d+))?', version_text)
    version = tuple(int(v or 0) for v in match.groups()) if match else None
    modeset = read('sys/module/nvidia_drm/parameters/modeset')
    payload_root = root / str(PAYLOAD_ROOT).lstrip('/')
    payload = validate_payload(payload_root) if payload_root.exists() else None
    checks = [
        {'id': 'kernel_module', 'status': 'ok' if version_text else 'blocked',
         'detail': 'NVIDIA kernel module detected (closed and open are supported by this backend).' if version_text else 'NVIDIA kernel module is unavailable.'},
        {'id': 'driver', 'status': 'ok' if version and version >= (535, 0, 0) else 'blocked',
         'detail': 'Quinovax requires driver 535+; Vulkan buffer sharing still needs verification.'},
        {'id': 'modeset', 'status': 'ok' if modeset in ('Y', '1') else 'blocked' if modeset in ('N', '0') else 'unknown',
         'detail': 'Requires nvidia-drm.modeset=1; unreadable state must be checked by the privileged controller.'},
        {'id': 'udmabuf', 'status': 'ok' if (root / 'dev/udmabuf').exists() else 'blocked',
         'detail': 'The renderer needs access to /dev/udmabuf for CPU-mappable buffers.'},
        {'id': 'payload',
         'status': 'ok' if payload and payload['ok'] else 'blocked' if payload else 'unknown',
         'detail': ('Host/guest payload passed structural checks; hashes are verified before launch.' if payload and payload['ok'] else
                    '; '.join(payload['errors'][:3]) if payload else
                    'NVIDIA renderer payload is not installed.')},
        {'id': 'buffer_import', 'status': 'unverified',
         'detail': 'NVIDIA Vulkan DMA-BUF, modifiers and sync_fd import/export still require a live probe.'},
        {'id': 'compositor', 'status': 'unverified',
         'detail': 'Block-linear and hybrid LINEAR presentation require a live compositor import check.'},
    ]
    return {'backend': 'nvidia-venus', 'implemented': True, 'experimental': True,
            'upstream_repo': UPSTREAM_REPO, 'upstream_version': UPSTREAM_VERSION,
            'driver_version': '.'.join(map(str, version)) if version else None,
            'checks': checks,
            'detail': 'Experimental quinovax Venus backend. ' +
                      next((c['detail'] for c in checks if c['status'] == 'blocked'),
                           'Host buffer sharing, image ABI and renderer lifecycle need validation.')}


def trusted_payload(root=PAYLOAD_ROOT):
    """Only controller-installed, immutable payloads can be executed."""
    report = validate_payload(root)
    if not report['ok']: raise RuntimeError('; '.join(report['errors'][:3]))
    for path in (root, *root.parents):
        info = path.lstat()
        if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Unsafe NVIDIA payload directory: ' + str(path))
    manifest_path = root / 'manifest.json'
    manifest_info = manifest_path.lstat()
    if not stat.S_ISREG(manifest_info.st_mode) or manifest_info.st_uid != 0 or manifest_info.st_mode & 0o022:
        raise RuntimeError('Unsafe NVIDIA payload manifest')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('archives') != ARCHIVES:
        raise RuntimeError('NVIDIA payload is not the pinned quinovax release')
    for relative in report['checked']:
        path = root / relative
        for component in (path, *path.parents):
            info = component.lstat()
            if component.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('Unsafe NVIDIA payload: ' + str(component))
            if component == root: break
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['files'].get(relative):
            raise RuntimeError('NVIDIA payload hash mismatch: ' + relative)
    return report


def properties(props):
    overrides = {
        'ro.hardware.egl': 'angle', 'ro.hardware.vulkan': 'virtio',
        'ro.hardware.gralloc': 'minigbm_gbm_mesa',
        'mesa.vn.debug': 'vtest', 'mesa.vtest.socket.name': '/dev/venus/venus.sock',
        'ro.waydroid.software_rendering': '0', 'ro.waydroid.override_props': 'false',
        # skiavk is the fault-free app render path (skiagl draws fault the
        # GPU on hybrid hosts, upstream #10/#11). Its WSI swapchain images
        # are remapped to sysmem by the local vkr device-memory patch.
        # Task snapshots are disabled in the guest (worker boot): their
        # buffers are non-importable on hybrid and kill the display on app
        # reopen.
        'debug.hwui.renderer': 'skiavk', 'debug.renderengine.backend': 'skiaglthreaded',
        # NVIDIA LINEAR DMA-BUF images reject INPUT_ATTACHMENT usage. Disable
        # the ANGLE features that request it for native window buffers. ANGLE
        # uses ':' separators; the wildcard keeps this below PROP_VALUE_MAX.
        'debug.angle.feature_overrides_disabled':
            'supportsShaderFramebuffer*:emulateAdvancedBlendEquations:supportsImagelessFramebuffer',
    }
    return [p for p in props if p.split('=', 1)[0] not in overrides
            and not p.startswith('gralloc.')] + [k + '=' + v for k, v in overrides.items()]


def image_mounts(root):
    # Preserve the image's SurfaceFlinger. Only the Android 13 native Waydroid
    # ABI is enabled initially; SDK alone does not prove HWC compatibility.
    build = (root / 'system/build.prop').read_text()
    if not re.search(r'^ro.build.version.sdk=33\s*$', build, re.M):
        raise RuntimeError('NVIDIA experimental currently requires an Android 13 Waydroid image')
    required = ('vendor/lib64/hw/gralloc.minigbm_gbm_mesa.so',
                'vendor/lib64/vendor.waydroid.display@1.0.so',
                'vendor/lib64/vendor.waydroid.window@1.2.so')
    if any(not (root / p).is_file() for p in required):
        raise RuntimeError('Image lacks the Waydroid minigbm/HWC ABI required by NVIDIA')
    mounts = []
    for group in ('guest32', 'guest64'):
        for relative in PAYLOAD_FILES[group]:
            target = root / relative
            if not target.is_file() or target.resolve() != target:
                raise RuntimeError('Missing or unsafe NVIDIA guest mount target: ' + relative)
            mounts.append((PAYLOAD_ROOT / 'guest' / relative, target))
    return mounts


class RendererFailure(RuntimeError):
    pass


class Renderer:
    """One Venus process group inside one worker's private mount/PID namespaces."""
    def __init__(self):
        self.process = None
        self.directory = Path('/run/anvil-venus')
        self.endpoint = self.directory / 'venus.sock'
        self.log_path = None
        self.log_start = 0
        self.log_scan = 0

    def start(self, uid, log):
        if uid <= 0: raise RuntimeError('NVIDIA renderer requires a desktop user')
        trusted_payload()
        checks = preflight()['checks']
        failures = [c['detail'] for c in checks if c['id'] in
                    ('kernel_module', 'driver', 'modeset', 'udmabuf') and c['status'] != 'ok']
        if failures: raise RuntimeError('; '.join(failures))
        # A single physical NVIDIA GPU avoids allocator/Venus picking different
        # devices. Multi-NVIDIA selection needs a UUID-aware upstream protocol.
        cards = list(Path('/proc/driver/nvidia/gpus').glob('*/information'))
        if len(cards) != 1: raise RuntimeError('NVIDIA experimental requires exactly one NVIDIA GPU')
        icds = [p for p in Path('/usr/share/vulkan/icd.d').glob('*nvidia*.json') if p.is_file()]
        if len(icds) != 1: raise RuntimeError('Expected one NVIDIA Vulkan ICD manifest')
        account = pwd.getpwuid(uid)
        permission = subprocess.run(
            [sys.executable, '-c',
             'import os; fd=os.open("/dev/udmabuf",os.O_RDWR); os.close(fd)'],
            user=uid, group=account.pw_gid,
            extra_groups=os.getgrouplist(account.pw_name, account.pw_gid),
            capture_output=True, timeout=5)
        if permission.returncode:
            raise RuntimeError('Desktop user cannot access /dev/udmabuf; configure host device access before selecting NVIDIA')
        self.directory.mkdir(mode=0o755)
        self.directory.chmod(0o755)
        os.chown(self.directory, uid, account.pw_gid)
        self.log_path = getattr(log, 'name', None)
        self.log_start = os.fstat(log.fileno()).st_size
        self.log_scan = self.log_start
        env = {'PATH': '/usr/bin:/bin', 'HOME': account.pw_dir,
               'LD_LIBRARY_PATH': str(PAYLOAD_ROOT),
               'RENDER_SERVER_EXEC_PATH': str(PAYLOAD_ROOT / 'virgl_render_server'),
               'VK_DRIVER_FILES': str(icds[0]), 'VK_ICD_FILENAMES': str(icds[0]),
               'VK_INSTANCE_LAYERS': 'VK_LAYER_KHRONOS_validation',
               # Activity-switch task snapshots and screencap render into
               # CPU-readable NVIDIA-LINEAR buffers, which faults this driver
               # (Xid-69 CLEAR_SURFACE 0x19D0, upstream quinovax #16). Force
               # the udmabuf fallback for those captures instead.
               'WAYDROID_NVIDIA_CPU_LINEAR': '0'}
        self.process = subprocess.Popen(
            ['/usr/bin/prlimit', '--nofile=65536:65536', '--',
             str(PAYLOAD_ROOT / 'virgl_test_server'), '--no-virgl', '--venus',
             '--multi-clients', '--socket-path', str(self.endpoint)],
            env=env, user=uid, group=account.pw_gid,
            extra_groups=os.getgrouplist(account.pw_name, account.pw_gid),
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            umask=0o077)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.check()
            if self.endpoint.exists():
                info = self.endpoint.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
                    raise RuntimeError('Unsafe NVIDIA Venus endpoint')
                # This directory lives in the worker's private /run and is
                # bind-mounted only into its own Android container.
                self.endpoint.chmod(0o666)
                return
            time.sleep(.1)
        raise RuntimeError('NVIDIA renderer socket startup timed out')

    def check(self):
        if self.process is None or self.process.poll() is not None:
            raise RendererFailure(self.failure_detail('NVIDIA renderer exited; restart the entire runtime. Inspect nvidia-renderer.log'))
        self._watch_device_loss()

    def _watch_device_loss(self):
        """Stop the runtime at the first GPU device loss.

        After VK_ERROR_DEVICE_LOST the quinovax render server restarts and the
        guest re-imports with stale object ids, so every subsequent submit
        faults the host GPU again (observed: Xid-69 every ~20s for minutes).
        The guest cannot recover from a device loss, so fail the runtime at
        the first one instead of waiting for the renderer process to exit.
        """
        if not self.log_path:
            return
        try:
            with open(self.log_path, 'rb') as stream:
                stream.seek(self.log_scan)
                text = stream.read().decode(errors='replace')
                self.log_scan = stream.tell()
        except OSError:
            return
        if 'vk_error_device_lost' not in text.lower():
            return
        raise RendererFailure(self.failure_detail(
            'NVIDIA renderer lost the GPU device; restart the entire runtime. See nvidia-renderer.log.'))

    def failure_detail(self, fallback=None):
        """Turn a Venus/device-loss crash into an actionable runtime error."""
        text = ''
        if self.log_path:
            try:
                with open(self.log_path, 'rb') as stream:
                    stream.seek(0, os.SEEK_END)
                    stream.seek(max(self.log_start, stream.tell() - 65536))
                    text = stream.read(65536).decode(errors='replace')
            except OSError:
                pass
        lower = text.lower()
        if 'xid-69' in lower or 'xid 69' in lower:
            return ('NVIDIA renderer exited after Xid-69. Restart the entire runtime; '
                    'restarting Venus alone cannot restore Android. See nvidia-renderer.log.')
        if 'vk_error_device_lost' in lower or 'device lost' in lower:
            return ('NVIDIA renderer exited after VK_ERROR_DEVICE_LOST. Restart the entire runtime; '
                    'restarting Venus alone cannot restore Android. See nvidia-renderer.log.')
        return fallback or 'NVIDIA Venus renderer stopped unexpectedly; the Android container was stopped. See nvidia-renderer.log.'

    def close(self):
        if self.process is None: return
        group = self.process.pid
        try: os.killpg(group, signal.SIGTERM)
        except ProcessLookupError: pass
        try: self.process.wait(timeout=3)
        except subprocess.TimeoutExpired: pass
        # The server forks clients; terminating only its parent leaves them
        # holding buffers and prevents the next runtime generation starting.
        try: os.killpg(group, signal.SIGKILL)
        except ProcessLookupError: pass
        self.process.wait(timeout=3)
        self.process = None
