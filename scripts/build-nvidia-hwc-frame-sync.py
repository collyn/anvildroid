#!/usr/bin/env python3
"""Build an isolated HWC fix candidate from the pinned fork and local patch.

Reads existing source/SDK caches, never downloads, installs or restarts a runtime.
The resulting binary is a candidate, not a replacement for the immutable pin.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile
import os

ROOT = Path(__file__).resolve().parents[1]
PIN = runpy.run_path(str(ROOT / 'services/runtime-nvidia.py'))['FORK_SOURCE']['hwcomposer']['commit']
HWC_BASE = '7750307152d2c1bc0f81ca0163c9253a7a82d8fa'


def archive(repo, commit, paths, destination):
    data = subprocess.check_output(['git', '-C', str(repo), 'archive', commit, *paths])
    with tarfile.open(fileobj=io.BytesIO(data)) as stream:
        stream.extractall(destination, filter='data')


def build(args):
    args.output.mkdir(parents=True, exist_ok=True)
    args.output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix='build-', dir=args.output) as temporary:
        work = Path(temporary)
        fork = work / 'fork'
        source = work / 'source'
        archive(args.fork, PIN, ('build/hwcomposer', 'src', 'patches/hwcomposer'), fork)
        archive(args.hwc, HWC_BASE, ('hwcomposer',), source)
        local_patch = ROOT / 'native/nvidia/hwc-frame-sync.patch'
        for patch in sorted((fork / 'patches/hwcomposer').glob('*.patch')) + [local_patch]:
            subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                           cwd=source, check=True)
        subprocess.run(['python3', str(ROOT / 'native/nvidia/check-frame-sync.py'),
                        str(source / 'hwcomposer')], check=True)
        # __FILE__ appears in HAL logs. Normalize paths for repeatable builds
        # without editing either the SDK cache or the pinned build recipe.
        recipe = fork / 'build/hwcomposer/build.sh'
        text = recipe.read_text()
        before = 'CXXFLAGS=(-O2 '
        assert text.count(before) == 1
        text = text.replace(before, 'CXXFLAGS=(-ffile-prefix-map="$SRC"=/anvildroid/hwcomposer '
                            '-ffile-prefix-map="$HERE"=/anvildroid/recipe '
                            '-ffile-prefix-map="$WNV"=/anvildroid/sdk -O2 ')
        recipe.write_text(text)
        env = dict(os.environ, WNV=str(args.sdk.resolve()),
                   NDK=str(args.ndk.resolve()), ROOTFS=str(args.rootfs.resolve()))
        log_path = args.output / 'build.log'
        with log_path.open('w') as log:
            result = subprocess.run(['bash', str(recipe), str(source / 'hwcomposer'),
                                     str(args.output / 'out')], env=env,
                                    stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            print(log_path.read_text()[-8000:])
            result.check_returncode()
        binary = args.output / 'out/hwcomposer.waydroid.stripped.so'
        report = dict(fork_commit=PIN, hwc_base=HWC_BASE,
                      patch_sha256=hashlib.sha256(local_patch.read_bytes()).hexdigest(),
                      binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                      regression='producer fence/copy order and recycled allocation identity passed',
                      live_validation='pending; no live process or installed payload modified')
        (args.output / 'candidate.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fork', type=Path, required=True, help='Cached collyn/waydroid-nvidia Git repository')
    parser.add_argument('--hwc', type=Path, required=True, help='Cached upstream android_hardware_waydroid Git repository')
    parser.add_argument('--sdk', type=Path, required=True, help='Provisioned fork HWC SDK (WNV)')
    parser.add_argument('--rootfs', type=Path, required=True, help='Unpacked Android 13 link-time image libraries')
    parser.add_argument('--ndk', type=Path, required=True, help='Android NDK r29')
    parser.add_argument('--output', type=Path, default=ROOT / 'target/nvidia-hwc-frame-sync/build')
    build(parser.parse_args())
