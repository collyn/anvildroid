"""Owner-authorized feature integration for controller, independent of IPC UI."""
import contextlib
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
default_shares = module('runtime-default-shares')
components = module('runtime-components')
diagnostics = module('runtime-diagnostics')
linux = module('runtime-linux')
device = module('runtime-device')
notifications = module('runtime-notifications')
camera = module('runtime-camera')

FIELDS = {
    'device_info': {'op', 'id'},
    'device_set': {'op', 'id', 'profile', 'custom', 'revision'},
    'compatibility_get': {'op', 'id', 'package'},
    'compatibility_set': {'op', 'id', 'package', 'settings', 'revision'},
    'compatibility_reset': {'op', 'id', 'package', 'revision'},
    'shares_get': {'op', 'id'},
    'shares_set': {'op', 'id', 'name', 'source', 'read_only'},
    'shares_remove': {'op', 'id', 'name'},
    'components_get': {'op', 'id'},
    'components_set': {'op', 'id', 'component_id', 'enabled', 'archive_sha256', 'revision'},
    'diagnostics_collect': {'op', 'id', 'include_logs'},
    'notifications_get': {'op', 'id'},
    'notifications_set': {'op', 'id', 'enabled'},
    'notifications_app_set': {'op', 'id', 'package', 'allowed'},
    'camera_get': {'op', 'id'},
    'camera_set': {'op', 'id', 'enabled', 'devices'},
}


def authorize(store, identifier, uid):
    if not isinstance(identifier, str) or (identifier != 'default' and not re.fullmatch(r'r-[0-9a-f]{32}', identifier)):
        raise RuntimeError('Invalid runtime ID')
    if identifier == 'default':
        if 'default' in store.active_jobs:
            raise RuntimeError('Wait for the System Waydroid operation to finish')
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


