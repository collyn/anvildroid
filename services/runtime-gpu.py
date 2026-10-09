"""Read-only graphics inventory; configured APIs do not prove acceleration."""
from pathlib import Path
import re
import json
import os
import stat
import importlib.util

_nv_spec = importlib.util.spec_from_file_location('runtime_nvidia', Path(__file__).with_name('runtime-nvidia.py'))
nvidia = importlib.util.module_from_spec(_nv_spec)
_nv_spec.loader.exec_module(nvidia)


def inventory(sysfs=Path('/sys/class/drm'), devices=Path('/dev/dri')):
    result = []
    for node in sorted(sysfs.glob('renderD*')):
        if not re.fullmatch(r'renderD\d+', node.name):
            continue
        def value(name):
            try: return (node / 'device' / name).read_text().strip()[:128]
            except OSError: return None
        driver_path = node / 'device/driver'
        driver = driver_path.resolve().name if driver_path.exists() else None
        result.append({'node': str(devices / node.name), 'driver': driver,
                       'vendor_id': value('vendor'), 'device_id': value('device'),
                       'identity': str((node / 'device').resolve()),
                       'available': (devices / node.name).exists()})
    # Proprietary NVIDIA exposes /dev/nvidia0 rather than a DRM render node
    # unless nvidia-drm.modeset is enabled. Keep it visible so the UI can show
    # the exact prerequisite failure instead of hiding the GPU entirely.
    nvidia_root = Path('/proc/driver/nvidia/gpus') if sysfs == Path('/sys/class/drm') else sysfs / 'nvidia-gpus'
    for information in sorted(nvidia_root.glob('*/information')):
        values = {}
        try:
            for line in information.read_text().splitlines():
                key, _, value = line.partition(':')
                values[key.strip()] = value.strip()
        except OSError:
            continue
        bus = values.get('Bus Location')
        if not bus or not re.fullmatch(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]', bus): continue
        identity = (Path('/sys/bus/pci/devices') / bus).resolve()
        device_id = None
        try: device_id = identity.joinpath('device').read_text().strip()
        except OSError: pass
        minor = values.get('Device Minor', '')
        if not minor.isdecimal(): continue
        node = Path('/dev/nvidia' + minor)
        if not any(d['identity'] == str(identity) for d in result):
            result.append({'node': str(node), 'driver': 'nvidia', 'vendor_id': '0x10de',
                           'device_id': device_id, 'identity': str(identity),
                           'available': node.exists()})
    return result


def renderer(surfaceflinger):
    for line in surfaceflinger.splitlines():
        if line.strip().startswith('GLES:'):
            description = line.strip()[5:].strip()[:1024]
            if not description: break
            software = any(name in description.lower() for name in
                           ('llvmpipe', 'softpipe', 'swiftshader', 'software rasterizer', 'lavapipe', 'pastel'))
            return {'gles': description, 'renderer_kind': 'software' if software else 'reported',
                    'scope': 'Android compositor; individual games may use a different API.'}
    return {'gles': None, 'renderer_kind': 'unknown',
            'scope': 'SurfaceFlinger did not report a GLES renderer.'}


# Only Mesa DRM drivers supported by the current Waydroid hardware path.
DRIVERS = {'amdgpu', 'radeon', 'i915', 'xe', 'panfrost', 'msm', 'vc4'}


def is_nvidia(node):
    return any(d['node'] == node and d['driver'] == 'nvidia' for d in inventory())


def nvidia_ready(report):
    required = {'kernel_module', 'driver', 'modeset', 'udmabuf', 'payload'}
    return bool(report and all(next((c['status'] for c in report['checks'] if c['id'] == key), None) == 'ok'
                               for key in required))


def software_supported(instance):
    # Stock Waydroid uses SwiftShader; custom ATV images may use ANGLE/Pastel.
    # Availability of the selected libraries is checked on the mounted image.
    return True


def software_backend(instance):
    try:
        report = json.loads((instance / 'support/compatibility.json').read_text())
        if report.get('software_renderer') in ('angle-pastel', 'swiftshader'):
            return report['software_renderer']
    except (OSError, ValueError):
        pass
    # Official GAPPS images commonly ship ANGLE/Pastel without the optional
    # compatibility metadata. SwiftShader is only selected explicitly.
    return 'angle-pastel'


