#!/usr/bin/python3
"""Load the SELinux rule Android needs for its private Binder context manager.

On SELinux hosts (Fedora, RHEL, openSUSE with SELinux) the controller's LXC
runs Android as spc_t from container-selinux. Android's servicemanager,
hwservicemanager and vndservicemanager must become context managers of their
own binderfs nodes; the targeted policy denies that, and Android never boots.
Each rule is optional so the module loads without container-selinux.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

MODULE = 'anvildroid'
POLICY = b'''; AnvilDroid: Android in the container may run its own Binder context manager.
(optional anvildroid_spc_binder
    (allow spc_t self (binder (call set_context_mgr transfer impersonate))))
(optional anvildroid_container_runtime_binder
    (allow container_runtime_t self (binder (call set_context_mgr transfer impersonate))))
'''


def install(selinuxfs=Path('/sys/fs/selinux'), semodule=None, run=subprocess.run):
    if not (selinuxfs / 'enforce').exists():
        return 'SELinux is not active; no policy needed'
    semodule = semodule or shutil.which('semodule')
    if not semodule:
        raise RuntimeError('semodule (policycoreutils) is required to load the AnvilDroid SELinux policy')
    with tempfile.TemporaryDirectory(prefix='anvildroid-selinux-') as directory:
        policy = Path(directory) / (MODULE + '.cil')
        policy.write_bytes(POLICY)
        run([semodule, '-i', str(policy)], check=True, capture_output=True, timeout=600)
    return 'SELinux Binder policy loaded'


def remove(selinuxfs=Path('/sys/fs/selinux'), semodule=None, run=subprocess.run):
    semodule = semodule or shutil.which('semodule')
    if not (selinuxfs / 'enforce').exists() or not semodule:
        return 'SELinux is not active'
    listed = run([semodule, '-l'], check=True, capture_output=True, text=True, timeout=120).stdout.split()
    if MODULE in listed:
        run([semodule, '-r', MODULE], check=True, capture_output=True, timeout=600)
    return 'SELinux Binder policy removed'


if __name__ == '__main__':
    import sys
    if os.geteuid() != 0:
        raise SystemExit('Run this installer as root.')
    print(remove() if sys.argv[1:] == ['--remove'] else install())