def diagnostic_session(store, identifier, uid):
    """Read owner-authorized identity without starting or waking Android."""
    if identifier == 'default':
        session, _ = store.existing.authorize(uid)
        state = store.existing.state()
        if not session:
            if state != 'STOPPED':
                raise RuntimeError('System Waydroid has no authenticated session')
            return ('default', state, None)
        pid = session.get('pid')
        if session.get('user_id') != str(uid):
            raise RuntimeError('System Waydroid session owner changed')
        identity = ('default', state, pid, session['user_id'], diagnostics.process_start(pid))
        if state in ('RUNNING', 'FROZEN'):
            container_pid = linux.run_bounded('lxc-info', '-P', store.existing.work / 'lxc',
                '-n', 'waydroid', '-pH', timeout=1, full_output=True, output_limit=256,
                operation='Default diagnostic container identity').strip()
            identity += (container_pid, diagnostics.process_start(container_pid))
        return identity
    record = authorize(store, identifier, uid)
    try:
        status = store.android.status(identifier)
    except (OSError, RuntimeError, ValueError):
        if record['state'] not in ('Allocated', 'Prepared', 'Stopped', 'Error') or not store.android.quiescent(identifier):
            raise
        # Configuration-only reports do not require a live worker or reuse old logs.
        return ('managed', record['state'], 'Stopped', None)
    if not isinstance(status, dict) or status.get('runtime_id') != identifier:
        raise RuntimeError('Diagnostic worker runtime identity is unavailable')
    state, generation = status.get('state'), status.get('generation')
    if state not in ('Starting', 'Running', 'Frozen', 'Stopping', 'Stopped', 'Error'):
        raise RuntimeError('Diagnostic worker state is unavailable')
    if generation is not None and (not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{32}', generation)):
        raise RuntimeError('Diagnostic worker generation is unavailable')
    if generation is None and (state != 'Stopped' or not store.android.quiescent(identifier)):
        raise RuntimeError('Diagnostic worker generation is unavailable')
    return ('managed', record['state'], state, generation)


def dispatch(store, request, uid, descriptor=None):
    op = request.get('op')
    if op not in FIELDS or set(request) != FIELDS[op] or descriptor is not None:
        raise RuntimeError('Unexpected feature fields or file descriptor')
    record = authorize(store, request['id'], uid)
    identifier = request['id']
    if op in ('device_info', 'device_set'):
        if identifier == 'default':
            if op == 'device_info': return device.default_info(store.existing, uid)
            if any(getattr(store, 'imports', {}).get(job) == 'pending' for job in store.active_jobs):
                raise RuntimeError('Wait for System Waydroid import to finish')
            return device.default_set(store.existing, uid, request['profile'], request['custom'], request['revision'])
        instance = store.backend.location(identifier)
        if op == 'device_info': return device.managed_info(instance, identifier)
        if record['state'] not in ('Allocated', 'Prepared', 'Stopped', 'Error') or not store.android.quiescent(identifier):
            raise RuntimeError('Stop this runtime completely before changing device identity')
        return device.managed_set(instance, identifier, request['profile'], request['custom'], request['revision'])
    if op.startswith('compatibility_'):
        action = op.removeprefix('compatibility_')
        if identifier == 'default':
            session, _ = store.existing.authorize(uid)
            if store.existing.state() != 'RUNNING':
                raise RuntimeError('Start System Waydroid before configuring app compatibility')
            generation = str(session.get('pid', '')) if isinstance(session, dict) else ''
            if not generation.isdecimal() or int(generation) <= 0:
                raise RuntimeError('System Waydroid generation is unavailable')
            payload = {'op': 'compatibility', 'action': action, 'generation': generation,
                       'package': request['package']}
            payload.update({key: request[key] for key in ('settings', 'revision') if key in request})
            result = compatibility.dispatch(store.existing.android, payload, identifier, generation)
            after, _ = store.existing.authorize(uid)
            if not isinstance(after, dict) or after.get('pid') != session.get('pid') or after.get('user_id') != session.get('user_id'):
                raise RuntimeError('System Waydroid changed during compatibility action; refresh before retrying')
            return result
        rpc = getattr(store, 'worker_rpc', None)
        with (rpc(identifier) if rpc else contextlib.nullcontext()):
            return compatibility.worker_request(store.android, identifier, action, request['package'],
                                                request.get('settings'), request.get('revision'))
    if op.startswith('shares_'):
        if identifier == 'default':
            adapter = default_shares.DefaultShares(store.existing.store, store.existing)
            if op == 'shares_get': return adapter.get(uid)
            if op == 'shares_remove': return adapter.remove(uid, request['name'])
            return adapter.set(uid, {key: request[key] for key in ('name', 'source', 'read_only')})
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
    if op.startswith('notifications_'):
        if identifier == 'default':
            raise RuntimeError('Desktop notifications are available for AnvilDroid-managed runtimes')
        path = store.backend.location(identifier) / 'notifications.json'
        policy = notifications.read_policy(path)
        if op == 'notifications_set':
            if type(request['enabled']) is not bool:
                raise RuntimeError('enabled must be boolean')
            policy = notifications.write_policy(path, dict(policy, enabled=request['enabled']))
        elif op == 'notifications_app_set':
            if type(request['allowed']) is not bool or not isinstance(request['package'], str) \
                    or not notifications.PACKAGE.fullmatch(request['package']) or len(request['package']) > 255:
                raise RuntimeError('Invalid package or setting')
            packages = dict(policy['packages'])
            if request['allowed']:
                packages.pop(request['package'], None)  # allowed is the default
            else:
                packages[request['package']] = False
            policy = notifications.write_policy(path, dict(policy, packages=packages))
        status = None
        if record['state'] in ('Running', 'Frozen'):
            try:
                worker = store.android.status(identifier)
                status = {'state': worker.get('notifications'), 'error': worker.get('notifications_error')}
            except (OSError, RuntimeError, ValueError):
                status = None
        return {'runtime_id': identifier, 'enabled': policy['enabled'],
                'blocked': sorted(name for name, allowed in policy['packages'].items() if not allowed),
                'status': status}
    if op.startswith('camera_'):
        if identifier == 'default':
            raise RuntimeError('Camera selection is for AnvilDroid-managed runtimes; System Waydroid passes every camera through')
        path = store.backend.location(identifier) / camera.POLICY
        if op == 'camera_set':
            if record['state'] not in ('Allocated', 'Prepared', 'Stopped', 'Error') or not store.android.quiescent(identifier):
                raise RuntimeError('Stop this runtime completely before changing cameras')
            policy = camera.write_policy(path, {'enabled': request['enabled'], 'devices': request['devices']})
        else:
            policy = camera.read_policy(path)
        active = None
        if record['state'] in ('Running', 'Frozen'):
            try:
                active = store.android.status(identifier).get('cameras')
            except (OSError, RuntimeError, ValueError):
                active = None
        return {'runtime_id': identifier, 'enabled': policy['enabled'], 'devices': policy['devices'],
                'available': camera.discover(), 'active': active}
    if op == 'diagnostics_collect':
        if type(request['include_logs']) is not bool:
            raise RuntimeError('include_logs must be boolean')
        runtime = {key: record[key] for key in ('id', 'state') if key in record}
        collectors = {'runtime': lambda: runtime,
                      'health': lambda: store._dispatch({'op': 'health', 'id': identifier}, uid),
                      'resources': lambda: store._dispatch({'op': 'resources_info', 'id': identifier}, uid),
                      'display': lambda: store._dispatch({'op': 'display_info', 'id': identifier}, uid),
                      'components': lambda: collect_components(),
                      'image': lambda: store.selections.get(identifier) or {'source': 'system_waydroid'}}
        session, session_error = None, None
        try:
            session = diagnostic_session(store, identifier, uid)
        except Exception as error:
            session_error = str(error)

        def collect_components():
            # The ordinary Google-services action resumes an idle runtime.
            # Diagnostics must keep a suspended guest asleep, or that action
            # invalidates its own pinned session halfway through collection.
            frozen = session is not None and (session[1] == 'FROZEN' if identifier == 'default'
                                               else session[2] == 'Frozen')
            if frozen:
                raise RuntimeError('Runtime is suspended; live component checks were skipped to keep it asleep')
            return component_report(store, identifier, uid)

        def verify_session():
            if session is None:
                raise RuntimeError(session_error)
            if diagnostic_session(store, identifier, uid) != session:
                raise RuntimeError('Runtime changed during diagnostic collection')

        live_states = ('Starting', 'Running', 'Frozen', 'Stopping')
        active = (identifier != 'default' and session is not None
                  and session[1] in live_states and session[2] in live_states)
        return diagnostics.collect(identifier, collectors, include_logs=request['include_logs'],
                                   log_directory=store.backend.location(identifier) if active else None,
                                   generation=session[3] if active else None, verify_session=verify_session,
                                   logs_unavailable_reason='No current managed runtime session; previous-session logs were not collected')
    raise RuntimeError('Unsupported feature operation')
