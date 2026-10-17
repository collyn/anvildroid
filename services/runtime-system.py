"""Waydroid system-service integration; hooks live only in this process.

The distribution's Waydroid modules and Android images remain untouched.
Guest NVIDIA libraries are read-only bind mounts for one container session.
"""
import atexit
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import sys
import tempfile
import time
import types


BASE = Path(__file__).resolve().parent
STATE = Path('/run/anvildroid-system')
MARKER = STATE / 'integration.json'
DROPIN = Path('/usr/lib/systemd/system/waydroid-container.service.d/50-anvildroid.conf')
LOG = Path('/var/lib/waydroid/anvildroid-nvidia-renderer.log')


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), BASE / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


gpu = module('runtime-gpu')
existing = module('runtime-existing')
workarea = module('runtime-workarea')
default_shares = module('runtime-default-shares')


def configured_binder_nodes(tools, args):
    cfg = tools.config.load(args)['waydroid']
    names = [cfg.get(key) for key in ('binder', 'vndbinder', 'hwbinder')]
    if not any(names):
        return []  # The service can run before first initialization.
    if any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', name)
           or name == 'binder-control' for name in names) or len(set(names)) != 3:
        raise RuntimeError('Invalid configured Default Binder device names')
    return names


def prepare_host_binder(tools, args, dev=Path('/dev')):
    """Prepare the shared stock Binder context before mount isolation.

    Invoke the distribution's allocation API, without copying its driver code.
    Never run its probe here: that probe may mount a new binderfs over an
    existing context when only one configured node is missing.
    """
    names = configured_binder_nodes(tools, args)
    def character(path):
        try: return stat.S_ISCHR(path.stat().st_mode)
        except FileNotFoundError: return False
    if not names:
        # First initialization selects devices before saving their names.
        # Prepare its supported defaults in the host namespace now, so that
        # the later D-Bus initializer does not create a private Binder context.
        cfg = tools.config.load(args)['waydroid']
        drivers = tools.helpers.drivers
        names = []
        for group in (drivers.BINDER_DRIVERS, drivers.VNDBINDER_DRIVERS, drivers.HWBINDER_DRIVERS):
            candidates = group if cfg.get('vendor_type', 'MAINLINE') == 'MAINLINE' else group[:-1]
            if not candidates: raise RuntimeError('Stock Waydroid has no supported Binder devices')
            # Stock initialization selects the last existing supported device.
            present = [name for name in candidates if character(dev / name)]
            names.append(present[-1] if present else candidates[0])
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', name) or name == 'binder-control'
               for name in names) or len(set(names)) != 3:
            raise RuntimeError('Invalid stock Default Binder device names')
    missing = [name for name in names if not character(dev / name)]
    if not missing: return
    for name in missing:
        target = dev / name
        if target.exists() or target.is_symlink():
            # The stock dangling alias is safe only at the exact fixed source.
            if not target.is_symlink() or os.readlink(target) != str(dev / 'binderfs' / name):
                raise RuntimeError('Refusing to replace existing Default Binder path: ' + str(target))
    # An empty rcfile lets LXC positively report STOPPED before the first
    # container config exists; its control identity still uses this path/name.
    # Do not interpret a failed query as STOPPED.
    status = existing.linux.run_bounded('lxc-info', '--rcfile=/dev/null', '-P', tools.config.defaults['lxc'],
        '-n', 'waydroid', '-sH', timeout=3, full_output=True, output_limit=256,
        operation='Default Binder context safety check').strip()
    if status != 'STOPPED':
        raise RuntimeError('Stop Default before preparing missing host Binder devices')
    binderfs = dev / 'binderfs'
    mounted = None
    with open('/proc/self/mountinfo') as stream:
        for line in stream:
            fields = line.split()
            if fields[4] == str(binderfs): mounted = fields[fields.index('-') + 1]
    if mounted is not None and mounted != 'binder':
        raise RuntimeError('Default Binder directory has an unrelated mount')
    if mounted is None:
        if binderfs.is_symlink() or binderfs.exists() and any(binderfs.iterdir()):
            raise RuntimeError('Default Binder directory must be empty before mounting')
        existing.linux.run_bounded('modprobe', 'binder_linux', timeout=10,
                                  operation='Default Binder module preparation')
        binderfs.mkdir(mode=0o755, exist_ok=True)
        existing.linux.run_bounded('mount', '-t', 'binder', 'binder', binderfs, timeout=5,
                                  operation='Default host Binder filesystem')
    allocate = []
    for name in missing:
        source = binderfs / name
        if not character(source):
            if source.exists() or source.is_symlink():
                raise RuntimeError('Invalid existing Default Binder source: ' + str(source))
            allocate.append(name)
    if allocate: tools.helpers.drivers.allocBinderNodes(args, allocate)
    for name in missing:
        source, target = binderfs / name, dev / name
        if not character(source): raise RuntimeError('Default Binder allocation did not create ' + name)
        if not target.is_symlink(): target.symlink_to(source)
    if not all(character(dev / name) for name in names):
        raise RuntimeError('Default host Binder devices are unavailable')


