"""Owner-authorized feature integration for controller, independent of IPC UI."""
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import re


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


compatibility = module('runtime-compatibility')
shares = module('runtime-shares')
components = module('runtime-components')
diagnostics = module('runtime-diagnostics')

FIELDS = {
    'compatibility_get': {'op', 'id', 'package'},
    'compatibility_set': {'op', 'id', 'package', 'settings', 'revision'},
    'compatibility_reset': {'op', 'id', 'package', 'revision'},
    'shares_get': {'op', 'id'},
    'shares_set': {'op', 'id', 'name', 'source', 'read_only'},
    'shares_remove': {'op', 'id', 'name'},
    'components_get': {'op', 'id'},
    'components_set': {'op', 'id', 'component_id', 'enabled', 'archive_sha256', 'revision'},
    'diagnostics_collect': {'op', 'id', 'include_logs'},
}


def authorize(store, identifier, uid):
    if not isinstance(identifier, str) or (identifier != 'default' and not re.fullmatch(r'r-[0-9a-f]{32}', identifier)):
        raise RuntimeError('Invalid runtime ID')
    if identifier == 'default':
        session, saved = store.existing.authorize(uid)
        return store.existing.record(session, saved)
    record = next((row for row in store.data['runtimes'] if row['id'] == identifier and row['owner_uid'] == uid), None)
    if record is None:
        raise RuntimeError('Runtime not found for this user')
    if identifier in store.deletions:
        raise RuntimeError('Deletion is incomplete; feature operations are unavailable')
    if identifier in store.active_jobs:
        raise RuntimeError('Wait for the runtime operation to finish')
    if store.storage.busy is not None or store.app_jobs.get(identifier, {}).get('status') == 'Running':
        raise RuntimeError('Wait for runtime storage or app operations to finish')
    store.storage.ensure(identifier)
    return record


def component_report(store, identifier, uid):
    arm = store._dispatch({'op': 'arm_info', 'id': identifier}, uid)
    sdk = None
    selection = store.selections.get(identifier)
    if identifier == 'default':
        # Default can only restore the installed provider's saved properties;
        # its existing arm_set path does not install selected archives.
        _, saved = store.existing.authorize(uid)
        arm = dict(arm, restore_available=bool(saved.get('arm_properties')))
        if store.existing.state() == 'RUNNING':
            value = store.existing.android('getprop', 'ro.build.version.sdk').strip()
            sdk = int(value) if value.isdecimal() else None
    else:
        try:
            metadata = store.backend.location(identifier) / 'support/compatibility.json'
            sdk = json.loads(metadata.read_text()).get('sdk')
        except (OSError, ValueError, AttributeError):
            pass
    try:
        google = store._dispatch({'op': 'google_services', 'id': identifier}, uid)
    except Exception:
        google = None
    report = components.catalog(identifier, architecture=platform.machine(), sdk=sdk,
                                arm_report=arm, google_report=google, selection=selection)
    report['revision'] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    return report


def dispatch(store, request, uid, descriptor=None):
    op = request.get('op')
    if op not in FIELDS or set(request) != FIELDS[op] or descriptor is not None:
        raise RuntimeError('Unexpected feature fields or file descriptor')
    record = authorize(store, request['id'], uid)
    identifier = request['id']
    if op.startswith('compatibility_'):
        action = op.removeprefix('compatibility_')
        if identifier == 'default':
            session, _ = store.existing.authorize(uid)
            if store.existing.state() != 'RUNNING':
                raise RuntimeError('Start Default runtime before configuring app compatibility')
            generation = str(session.get('pid', '')) if isinstance(session, dict) else ''
            if not generation.isdecimal() or int(generation) <= 0:
                raise RuntimeError('Default runtime generation is unavailable')
            payload = {'op': 'compatibility', 'action': action, 'generation': generation,
                       'package': request['package']}
            payload.update({key: request[key] for key in ('settings', 'revision') if key in request})
            result = compatibility.dispatch(store.existing.android, payload, identifier, generation)
            after, _ = store.existing.authorize(uid)
            if not isinstance(after, dict) or after.get('pid') != session.get('pid') or after.get('user_id') != session.get('user_id'):
                raise RuntimeError('Default runtime changed during compatibility action; refresh before retrying')
            return result
        return compatibility.worker_request(store.android, identifier, action, request['package'],
                                            request.get('settings'), request.get('revision'))
    if op.startswith('shares_'):
        if identifier == 'default':
            raise RuntimeError('Shared folders currently require a managed runtime; Default uses the system Waydroid mount lifecycle')
        instance = store.backend.location(identifier)
        if op == 'shares_get':
            mounted = None
            if record['state'] in ('Running', 'Frozen'):
                status = store.android.status(identifier)
                mounted = status.get('shared_folders_mounted')
            return dict(shares.list_shares(instance, uid, mounted), runtime_id=identifier,
                        editable=record['state'] in ('Prepared', 'Stopped') and store.android.quiescent(identifier))
        if record['state'] not in ('Prepared', 'Stopped') or not store.android.quiescent(identifier):
            raise RuntimeError('Stop this runtime completely before changing shared folders')
        result = (shares.configure(instance, uid, {key: request[key] for key in ('name', 'source', 'read_only')})
                  if op == 'shares_set' else shares.remove(instance, uid, request['name']))
        return dict(result, runtime_id=identifier, editable=True)
    if op in ('components_get', 'components_set'):
        report = component_report(store, identifier, uid)
        if op == 'components_set':
            if request['revision'] != report['revision']:
                raise RuntimeError('Components or runtime changed; refresh before applying')
            arm_request = components.arm_action(report, request['component_id'], request['enabled'], request['archive_sha256'])
            store._dispatch(arm_request, uid)
            return component_report(store, identifier, uid)
        return report
    if op == 'diagnostics_collect':
        if type(request['include_logs']) is not bool:
            raise RuntimeError('include_logs must be boolean')
        runtime = {key: record[key] for key in ('id', 'state') if key in record}
        collectors = {'runtime': lambda: runtime,
                      'health': lambda: store._dispatch({'op': 'health', 'id': identifier}, uid),
                      'resources': lambda: store._dispatch({'op': 'resources_info', 'id': identifier}, uid),
                      'display': lambda: store._dispatch({'op': 'display_info', 'id': identifier}, uid),
                      'components': lambda: component_report(store, identifier, uid),
                      'image': lambda: store.selections.get(identifier) or {'source': 'system_waydroid'}}
        generation = None
        if identifier != 'default':
            try:
                generation = store.android.status(identifier).get('generation')
            except (OSError, RuntimeError, ValueError):
                pass
        return diagnostics.collect(identifier, collectors, include_logs=request['include_logs'],
                                   log_directory=store.backend.location(identifier) if identifier != 'default' else None,
                                   generation=generation)
    raise RuntimeError('Unsupported feature operation')
