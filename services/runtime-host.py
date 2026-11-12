"""Host programs needed by managed runtimes and image-specific provisioning."""
from pathlib import Path
import shutil

# Packages are Ubuntu/Debian names; installation is performed only by the
# explicitly authorized setup helper, never by status polling.
COMMAND_PACKAGES = {
    'unshare': 'util-linux', 'nsenter': 'util-linux', 'mount': 'mount', 'umount': 'mount',
    'ip': 'iproute2', 'nft': 'nftables', 'slirp4netns': 'slirp4netns',
    'lxc-start': 'lxc', 'lxc-stop': 'lxc', 'lxc-info': 'lxc', 'lxc-attach': 'lxc',
    'aapt': 'aapt', 'apksigner': 'apksigner',
    'java': 'default-jre-headless', 'modprobe': 'kmod', 'systemd-run': 'systemd',
    'curl': 'curl', 'patchelf': 'patchelf',
}


def missing_commands():
    return [command for command in COMMAND_PACKAGES if shutil.which(command) is None]


# Package names on other package managers, keyed by the Debian name above.
# None: the distribution has no package for it; AnvilDroid's own package
# bundles that tool (aapt, apksigner). Names are release-independent so one
# table serves every Fedora release (43 onwards), openSUSE and Arch.
DISTRO_PACKAGES = {
    'dnf': {'mount': 'util-linux-core', 'iproute2': 'iproute', 'default-jre-headless': 'java-headless',
            'aapt': None, 'apksigner': None},
    'zypper': {'mount': 'util-linux', 'default-jre-headless': 'java-headless>=17', 'aapt': None, 'apksigner': None},
    'pacman': {'mount': 'util-linux', 'default-jre-headless': 'jre-openjdk-headless', 'aapt': None, 'apksigner': None},
}


def missing_packages(manager='apt'):
    names = DISTRO_PACKAGES.get(manager, {})
    return sorted({package for package in (names.get(COMMAND_PACKAGES[command], COMMAND_PACKAGES[command])
                                           for command in missing_commands()) if package})


# Android's system_server runs as AID_SYSTEM (1000) without a user namespace,
# so its inotify watches count against host UID 1000's quota. When a desktop
# program (an indexer, an editor's file watcher) has used that quota up,
# system_server aborts at boot ("Could not register INotify for /dev/input")
# and Android restarts forever without any visible error.
ANDROID_SYSTEM_UID = 1000
INOTIFY_RESERVE = 512


def inotify_usage(uid=ANDROID_SYSTEM_UID, proc=Path('/proc'), limit_path=Path('/proc/sys/fs/inotify/max_user_watches')):
    """(watches used by uid, limit, top holders as [(count, pid, name)])."""
    limit = int(limit_path.read_text().split()[0])
    used, holders = 0, []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            count = 0
            for info in (entry / 'fdinfo').iterdir():
                try:
                    count += sum(1 for line in info.read_text().splitlines() if line.startswith('inotify'))
                except OSError:
                    continue
            if count:
                name = (entry / 'comm').read_text().strip()
                used += count
                holders.append((count, int(entry.name), name))
        except OSError:
            continue  # process exited or is not readable
    return used, limit, sorted(holders, reverse=True)[:3]


def inotify_check(uid=ANDROID_SYSTEM_UID, **paths):
    try:
        used, limit, holders = inotify_usage(uid, **paths)
    except (OSError, ValueError):
        return {'id': 'inotify', 'label': 'File watch limit', 'available': True, 'optional': True,
                'detail': 'Could not measure inotify usage'}
    if limit - used >= INOTIFY_RESERVE:
        return {'id': 'inotify', 'label': 'File watch limit', 'available': True,
                'detail': f'{used} of {limit} inotify watches in use by host user {uid}'}
    top = ', '.join(f'{name} (PID {pid}): {count}' for count, pid, name in holders)
    return {'id': 'inotify', 'label': 'File watch limit', 'available': False,
            'detail': (f'Host user {uid} has used {used} of {limit} inotify watches; Android\'s system server shares '
                       f'this user and cannot boot. Largest: {top}. Close that program or raise the limit: '
                       'sudo sysctl fs.inotify.max_user_watches=524288 (persist in /etc/sysctl.d).')}