def host_binder_child():
    if os.geteuid() != 0: raise RuntimeError('Default Binder preparation requires root')
    launcher = Path('/usr/bin/waydroid').resolve(strict=True)
    sys.path.insert(0, str(launcher.parent))
    import tools
    args = types.SimpleNamespace()
    tools.prep_args(args)
    prepare_host_binder(tools, args)


def available():
    return gpu.nvidia.system_integration_available()


class Integration:
    def __init__(self, tools, glib, original_mnt=None):
        self.tools, self.glib = tools, glib
        self.renderer = self.log = None
        self.timer = None
        self.args = None
        self.nvidia = False
        self.dri = None
        self.dri_node = None
        self.snapshots_disabled = False
        self.original_mnt = original_mnt
        self.sharing = (default_shares.SessionMounts(
            Path('/var/lib/anvildroid-controller'),
            existing.ExistingRuntime(Path('/var/lib/anvildroid-controller')), STATE, original_mnt, self.publish)
            if original_mnt is not None else None)
        self.original_start = tools.actions.container_manager.do_start
        self.original_freeze = tools.actions.container_manager.freeze
        self.original_stop = tools.actions.container_manager.stop
        self.original_mount = tools.helpers.images.mount_rootfs
        self.original_props = tools.helpers.images.make_prop

    def publish(self):
        if self.original_mnt is None: return
        default_shares.storage.atomic(MARKER, {
            'pid': os.getpid(), 'version': 2, 'sharing_version': 1,
            'start_time': default_shares.process_start(os.getpid()),
            'mount_namespace': os.readlink('/proc/self/ns/mnt'),
            'original_mount_namespace': self.original_mnt})

    def install(self):
        self.tools.actions.container_manager.do_start = self.start
        self.tools.actions.container_manager.freeze = self.freeze
        self.tools.actions.container_manager.stop = self.stop
        self.tools.helpers.images.mount_rootfs = self.mount
        self.tools.helpers.images.make_prop = self.props

    def start(self, args, session):
        # Do not tear down an already-running session on a duplicate Start.
        if 'session' in args: return self.original_start(args, session)
        self.args = args
        cfg = self.tools.config.load(args)
        for name in configured_binder_nodes(self.tools, args):
            try: ready = stat.S_ISCHR((Path('/dev') / name).stat().st_mode)
            except FileNotFoundError: ready = False
            if not ready:
                raise RuntimeError('Restart the Default container service to prepare host Binder devices')
        node = cfg['waydroid'].get('drm_device', 'auto')
        self.nvidia = gpu.is_nvidia(node)
        if self.sharing and self.tools.helpers.lxc.status(args) != 'STOPPED':
            raise RuntimeError('Stop the existing Default container before creating a private sharing session')
        try:
            # Updating the installed GUI alone used to leave Default's bridge
            # stale, causing its CSD plus KDE's SSD to appear together.
            # The system D-Bus service outlives container restarts and package
            # upgrades. Refresh the adapter before preparing each new boot.
            global existing
            existing = module('runtime-existing')
            runtime = existing.ExistingRuntime(Path('/var/lib/anvildroid-controller'))
            if self.sharing:
                self.sharing.runtime = runtime
                self.sharing.begin(session)
            runtime.sync_desktop_assets(int(session['user_id']))
            if self.nvidia:
                gpu.choose(node)
                self.dri_node = node
                gpu.nvidia.trusted_payload()
                # The native X11 adapter also provides a private endpoint;
                # Venus itself only needs the user's NVIDIA Vulkan device.
                uid = int(session['user_id'])
                self.log = LOG.open('ab')
                self.renderer = gpu.nvidia.Renderer(directory=STATE / 'venus', private_host_endpoint=True)
                self.renderer.start(uid, self.log)
            self.original_start(args, session)
            if self.sharing:
                pid = existing.linux.run_bounded('lxc-info', '-P', Path(self.tools.config.defaults['lxc']),
                    '-n', 'waydroid', '-pH', timeout=2, full_output=True, output_limit=256,
                    operation='Default sharing container identity').strip()
                if not re.fullmatch(r'[1-9][0-9]*', pid): raise RuntimeError('Default Android process identity is unavailable')
                self.sharing.started(int(pid))
            if self.renderer or self.sharing:
                self.timer = self.glib.timeout_add(1000, self.watch)
        except Exception:
            try: self.original_stop(args)
            finally: self.close()
            raise

    def props(self, args, session, path):
        self.original_props(args, session, path)
        target = Path(path)
        props = target.read_text().splitlines()
        runtime = existing.ExistingRuntime(Path('/var/lib/anvildroid-controller'))
        if runtime.desktop_overlay():
            # Match managed Desktop runtimes: reserve the largest monitor's
            # physical workarea, rather than the bridge's fallback 2x canvas.
            # Only the generated session properties change; user-configured
            # Waydroid width/height and the base properties remain untouched.
            props, report = workarea.boot_props(props, session['wayland_display'], int(session['user_id']))
            logging.info('Default desktop canvas: %s', report)
        if self.nvidia:
            # make_prop precedes its bind mount; base properties are kept for
            # the next Intel/AMD boot, including all user's other settings.
            props = gpu.nvidia.properties(props)
            # Default uses the same GBM/Venus buffers as managed runtimes.
            # Shader warm-up can abort Android 13's RenderEngine before boot:
            # "output buffer not gpu writeable".
            props = gpu.surfaceflinger_properties(props)
        target.write_text('\n'.join(props) + '\n')

    def mount(self, args, images, session):
        if self.sharing: self.sharing.check_thread()
        self.original_mount(args, images, session)
        if self.sharing:
            self.sharing.prepare(Path(self.tools.config.defaults['lxc']) / 'waydroid/config_session')
        if not self.nvidia: return
        root = Path(self.tools.config.defaults['rootfs'])
        for source, target in gpu.nvidia.image_mounts(root):
            self.tools.helpers.mount.bind_file(args, str(source), str(target))
            self.tools.helpers.run.user(args, ['mount', '-o', 'remount,bind,ro', str(target)])
        # Session config is regenerated by Waydroid on every boot.
        dri = self.private_dri(self.dri_node)
        path = Path(self.tools.config.defaults['lxc']) / 'waydroid/config_session'
        with path.open('a') as config:
            config.write(f'lxc.mount.entry = {self.renderer.directory} dev/venus none bind,ro,create=dir 0 0\n')
            config.write(f'lxc.mount.entry = {dri} dev/dri none bind,ro,dev,create=dir 0 0\n')

    def private_dri(self, node):
        # Stock Waydroid excludes NVIDIA from getDriNode, leaving /dev/dri
        # empty. Even the Venus wrapper needs minigbm to open a render node
        # before dev_create (the wrapper itself allocates through Venus).
        # Match managed runtimes: expose only the selected node, with a
        # private inode so Android ueventd cannot chmod the host device.
        if not isinstance(node, str) or not re.fullmatch(r'/dev/dri/renderD[0-9]+', node):
            raise RuntimeError('Default NVIDIA requires a DRM render node')
        metadata = Path(node).lstat()
        if not stat.S_ISCHR(metadata.st_mode) or os.major(metadata.st_rdev) != 226:
            raise RuntimeError('Default NVIDIA requires a DRM character device')
        if self.dri is not None:
            raise RuntimeError('Default NVIDIA DRM directory already exists')
        self.dri = Path(tempfile.mkdtemp(prefix='dri-', dir=STATE))
        self.dri.chmod(0o755)
        target = self.dri / Path(node).name
        os.mknod(target, stat.S_IFCHR | 0o666, metadata.st_rdev)
        target.chmod(0o666)  # Do not inherit the service's umask.
        return self.dri

    def watch(self):
        try:
            if self.renderer: self.renderer.check()
            if self.tools.helpers.lxc.status(self.args) == 'STOPPED':
                self.timer = None
                self.stop(self.args)
                return False
            if self.sharing and self.sharing.active:
                self.sharing.reconcile()
            if self.renderer and not self.snapshots_disabled:
                result = existing.linux.run_bounded('lxc-attach', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid',
                    '--', '/system/bin/getprop', 'sys.boot_completed', timeout=2, full_output=True,
                    output_limit=256, operation='Default NVIDIA boot check')
                if result.strip() == '1':
                    existing.linux.run_bounded('lxc-attach', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid',
                        '--', '/system/bin/settings', 'put', 'global', 'enable_task_snapshots', '0', timeout=3,
                        operation='Default NVIDIA snapshot compatibility')
                    self.snapshots_disabled = True
        except gpu.nvidia.RendererFailure as error:
            logging.error('Default NVIDIA failed: %s', error)
            self.timer = None
            self.stop(self.args)
            return False
        except (RuntimeError, OSError):
            # The container/platform may still be starting. Renderer failures
            # above are fatal; a boot probe is retried without restarting GPU.
            pass
        return True

    def freeze(self, args):
        # An explicit debug session must survive Waydroid's window-idle freeze.
        try:
            lease = Path('/var/lib/anvildroid-controller/adb-default.lease').lstat()
            if stat.S_ISREG(lease.st_mode) and lease.st_uid == 0 and not lease.st_mode & 0o077 and time.time() - lease.st_mtime < 12:
                return
        except (OSError, RuntimeError): pass
        return self.original_freeze(args)

    def stop(self, args, quit_session=True):
        try: return self.original_stop(args, quit_session)
        finally: self.close()

    def close(self):
        if self.sharing and self.sharing.active:
            self.sharing.check_thread()
            # Stock hardware_manager owns an inherited namespace and can issue
            # OTA callbacks. Never switch namespaces while that thread survives.
            helper = getattr(self.args, 'hardware_manager', None)
            if helper is not None and helper.is_alive():
                if helper is threading.current_thread():
                    raise RuntimeError('Default sharing cleanup cannot run from its hardware callback thread')
                helper.join(timeout=3)
                if helper.is_alive():
                    raise RuntimeError('Default hardware callbacks did not stop; retaining shared-folder namespace')
            if self.tools.helpers.lxc.status(self.args) != 'STOPPED':
                raise RuntimeError('Default Android is still active; retaining shared-folder namespace')
        if self.timer is not None:
            self.glib.source_remove(self.timer)
            self.timer = None
        if self.renderer:
            try:
                self.renderer.close()
                # Remove only the fixed endpoint and empty directory.
                self.renderer.endpoint.unlink(missing_ok=True)
                try: self.renderer.directory.rmdir()
                except FileNotFoundError: pass
            finally:
                self.renderer = None
        if self.log:
            self.log.close()
            self.log = None
        if self.dri is not None:
            (self.dri / Path(self.dri_node).name).unlink(missing_ok=True)
            self.dri.rmdir()
            self.dri = None
        self.dri_node = None
        self.nvidia = self.snapshots_disabled = False
        if self.sharing and self.sharing.active: self.sharing.close(True)