def validate_software(root, backend):
    libraries = (('libEGL_angle.so', 'libGLESv2_angle.so', 'vulkan.pastel.so')
                 if backend == 'angle-pastel' else
                 ('libEGL_swiftshader.so', 'libGLESv2_swiftshader.so'))
    paths = [root / partition / 'lib64' / sub
             for partition in ('system', 'vendor', 'system_ext', 'product')
             for sub in ('', 'egl', 'hw')]
    missing = [name for name in libraries if not any((p / name).is_file() for p in paths)]
    if missing:
        raise RuntimeError('CPU rendering requires ' + backend + ' libraries missing from this image: '
                           + ', '.join(missing) + '. Use an image with software rendering support.')


def detect_software(root, preferred):
    errors = []
    for backend in dict.fromkeys((preferred, 'angle-pastel', 'swiftshader')):
        try:
            validate_software(root, backend)
            return backend
        except RuntimeError as error:
            errors.append(str(error))
    raise RuntimeError('No complete CPU renderer found in the mounted image. ' + ' '.join(errors))


def info(instance):
    path = instance / 'gpu.json'
    selected = json.loads(path.read_text()) if path.exists() else {'node': 'auto'}
    devices = inventory()
    nvidia_status = nvidia.preflight() if any(d['vendor_id'] == '0x10de' for d in devices) else None
    for device in devices:
        experimental = device['vendor_id'] == '0x10de' or device['driver'] in ('nvidia', 'nouveau')
        device['experimental'] = experimental
        device['reason'] = ('NVIDIA · Experimental — ' + nvidia_status['detail']
                            if experimental and nvidia_status else
                            'NVIDIA · Experimental — backend not enabled' if experimental else None)
        device['selectable'] = device['available'] and ((not experimental and device['driver'] in DRIVERS)
                                  or (device['driver'] == 'nvidia' and device['node'].startswith('/dev/dri/') and nvidia_ready(nvidia_status)))
        if device['driver'] == 'nvidia' and device['node'].startswith('/dev/nvidia'):
            device['reason'] = 'NVIDIA detected, but no DRM render node. Enable nvidia-drm.modeset=1 on the host.'
    return {'selection': selected['node'], 'devices': devices,
            'software_supported': software_supported(instance), 'nvidia': nvidia_status}


def choose(node):
    if not isinstance(node, str):
        raise RuntimeError('Invalid GPU selection')
    if node in ('auto', 'software'):
        return {'node': node}
    device = next((d for d in inventory() if d['node'] == node), None)
    venus = bool(device and device['driver'] == 'nvidia')
    if venus:
        if not node.startswith('/dev/dri/'):
            raise RuntimeError('NVIDIA DRM render node missing; enable nvidia-drm.modeset=1 on the host')
        if not nvidia_ready(nvidia.preflight()):
            raise RuntimeError('NVIDIA Venus prerequisites are not ready; inspect Graphics compatibility checks')
    if not device or not device['available'] or (not venus and (device['vendor_id'] == '0x10de' or device['driver'] not in DRIVERS)):
        raise RuntimeError('GPU is unavailable or its driver is unsupported')
    metadata = Path(node).lstat()
    if not stat.S_ISCHR(metadata.st_mode) or os.major(metadata.st_rdev) != 226:
        raise RuntimeError('GPU must be a supported graphics character device')
    return {key: device[key] for key in ('node', 'identity', 'vendor_id', 'device_id', 'driver')}


def resolve(instance):
    path = instance / 'gpu.json'
    selected = json.loads(path.read_text()) if path.exists() else {'node': 'auto'}
    if selected['node'] == 'software':
        return None
    if selected['node'] == 'auto':
        # Prefer supported hardware; NVIDIA-only/no-DRM hosts use CPU rendering.
        devices = [d for d in inventory() if d['available'] and d['driver'] in DRIVERS and d['vendor_id'] != '0x10de']
        if not devices:
            return None
        device = choose(devices[0]['node'])
        # Navi 33 (0x73ff) is currently unsafe with the Waydroid minigbm
        # composer on the affected Mesa/kernel combination: starting the
        # allocator can wedge gfx_0.0.0 and reset the host GPU. Use CPU when
        # this safety guard is enabled. An explicit opt-in
        # is available for testing newer drivers.
        if (device.get('driver') == 'amdgpu' and device.get('device_id') == '0x73ff'
                and os.environ.get('ANVIL_GPU_SAFETY_GUARD') == '1'
                and os.environ.get('ANVIL_ALLOW_UNSTABLE_AMDGPU') != '1'):
            return None
        return device['node']
    try:
        current = choose(selected['node'])
    except RuntimeError:
        if selected.get('driver') == 'nvidia' or selected['node'].startswith('/dev/nvidia'):
            raise
        # A saved render node may disappear when moving a runtime to another host.
        return None
    if (current.get('driver') == 'amdgpu' and current.get('device_id') == '0x73ff'
            and os.environ.get('ANVIL_GPU_SAFETY_GUARD') == '1'
            and os.environ.get('ANVIL_ALLOW_UNSTABLE_AMDGPU') != '1'):
        return None
    if current != selected:
        raise RuntimeError('Selected GPU identity changed. Select the GPU again before starting.')
    return current['node']


