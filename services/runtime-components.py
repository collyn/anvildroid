"""Original component catalog backed by existing verified installation paths."""
import re


def _engine(provider):
    text = str(provider or '').lower()
    return 'libhoudini' if 'houdini' in text else 'libndk' if 'ndk' in text else None


def catalog(runtime_id, *, architecture, sdk, arm_report, google_report=None, selection=None):
    if not isinstance(runtime_id, str) or (runtime_id != 'default' and not re.fullmatch(r'r-[0-9a-f]{32}', runtime_id)):
        raise ValueError('Invalid component runtime ID')
    arm_report = arm_report if isinstance(arm_report, dict) else {}
    installed = _engine(arm_report.get('provider')) if arm_report.get('enabled') is True else None
    sources = arm_report.get('sources')
    sources = sources if isinstance(sources, list) else []
    google_report = google_report if isinstance(google_report, dict) else {}
    selection = selection if isinstance(selection, dict) else {}
    sdk = sdk if type(sdk) is int and 1 <= sdk <= 100 else None
    components = []
    for engine, label in (('libndk', 'libndk_translation'), ('libhoudini', 'libhoudini')):
        packages = []
        for source in sources[:128]:
            if not isinstance(source, dict) or source.get('engine') != engine:
                continue
            digest = source.get('sha256') or source.get('id')
            if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
                continue
            cached = source.get('installed') is True
            packages.append({'sha256': digest, 'version': str(source.get('version', 'Unknown'))[:160],
                             'label': str(source.get('label', label))[:240],
                             'verification': 'verify_on_install' if cached else 'verify_on_download', 'cached': cached})
        if engine == 'libndk' and arm_report.get('available') is True and not packages:
            # Default pinned package is verified by arm_set, not by catalog reads.
            packages.append({'sha256': None,
                             'version': 'Unknown (existing provider)' if runtime_id == 'default' else '0.2.3',
                             'label': 'Existing system libndk libraries' if runtime_id == 'default' else 'Pinned default libndk_translation',
                             'verification': 'existing_libraries' if runtime_id == 'default' else 'verify_on_install',
                             'cached': True})
        supported = architecture == 'x86_64' and arm_report.get('supported') is True
        # Bundled translators are primarily Android 13; existing backend checks
        # image compatibility independently before writing any configuration.
        compatible = supported and (sdk in (None, 33))
        reason = (None if compatible else 'ARM translation requires a supported x86_64 Android 13 runtime')
        is_installed = installed == engine
        cached = any(item['cached'] for item in packages)
        can_enable = compatible and cached
        if runtime_id == 'default':
            can_enable = can_enable and engine == 'libndk' and arm_report.get('restore_available') is True
            if compatible and cached and not can_enable:
                reason = 'Default can only restore an installed libndk provider previously disabled through AnvilDroid'
        components.append({'id': engine, 'name': label, 'kind': 'arm_translation',
                           'architectures': ['x86_64'], 'android_sdks': [33],
                           'conflicts': ['libhoudini' if engine == 'libndk' else 'libndk'],
                           'installed': is_installed, 'inherited': is_installed and bool(arm_report.get('inherited')),
                           'installed_sha256': arm_report.get('archive_sha256') if is_installed else None,
                           'installed_version': str(arm_report.get('provider')) if is_installed else None,
                           'status': 'configured' if is_installed else 'available' if compatible and cached else 'not_cached' if compatible else 'incompatible',
                           'can_enable': can_enable, 'can_disable': is_installed and supported,
                           'state_source': 'saved_configuration', 'observed_active': None,
                           'reason': reason, 'packages': packages, 'activation': 'runtime_restart',
                           'experimental': True})
    packages = (google_report or {}).get('packages')
    google_installed = (all(packages.get(name) is True for name in
                        ('com.android.vending', 'com.google.android.gms', 'com.google.android.gsf'))
                        if isinstance(packages, dict) else None)
    components.append({'id': 'gapps', 'name': 'Google Play services', 'kind': 'image',
                       'installed': google_installed, 'status': 'installed' if google_installed else 'not_installed' if google_installed is False else 'unknown',
                       'configured_image': (selection or {}).get('flavor'),
                       'conflicts': ['microg'], 'can_enable': False, 'can_disable': False,
                       'action': 'create_gapps_runtime', 'activation': 'new_runtime',
                       'reason': 'Use a verified GAPPS image to create a separate runtime; existing app data is preserved'})
    # These are deliberately not installable until an image-specific, verified
    # transactional installation contract exists. A catalog entry grants no root
    # script execution authority.
    for identifier, label in (('microg', 'MicroG'), ('smartdock', 'SmartDock'), ('magisk', 'Magisk')):
        components.append({'id': identifier, 'name': label, 'kind': 'extension',
                           'installed': None, 'status': 'unavailable', 'packages': [],
                           'conflicts': ['gapps'] if identifier == 'microg' else [],
                           'can_enable': False, 'can_disable': False,
                           'reason': 'A verified installer and runtime readback are not available'})
    return {'schema_version': 1, 'runtime_id': runtime_id, 'architecture': architecture,
            'android_sdk': sdk, 'components': components}


def arm_action(report, component_id, enabled, archive_sha256=None):
    if component_id not in ('libndk', 'libhoudini') or type(enabled) is not bool:
        raise ValueError('This component cannot be changed in place')
    row = next(item for item in report['components'] if item['id'] == component_id)
    if not row['can_enable' if enabled else 'can_disable']:
        raise ValueError(row.get('reason') or 'Component action is unavailable; refresh component state')
    request = {'op': 'arm_set', 'id': report['runtime_id'], 'enabled': enabled}
    if enabled:
        package = next((item for item in row['packages'] if item['sha256'] == archive_sha256), None)
        if package is None or not package['cached']:
            raise ValueError('Select a currently verified package for this component')
        if archive_sha256 is not None:
            request['archive_sha256'] = archive_sha256
    elif archive_sha256 is not None:
        raise ValueError('Disabling a component does not select an archive')
    return request