def main():
    if os.geteuid() != 0: raise RuntimeError('Waydroid system integration requires root')
    # Stock Binder is used by host clients as well as Android. Prepare it in
    # this inherited namespace; a short exec child keeps stock imports/threads
    # out of the parent until its private mount baseline exists.
    existing.linux.run_bounded(sys.executable, __file__, '--prepare-host-binder',
                              timeout=30, operation='Default host Binder preparation')
    # This must precede importing stock Waydroid (which may create threads).
    # All subsequent container sessions inherit only this private baseline.
    original_mnt = os.readlink('/proc/self/ns/mnt')
    os.unshare(os.CLONE_NEWNS)
    subprocess.run(['mount', '--make-rprivate', '/'], check=True, capture_output=True)
    if os.readlink('/proc/self/ns/mnt') == original_mnt:
        raise RuntimeError('System Waydroid needs a private mount namespace')
    launcher = Path('/usr/bin/waydroid').resolve(strict=True)
    sys.path.insert(0, str(launcher.parent))
    import tools
    from gi.repository import GLib
    integration = Integration(tools, GLib, original_mnt)
    integration.install()
    # RuntimeDirectory is created by systemd, owned by root, not caller input.
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Unsafe system Waydroid runtime directory')
    integration.publish()
    atexit.register(integration.close)
    atexit.register(lambda: MARKER.unlink(missing_ok=True))
    sys.argv = [str(launcher), 'container', 'start']
    os.umask(0o022)
    return tools.main()


if __name__ == '__main__':
    if sys.argv[1:] == ['--prepare-host-binder']:
        host_binder_child()
    else:
        sys.exit(main())
