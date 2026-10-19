#!/usr/bin/env python3
"""Offline real-archive ARM catalog acceptance in a disposable controller store.

sudo python3 tests/acceptance/verify-arm-catalog.py /path/to/commit-named-zips --output report.json
No download, service call, Android start, mount or installer execution occurs.
Input files are named <catalog revision>.zip and must match upstream SHA-256.
This verifies installation/configuration, not actual Android app execution.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0: raise RuntimeError('Root is required for production payload ownership verification')
    spec = importlib.util.spec_from_file_location('catalog_controller', REPO / 'services/runtime-controller.py')
    controller = importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)
    arm = controller.worker_module.arm
    source = arm._source
    results = []
    with tempfile.TemporaryDirectory(prefix='anvil-arm-catalog-') as temporary:
        root = Path(temporary)
        store = controller.Store(root, root, allow_temporary_storage=True)
        try:
            record = store.dispatch({'op':'create', 'name':'ARM catalog fixture'}, 1000)
            record.update(state='Prepared', job={'id':'a'*32, 'operation':'prepare', 'status':'Succeeded', 'error':None})
            store.replace(record)
            instance = store.backend.location(record['id']); instance.mkdir(mode=0o700)
            support = instance / 'support'; support.mkdir(mode=0o700)
            (support / 'images.json').write_text('{}')
            for entry in arm.CATALOG:
                upstream = args.archives / (entry['revision'] + '.zip')
                def local_download(url, output, expected):
                    assert url == entry['url']
                    with upstream.open('rb') as stream:
                        _, digest = source.copy_bounded(stream, output, source.MAX_ARCHIVE)
                    assert digest == expected, 'Upstream checksum mismatch: ' + upstream.name
                    return digest
                collected = root / 'collected.zip'
                with patch.object(source, 'download', side_effect=local_download), collected.open('wb') as output:
                    source.collect('builtin', entry['sha256'], output)
                assert hashlib.sha256(collected.read_bytes()).hexdigest() == entry['sha256']
                with collected.open('rb') as stream:
                    imported = store.dispatch({'op':'arm_import'}, 1000, stream.fileno())
                assert imported['version'] == entry['version']
                assert imported['sha256'] == entry['sha256']
                (support / 'compatibility.json').write_text(json.dumps({'sdk':entry['android_sdks'][0]}))
                result = store.dispatch({'op':'arm_set', 'id':record['id'], 'enabled':True,
                                         'archive_sha256':entry['sha256']}, 1000)
                assert result['archive_sha256'] == entry['sha256'] and result['enabled']
                assert result['android_sdk'] == entry['android_sdks'][0]
                library = 'libndk_translation.so' if entry['engine'] == 'libndk' else 'libhoudini.so'
                properties = arm.runtime_properties(instance)
                assert properties['ro.dalvik.vm.native.bridge'] == library
                if entry['engine'] == 'libndk': assert properties['ro.ndk_translation.version'] == '0.2.3'
                else: assert 'ro.ndk_translation.version' not in properties
                payload = arm.active_layer(instance)
                manifest = arm.verify(payload, entry['sha256'])
                assert not (payload / 'system/etc/init').exists()
                assert not (payload / 'system/etc/binfmt_misc').exists()
                if entry.get('variant'):
                    assert manifest['files']['system/lib64/' + library] == entry['patch_sha256']
                cached = next(item for item in store.arm_sources(1000) if item['sha256'] == entry['sha256'])
                assert cached['installed'] and cached['android_sdks'] == entry['android_sdks']
                row = {'engine':entry['engine'], 'version':entry['version'], 'sha256':entry['sha256'],
                       'upstream_sha256':entry.get('upstream_sha256', entry['sha256']),
                       'files':len(manifest['files']), 'patched':bool(entry.get('variant')), 'result':'PASS'}
                results.append(row)
                print(entry['label'] + ': PASS', flush=True)
            # Corrupt a cached selection: preserve the last active config and payload.
            previous = (instance / 'arm.json').read_bytes()
            selected = arm.active_layer(instance)
            first = arm.CATALOG[1]
            cached = store.arm_sources_root / '1000' / (first['sha256'] + '.zip')
            original = cached.read_bytes(); cached.write_bytes(b'bad archive')
            try:
                try:
                    store.dispatch({'op':'arm_set', 'id':record['id'], 'enabled':True,
                                    'archive_sha256':first['sha256']}, 1000)
                except controller.RequestError as error:
                    assert error.code == 'CONFIG'
                else: raise AssertionError('Corrupt archive accepted')
                assert (instance / 'arm.json').read_bytes() == previous
                assert arm.active_layer(instance) == selected
            finally:
                cached.write_bytes(original)
            # Revisit an older version from its retained payload, disable and restore.
            request = {'op':'arm_set', 'id':record['id'], 'enabled':True, 'archive_sha256':first['sha256']}
            store.dispatch(request, 1000)
            restored = arm.active_layer(instance)
            store.dispatch({'op':'arm_set', 'id':record['id'], 'enabled':False}, 1000)
            assert arm.active_layer(instance) is None
            assert 'arm' not in arm.runtime_properties(instance)['ro.product.cpu.abilist']
            store.dispatch(request, 1000)
            assert arm.active_layer(instance) == restored
        finally:
            store.close()
    report = {'catalog':results, 'corrupt_cache_rejected':True, 'previous_config_retained':True,
              'retained_version_restore':True, 'disable_restore':True, 'fixture_removed':not root.exists(),
              'android_execution_tested':False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(str(len(results)) + '/' + str(len(arm.CATALOG)) + ' catalog entries passed; disposable store removed')


if __name__ == '__main__': main()
