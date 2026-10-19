"""Original per-package Waydroid compatibility operations; no host filesystem writes.

The caller authorizes runtime ownership and supplies an argv-only Android adapter.
All mutable state lives in the selected Android runtime's property service.
"""
import hashlib
import json
import re
import threading

PROPERTIES = {'fake_wifi': 'persist.waydroid.fake_wifi',
              'mouse_as_touch': 'persist.waydroid.fake_touch'}
PACKAGE = re.compile(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+\Z')
_LOCK = threading.RLock()
MAX_VALUE = 8192


def validate_package(package):
    if not isinstance(package, str) or len(package) > 255 or not PACKAGE.fullmatch(package):
        raise RuntimeError('Invalid Android package')
    return package


def _installed(android, package):
    validate_package(package)
    output = android('pm', 'list', 'packages', '--user', '0', package)
    if not isinstance(output, str) or len(output) > 65536:
        raise RuntimeError('Invalid PackageManager response')
    if 'package:' + package not in output.splitlines():
        raise RuntimeError('Package is not installed for Android user 0 in this runtime')


def _read(android):
    values = {}
    for key, prop in PROPERTIES.items():
        raw = android('getprop', prop)
        if not isinstance(raw, str) or len(raw) > MAX_VALUE:
            raise RuntimeError('Invalid compatibility property value')
        raw = raw.rstrip('\r\n')
        # Refuse to rewrite an unfamiliar grammar. Never discard unrelated data.
        if raw and any(token != '*' and (len(token) > 255 or not PACKAGE.fullmatch(token))
                       for token in raw.split(',')):
            raise RuntimeError('Unsupported compatibility property format; no settings changed')
        values[key] = raw
    return values


def _revision(runtime_id, generation, values):
    return hashlib.sha256(json.dumps([runtime_id, generation, values], sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _report(runtime_id, generation, package, values, changed=False):
    settings = {}
    for key, raw in values.items():
        tokens = raw.split(',') if raw else []
        explicit, inherited = package in tokens, '*' in tokens
        settings[key] = {'explicit': explicit, 'inherited': inherited,
                         'effective': explicit or inherited}
    return {'runtime_id': runtime_id, 'generation': generation, 'package': package,
            'settings': settings, 'revision': _revision(runtime_id, generation, values),
            'changed': changed, 'activation': 'app_restart_required' if changed else 'current_configuration',
            'support': 'waydroid_properties', 'behavior_verified': False,
            'supports_global_override': False}


def read(android, package, runtime_id, generation):
    with _LOCK:
        _installed(android, package)
        return _report(runtime_id, generation, package, _read(android))


def _change(android, package, runtime_id, generation, settings, revision, reset=False):
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{64}', revision):
        raise RuntimeError('Refresh compatibility settings before applying changes')
    if not reset and (not isinstance(settings, dict) or set(settings) != set(PROPERTIES)
                      or any(type(value) is not bool for value in settings.values())):
        raise RuntimeError('Compatibility settings require exactly two boolean values')
    with _LOCK:
        _installed(android, package)
        before = _read(android)
        if revision != _revision(runtime_id, generation, before):
            raise RuntimeError('Compatibility settings or runtime changed; refresh and retry')
        after = {}
        for key, raw in before.items():
            tokens = raw.split(',') if raw else []
            if not reset and not settings[key] and '*' in tokens:
                raise RuntimeError('A global wildcard enables this setting; per-app disable is unavailable')
            # Preserve order and unrelated values, including wildcard and duplicates.
            wanted = False if reset else settings[key]
            if wanted and package not in tokens and '*' not in tokens:
                tokens.append(package)
            elif not wanted:
                tokens = [token for token in tokens if token != package]
            after[key] = ','.join(tokens)
            if len(after[key]) > MAX_VALUE:
                raise RuntimeError('Compatibility property is full; no settings changed')
        if after == before:
            return _report(runtime_id, generation, package, before)
        touched = []
        try:
            for key, prop in PROPERTIES.items():
                if after[key] == before[key]:
                    continue
                # Record before execution: a timed-out command may have taken effect.
                touched.append(key)
                android('setprop', prop, after[key])
                if android('getprop', prop).rstrip('\r\n') != after[key]:
                    raise RuntimeError('Compatibility property readback did not match')
            if _read(android) != after:
                raise RuntimeError('Compatibility settings changed during verification')
        except Exception as error:
            rollback_errors = []
            for key in reversed(touched):
                try:
                    android('setprop', PROPERTIES[key], before[key])
                    if android('getprop', PROPERTIES[key]).rstrip('\r\n') != before[key]:
                        raise RuntimeError('readback mismatch')
                except Exception:
                    rollback_errors.append(key)
            if rollback_errors:
                raise RuntimeError('Compatibility update failed; rollback could not restore ' +
                                   ', '.join(rollback_errors) + '. Refresh before retrying.') from error
            raise RuntimeError('Compatibility update failed; previous settings restored. Refresh before retrying.') from error
        return _report(runtime_id, generation, package, after, changed=True)


def apply(android, package, runtime_id, generation, settings, revision):
    return _change(android, package, runtime_id, generation, settings, revision)


def reset(android, package, runtime_id, generation, revision):
    """Remove only this package's entries; global '*' continues to apply."""
    return _change(android, package, runtime_id, generation, None, revision, reset=True)


def dispatch(android, request, runtime_id, generation):
    if not isinstance(request, dict) or request.get('op') != 'compatibility':
        raise RuntimeError('Invalid compatibility worker request')
    action = request.get('action')
    expected = {'op', 'generation', 'action', 'package'}
    if action in ('set', 'reset'):
        expected.add('revision')
    if action == 'set':
        expected.add('settings')
    if action not in ('get', 'set', 'reset') or set(request) != expected:
        raise RuntimeError('Invalid compatibility worker fields')
    if request['generation'] != generation:
        raise RuntimeError('Runtime generation changed; compatibility action refused')
    package = validate_package(request['package'])
    if action == 'get':
        return read(android, package, runtime_id, generation)
    if action == 'set':
        return apply(android, package, runtime_id, generation, request['settings'], request['revision'])
    return reset(android, package, runtime_id, generation, request['revision'])


def worker_request(backend, identifier, action, package, settings=None, revision=None):
    validate_package(package)
    if action not in ('get', 'set', 'reset'):
        raise RuntimeError('Invalid compatibility action')
    status = backend.status(identifier)
    if status.get('state') != 'Running':
        raise RuntimeError('Start this runtime before configuring app compatibility')
    if 'app_compatibility' not in status.get('capabilities', []):
        raise RuntimeError('Stop and start this runtime to enable app compatibility')
    request = {'op': 'compatibility', 'generation': status['generation'],
               'action': action, 'package': package}
    if action in ('set', 'reset'):
        request['revision'] = revision
    if action == 'set':
        request['settings'] = settings
    report = backend.status(identifier, 'compatibility', request)
    if report.get('app_error'):
        raise RuntimeError(report['app_error'])
    if report.get('generation') != status['generation'] or report.get('state') != 'Running':
        raise RuntimeError('Runtime changed during compatibility action; refresh before retrying')
    result = report.get('compatibility')
    if (not isinstance(result, dict) or result.get('runtime_id') != identifier or
            result.get('generation') != status['generation'] or result.get('package') != package):
        raise RuntimeError('Invalid compatibility response; refresh before retrying')
    return result
