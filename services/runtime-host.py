"""Host programs needed by managed runtimes and image-specific provisioning."""
import shutil

# Packages are Ubuntu/Debian names; installation is performed only by the
# explicitly authorized setup helper, never by status polling.
COMMAND_PACKAGES = {
    'unshare': 'util-linux', 'nsenter': 'util-linux', 'mount': 'mount', 'umount': 'mount',
    'ip': 'iproute2', 'nft': 'nftables', 'slirp4netns': 'slirp4netns',
    'lxc-start': 'lxc', 'lxc-stop': 'lxc', 'lxc-info': 'lxc', 'lxc-attach': 'lxc',
    'weston': 'weston', 'aapt': 'aapt', 'apksigner': 'apksigner',
    'java': 'default-jre-headless', 'modprobe': 'kmod', 'systemd-run': 'systemd',
    'curl': 'curl', 'patchelf': 'patchelf',
}


def missing_commands():
    return [command for command in COMMAND_PACKAGES if shutil.which(command) is None]


def missing_packages():
    return sorted({COMMAND_PACKAGES[command] for command in missing_commands()})
