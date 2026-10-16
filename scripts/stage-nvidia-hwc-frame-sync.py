#!/usr/bin/env python3
"""Stage a reviewable local HWC candidate without installing or changing pins.

Uses verified current archives and a candidate produced by the isolated builder.
The generated controller module/manifest record the local patch and binary hashes.
"""
import argparse
import ast
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import pprint
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def stage(args):
    nvidia = module('frame_sync_base', ROOT / 'services/runtime-nvidia.py')
    preparer = module('frame_sync_preparer', ROOT / 'scripts/prepare-nvidia-fork.py')
    installer = module('frame_sync_installer', ROOT / 'scripts/install-nvidia-payload.py')
    report = json.loads((args.build / 'candidate.json').read_text())
    binary = (args.build / 'out/hwcomposer.waydroid.stripped.so').read_bytes()
    patch = (ROOT / 'native/nvidia/hwc-frame-sync.patch').read_bytes()
    assert hashlib.sha256(binary).hexdigest() == report['binary_sha256']
    assert hashlib.sha256(patch).hexdigest() == report['patch_sha256']
    assert report['fork_commit'] == nvidia.FORK_SOURCE['hwcomposer']['commit']
    if args.output.exists():
        raise FileExistsError('Candidate already staged; use a new output directory')
    args.output.mkdir(parents=True)
    archives = args.output / 'archives'
    archives.mkdir()
    version = nvidia.UPSTREAM_VERSION + '-frame-sync-' + report['patch_sha256'][:8]
    hashes = {}
    for component, expected in nvidia.ARCHIVES.items():
        base = args.archives / f'waydroid-nvidia-{component}-{nvidia.UPSTREAM_VERSION}.tar.gz'
        data = preparer.verified_bytes(base, expected)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            errors = nvidia.validate_archive_members(archive.getmembers())
            if errors:
                raise RuntimeError('; '.join(errors))
            files = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
        if component == 'guest-android-x86_64':
            hwc = 'vendor/lib64/hw/hwcomposer.waydroid.so'
            assert hwc in files
            files[hwc] = binary
        destination = archives / f'waydroid-nvidia-{component}-{version}.tar.gz'
        preparer.write_archive(files, destination, component)
        hashes[component] = hashlib.sha256(destination.read_bytes()).hexdigest()
    source = copy.deepcopy(nvidia.FORK_SOURCE)
    source['hwcomposer']['local_patch'] = dict(
        path='native/nvidia/hwc-frame-sync.patch', sha256=report['patch_sha256'],
        binary_sha256=report['binary_sha256'])
    values = dict(UPSTREAM_VERSION=version, FORK_SOURCE=source, ARCHIVES=hashes)
    original = (ROOT / 'services/runtime-nvidia.py').read_text()
    lines = original.splitlines(keepends=True)
    edits = []
    for statement in ast.parse(original).body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id in values:
                edits.append((statement.lineno - 1, statement.end_lineno,
                              target.id + ' = ' + pprint.pformat(values[target.id], sort_dicts=False) + '\n'))
    assert len(edits) == len(values)
    for first, last, replacement in reversed(edits):
        lines[first:last] = [replacement]
    controller = args.output / 'runtime-nvidia.py'
    controller.write_text(''.join(lines))
    candidate = module('frame_sync_candidate', controller)
    installer.nvidia = candidate
    destination = args.output / candidate.PAYLOAD_ROOT.name
    manifest = installer.stage(archives, destination)
    for name, expected in manifest['files'].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected
    assert manifest['source'] == source and manifest['archives'] == hashes
    (args.output / 'candidate.json').write_text(json.dumps(dict(
        **report, version=version, payload=str(destination.resolve()),
        controller_sha256=hashlib.sha256(controller.read_bytes()).hexdigest(),
        archives=hashes, installed=False), indent=2) + '\n')
    print('Staged and verified all', len(manifest['files']), 'ELFs:', destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=ROOT / 'target/nvidia-hwc-frame-sync/build')
    parser.add_argument('--archives', type=Path, default=ROOT / 'target/tools/nvidia')
    parser.add_argument('--output', type=Path, default=ROOT / 'target/nvidia-hwc-frame-sync/staged')
    stage(parser.parse_args())
