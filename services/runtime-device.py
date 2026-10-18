"""Per-runtime Android identity overrides; no image, ABI or hardware changes.

The catalog contains model identifiers, not downloaded scripts or ROM payloads.
Managed workers merge these properties at boot. System Waydroid uses its existing
stopped-only configuration transaction, with separate originals for each file.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile

FILE = 'device-profile.json'
FIELDS = ('brand', 'manufacturer', 'model', 'device', 'product', 'fingerprint')
PARTITIONS = ('system', 'vendor', 'product', 'odm', 'system_ext')
IDENTITY_KEYS = {f'ro.product.{prefix}{field}'
                 for prefix in ('', *(part + '.' for part in PARTITIONS))
                 for field in FIELDS[:5] if field != 'product'} | {
                     f'ro.product.{prefix}name' for prefix in ('', *(part + '.' for part in PARTITIONS))}
FINGERPRINT_KEYS = {'ro.build.fingerprint', *(f'ro.{part}.build.fingerprint'
                    for part in (*PARTITIONS, 'bootimage'))}
KEYS = IDENTITY_KEYS | FINGERPRINT_KEYS | {'ro.build.product', 'ro.system.build.product'}

# Public device model/codename facts. No guessed firmware fingerprints: profiles
# without a pinned fingerprint leave fingerprint generation to Android.
MODELS = (
    ('pixel-5', 'Google Pixel 5', 'google', 'Google', 'Pixel 5', 'redfin'),
    ('pixel-6', 'Google Pixel 6', 'google', 'Google', 'Pixel 6', 'oriole'),
    ('pixel-6-pro', 'Google Pixel 6 Pro', 'google', 'Google', 'Pixel 6 Pro', 'raven'),
    ('pixel-7', 'Google Pixel 7', 'google', 'Google', 'Pixel 7', 'panther'),
    ('pixel-7-pro', 'Google Pixel 7 Pro', 'google', 'Google', 'Pixel 7 Pro', 'cheetah'),
    ('pixel-8', 'Google Pixel 8', 'google', 'Google', 'Pixel 8', 'shiba'),
    ('pixel-8-pro', 'Google Pixel 8 Pro', 'google', 'Google', 'Pixel 8 Pro', 'husky'),
    ('pixel-9', 'Google Pixel 9', 'google', 'Google', 'Pixel 9', 'tokay'),
    ('pixel-9-pro', 'Google Pixel 9 Pro', 'google', 'Google', 'Pixel 9 Pro', 'caiman'),
    ('galaxy-s20-plus', 'Samsung Galaxy S20+ (SM-G9860)', 'samsung', 'samsung', 'SM-G9860', 'y2q'),
    ('galaxy-s21-ultra', 'Samsung Galaxy S21 Ultra (SM-G998B)', 'samsung', 'samsung', 'SM-G998B', 'p3s'),
    ('galaxy-s22-ultra', 'Samsung Galaxy S22 Ultra (SM-S908B)', 'samsung', 'samsung', 'SM-S908B', 'b0s'),
    ('galaxy-s23-ultra', 'Samsung Galaxy S23 Ultra (SM-S918B)', 'samsung', 'samsung', 'SM-S918B', 'dm3q'),
    ('galaxy-s24-ultra', 'Samsung Galaxy S24 Ultra (SM-S928B)', 'samsung', 'samsung', 'SM-S928B', 'e3q'),
    ('oneplus-9-pro', 'OnePlus 9 Pro (LE2123)', 'OnePlus', 'OnePlus', 'LE2123', 'OnePlus9Pro'),
    ('oneplus-11', 'OnePlus 11 (CPH2449)', 'OnePlus', 'OnePlus', 'CPH2449', 'OP594DL1'),
    ('oneplus-12', 'OnePlus 12 (CPH2581)', 'OnePlus', 'OnePlus', 'CPH2581', 'OP595DL1'),
    ('xiaomi-mi-11', 'Xiaomi Mi 11', 'Xiaomi', 'Xiaomi', 'M2011K2G', 'venus'),
    ('xiaomi-13', 'Xiaomi 13', 'Xiaomi', 'Xiaomi', '2211133G', 'fuxi'),
    ('xiaomi-14', 'Xiaomi 14', 'Xiaomi', 'Xiaomi', '23127PN0CG', 'houji'),
    ('redmi-note-12-pro', 'Redmi Note 12 Pro 5G', 'Redmi', 'Xiaomi', '22101316G', 'ruby'),
    ('poco-f5', 'POCO F5', 'POCO', 'Xiaomi', '23049PCD8G', 'marble'),
    ('huawei-p30-pro', 'HUAWEI P30 Pro (VOG-AL10)', 'HUAWEI', 'HUAWEI', 'VOG-AL10', 'HWVOG'),
    ('vivo-v2196a', 'vivo V2196A', 'vivo', 'vivo', 'V2196A', 'V2196A'),
    ('oppo-find-x5-pro', 'OPPO Find X5 Pro (CPH2305)', 'OPPO', 'OPPO', 'CPH2305', 'OP516FL1'),
    ('rog-phone-6', 'ASUS ROG Phone 6 (AI2201)', 'asus', 'asus', 'ASUS_AI2201_F', 'ASUS_AI2201'),
)
FINGERPRINTS = {
    'pixel-5': 'google/redfin/redfin:11/RQ3A.210905.001/7511028:user/release-keys',
    'pixel-7': 'google/panther/panther:13/TQ2A.230305.008.C1/9619669:user/release-keys',
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def catalog():
    return [{'id': identifier, 'name': label, 'values': dict(zip(FIELDS,
             (brand, manufacturer, model, device, device, FINGERPRINTS.get(identifier, ''))))}
            for identifier, label, brand, manufacturer, model, device in MODELS]


def selection(profile, custom):
    require(isinstance(profile, str), 'Invalid device profile')
    if profile == 'custom':
        require(isinstance(custom, dict) and set(custom) == set(FIELDS), 'Expected the six custom device fields')
        values = {}
        for field in FIELDS:
            value = custom[field]
            require(isinstance(value, str) and value == value.strip()
                    and len(value) <= (256 if field == 'fingerprint' else 91)
                    and all(32 <= ord(c) <= 126 for c in value)
                    and not any(c in value for c in '=\\'), 'Invalid device ' + field)
            require(bool(value) or field == 'fingerprint', 'Device ' + field + ' is required')
            values[field] = value
        fingerprint = values['fingerprint']
        require(not fingerprint or re.fullmatch(r'[^/\s:]+/[^/\s:]+/[^/\s:]+:[^/\s:]+/[^/\s:]+/[^/\s:]+:(?:user|userdebug|eng)/[\w.,-]+', fingerprint),
                'Use a complete Android build fingerprint or leave it empty')
        return {'profile': profile, 'custom': values}
    require(custom is None, 'Custom fields are only accepted for the custom profile')
    require(profile == 'default' or any(row['id'] == profile for row in catalog()), 'Unknown device profile')
    return {'profile': profile, 'custom': None}


def values_for(config):
    config = selection(config['profile'], config['custom'])
    if config['profile'] == 'default':
        return None
    return config['custom'] if config['profile'] == 'custom' else next(
        row['values'] for row in catalog() if row['id'] == config['profile'])


def overrides(config):
    values = values_for(config)
    if values is None:
        return {}
    props = {}
    for prefix in ('', *(part + '.' for part in PARTITIONS)):
        for field in FIELDS[:5]:
            props['ro.product.' + prefix + ('name' if field == 'product' else field)] = values[field]
    props.update({'ro.build.product': values['device'], 'ro.system.build.product': values['device']})
    if values['fingerprint']:
        props.update(dict.fromkeys(FINGERPRINT_KEYS, values['fingerprint']))
    return props


def revision(identifier, value):
    return hashlib.sha256(json.dumps([identifier, value], sort_keys=True).encode()).hexdigest()


def report(identifier, config, snapshot=None):
    return {'runtime_id': identifier, **config, 'values': values_for(config),
            'profiles': catalog(), 'revision': revision(identifier, [config, snapshot]),
            'activation': 'runtime_restart'}


def load(instance):
    try:
        fd = os.open(Path(instance) / FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return selection('default', None)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and not info.st_mode & 0o077 and info.st_nlink == 1 and info.st_size <= 8192,
                'Unsafe device profile configuration')
        value = json.load(stream)
    require(isinstance(value, dict) and set(value) == {'schema', 'profile', 'custom'}
            and type(value['schema']) is int and value['schema'] == 1, 'Invalid device profile configuration')
    return selection(value['profile'], value['custom'])


def managed_info(instance, identifier):
    return report(identifier, load(instance))


def managed_set(instance, identifier, profile, custom, expected_revision):
    config = selection(profile, custom)
    require(managed_info(instance, identifier)['revision'] == expected_revision,
            'Device configuration changed; refresh before applying')
    # Allocated runtimes have no image/support directory until Prepare.
    Path(instance).mkdir(mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.device-', dir=instance)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'schema': 1, **config}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, Path(instance) / FILE)
        directory = os.open(instance, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return managed_info(instance, identifier)


def boot_properties(instance, lines):
    props = overrides(load(instance))
    # No edits to the shared support files or either immutable Android image.
    # Host-generated support may have inherited Default's spoof. Identity must
    # come from this runtime's image or its explicit selection, never a sibling.
    return [line for line in lines if line.split('=', 1)[0].strip() not in KEYS] + [
        key + '=' + value for key, value in sorted(props.items())]


def parse(text):
    return {key.strip(): value.strip() for line in text.splitlines()
            if '=' in line and not line.lstrip().startswith('#')
            for key, value in [line.split('=', 1)] if key.strip() in KEYS}


def default_snapshot(existing):
    cfg = existing.config()
    snapshot = {'waydroid.cfg': {key: value for key, value in cfg.items('properties') if key in KEYS}
                if cfg.has_section('properties') else {}}
    for name in ('waydroid.prop', 'waydroid_base.prop'):
        try: snapshot[name] = parse(existing.read(existing.work / name))
        except FileNotFoundError: pass
    return cfg, snapshot


def default_info(existing, uid):
    _, saved = existing.authorize(uid)
    stored = saved.get('device_spoof', {})
    config = selection(stored.get('profile', 'default'), stored.get('custom'))
    _, snapshot = default_snapshot(existing)
    return report('default', config, [stored, snapshot])


def default_set(existing, uid, profile, custom, expected_revision):
    config = selection(profile, custom)
    _, saved = existing.authorize(uid)
    require(bool(saved), 'Enable management for Default runtime before changing device identity')
    existing.stopped()
    require(default_info(existing, uid)['revision'] == expected_revision,
            'Device configuration changed; refresh before applying')
    cfg, snapshot = default_snapshot(existing)
    stored = saved.get('device_spoof', {})
    originals = stored.get('originals', snapshot)
    previous = overrides(selection(stored.get('profile', 'default'), stored.get('custom')))
    props = overrides(config)
    touched = set(previous) | set(props)
    if not touched:
        return default_info(existing, uid)
    require(isinstance(originals, dict) and set(originals) == set(snapshot),
            'Original Waydroid property files changed; check the saved device backup')
    changes = {}
    if not cfg.has_section('properties'):
        cfg.add_section('properties')
    for name, original in originals.items():
        require(name in snapshot and isinstance(original, dict) and set(original) <= KEYS,
                'Invalid original device configuration')
        restore = {key: props.get(key, original.get(key)) for key in touched}
        if name == 'waydroid.cfg':
            for key, value in restore.items():
                if value is None: cfg.remove_option('properties', key)
                else: cfg.set('properties', key, value)
            stream = io.StringIO(); cfg.write(stream)
            changes[existing.work / name] = stream.getvalue()
        else:
            lines = [line for line in existing.read(existing.work / name).splitlines()
                     if line.split('=', 1)[0].strip() not in touched]
            changes[existing.work / name] = '\n'.join(lines + [key + '=' + value
                for key, value in sorted(restore.items()) if value is not None]) + '\n'
    updated = dict(saved)
    if profile == 'default':
        updated.pop('device_spoof', None)
    else:
        updated['device_spoof'] = {**config, 'originals': originals}
    changes[existing.metadata] = json.dumps(updated)
    # Includes metadata in the same rollback transaction as all three prop files.
    existing.write_transaction(changes)
    return default_info(existing, uid)
