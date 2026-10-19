#!/usr/bin/env python3
"""Build the packaged control server from pinned scrcpy source, without modifying it.

python3 native/input/build-scrcpy-diagnostic.py SCRCPY_CHECKOUT PLATFORM_DIR BUILD_TOOLS_DIR
Requires Android platform 35, build-tools 35 and a JDK. No network downloads.
Use --verify to check the cached artifact against the source and patch pins.
"""
import hashlib
import json
import runpy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

REVISION = 'f01231dff8294fe2c99045a4f9a14b233a71bb86'


def verify():
    root = Path(__file__).resolve().parents[2]
    destination = root / 'target/input-diagnostic'
    manifest = json.loads((destination / 'scrcpy-build.json').read_text())
    assert manifest['revision'] == REVISION, 'Unexpected scrcpy source revision'
    for name, path in (
        ('patch_sha256', root / 'native/input/scrcpy-3.3.1-cancel.patch'),
        ('jar_sha256', destination / 'scrcpy-server-cancel.jar'),
        ('license_sha256', destination / 'scrcpy-LICENSE'),
    ):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[name], f'Changed scrcpy input/output: {path}'
    print('Pinned scrcpy server verified')


def build(checkout, platform, tools):
    checkout, platform, tools = map(lambda p: Path(p).resolve(), (checkout, platform, tools))
    root = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != REVISION:
        raise ValueError('Expected scrcpy v3.3.1 source')
    destination = root / 'target/input-diagnostic'
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='build-', dir=destination) as temp:
        work = Path(temp)
        # Export the pinned tree, so uncommitted checkout edits cannot enter it.
        archive = subprocess.check_output(['git', '-C', str(checkout), 'archive', '--format=zip', 'HEAD', 'server', 'LICENSE'])
        archive_path = work / 'source.zip'
        archive_path.write_bytes(archive)
        with zipfile.ZipFile(archive_path) as z:
            z.extractall(work)
        subprocess.run(['patch', '-p1', '-i', str(root / 'native/input/scrcpy-3.3.1-cancel.patch')], cwd=work, check=True)
        runpy.run_path(str(root / 'native/input/scrcpy-reset-test.py'))['test_source'](work)
        gen = work / 'gen'
        classes = work / 'classes'
        dex = work / 'dex'
        for directory in (gen / 'com/genymobile/scrcpy', classes, dex):
            directory.mkdir(parents=True)
        (gen / 'com/genymobile/scrcpy/BuildConfig.java').write_text(
            'package com.genymobile.scrcpy; public final class BuildConfig { '
            'public static final boolean DEBUG=false; public static final String VERSION_NAME="3.3.1"; }\n')
        aidl = work / 'server/src/main/aidl'
        for source in sorted(aidl.rglob('*.aidl')):
            subprocess.run([str(tools / 'aidl'), '-o' + str(gen), '-I' + str(aidl),
                            '-p', str(platform / 'framework.aidl'), str(source)], check=True)
        android = platform / 'android.jar'
        sources = sorted((work / 'server/src/main/java').rglob('*.java')) + sorted(gen.rglob('*.java'))
        subprocess.run(['javac', '-encoding', 'UTF-8', '-source', '8', '-target', '8',
                        '-bootclasspath', str(android), '-cp', str(tools / 'core-lambda-stubs.jar'),
                        '-d', str(classes), *map(str, sources)], check=True)
        inputs = work / 'classes.jar'
        with zipfile.ZipFile(inputs, 'w') as z:
            for source in sorted(classes.rglob('*.class')):
                z.write(source, source.relative_to(classes))
        subprocess.run([str(tools / 'd8'), '--min-api', '23', '--lib', str(android),
                        '--output', str(dex), str(inputs)], check=True)
        output = destination / 'scrcpy-server-cancel.jar'
        # Fixed zip timestamps make the artifact reproducible for identical tools.
        with zipfile.ZipFile(output, 'w') as z:
            for source in sorted(dex.glob('*.dex')):
                z.writestr(zipfile.ZipInfo(source.name, (1980, 1, 1, 0, 0, 0)), source.read_bytes())
        shutil.copyfile(work / 'LICENSE', destination / 'scrcpy-LICENSE')
        (destination / 'scrcpy-build.json').write_text(json.dumps({
            'revision': revision,
            'patch_sha256': hashlib.sha256((root / 'native/input/scrcpy-3.3.1-cancel.patch').read_bytes()).hexdigest(),
            'jar_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'license_sha256': hashlib.sha256((destination / 'scrcpy-LICENSE').read_bytes()).hexdigest(),
        }, indent=2) + '\n')
        print(output)
        print(hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    if sys.argv[1:] == ['--verify']:
        verify()
    elif len(sys.argv) != 4:
        raise SystemExit(__doc__)
    else:
        build(*sys.argv[1:])