def properties(props, node, software='angle-pastel'):
    if node and is_nvidia(node):
        return nvidia.properties(props)
    keys = ('mesa.vn.debug=', 'mesa.vtest.socket.name=', 'debug.renderengine.backend=',
            'debug.angle.feature_overrides_disabled=',
            'gralloc.gbm.device=', 'ro.hardware.egl=', 'ro.hardware.gralloc=',
            'ro.hardware.vulkan=', 'ro.waydroid.software_rendering=',
            'ro.waydroid.override_props=', 'debug.hwui.renderer=')
    if node is None:
        if software == 'swiftshader':
            return [p for p in props if not p.startswith(keys)] + [
                'ro.hardware.egl=swiftshader', 'ro.hardware.gralloc=default',
                'ro.waydroid.software_rendering=1', 'ro.waydroid.override_props=false',
                'debug.hwui.renderer=skiagl']
        return [p for p in props if not p.startswith(keys)] + [
            'ro.hardware.egl=angle', 'ro.hardware.gralloc=default',
            'ro.hardware.vulkan=pastel', 'ro.waydroid.software_rendering=1',
            'ro.waydroid.override_props=false', 'debug.hwui.renderer=skiagl']
    driver = next((d['driver'] for d in inventory() if d['node'] == node), None)
    vulkan = {'i915': 'intel', 'xe': 'intel', 'amdgpu': 'radeon',
              'radeon': 'radeon', 'msm': 'freedreno', 'vc4': 'broadcom'}.get(driver)
    return [p for p in props if not p.startswith(keys)] + [
        'gralloc.gbm.device=' + node, 'ro.hardware.egl=mesa',
        'ro.hardware.gralloc=minigbm_gbm_mesa',
        'ro.waydroid.software_rendering=0', 'ro.waydroid.override_props=false',
        'debug.hwui.renderer=skiagl'] + (['ro.hardware.vulkan=' + vulkan] if vulkan else [])


# One bounded attach: inspect only SurfaceFlinger, never arbitrary app processes.
PROBE_COMMAND = r'''dumpsys SurfaceFlinger | grep '^GLES:'
printf '\nANVIL_GPU_PROPERTY='
getprop gralloc.gbm.device
for p in $(pidof surfaceflinger); do
    ls -l /proc/$p/fd | sed -n 's@.* -> \(/dev/dri/renderD[0-9]*\)$@ANVIL_GPU_OPEN=\1@p'
done
'''



def probe_report(output):
    report = renderer(output)
    nodes = []
    configured = None
    for line in output.splitlines():
        if line.startswith('ANVIL_GPU_PROPERTY='):
            value = line.partition('=')[2]
            if re.fullmatch(r'/dev/dri/renderD\d+', value): configured = value
        elif line.startswith('ANVIL_GPU_OPEN='):
            value = line.partition('=')[2]
            if re.fullmatch(r'/dev/dri/renderD\d+', value): nodes.append(value)
    report.update(android_node=configured, compositor_nodes=sorted(set(nodes)))
    return report


def verification(selected, active_node, graphics):
    if not graphics:
        return {'status': 'unknown', 'detail': 'Renderer not measured. Run Check GPU while Android is running.'}
    if graphics.get('renderer_kind') == 'software':
        return {'status': 'software', 'detail': 'Android compositor reports a CPU software renderer.'}
    if 'venus' in (graphics.get('gles') or '').lower() and 'nvidia' in (graphics.get('gles') or '').lower():
        return {'status': 'unknown', 'detail': 'Android reports NVIDIA via Venus. The host physical GPU and buffer import path still require independent verification.'}
    if not active_node or graphics.get('android_node') is None or not graphics.get('compositor_nodes'):
        return {'status': 'unknown', 'detail': 'Device usage has not been verified. Stop/start an older worker, then check again.'}
    if ((selected != 'auto' and selected != active_node)
            or graphics['android_node'] != active_node
            or graphics['compositor_nodes'] != [active_node]):
        return {'status': 'mismatch', 'detail': 'Saved, booted or observed GPU devices differ. Stop/start and check again.'}
    if graphics.get('renderer_kind') != 'reported':
        return {'status': 'unknown', 'detail': 'The device is open but the compositor renderer is unavailable.'}
    return {'status': 'verified', 'detail': 'Android compositor opens the selected GPU and reports a non-software renderer. This does not verify individual games.'}
