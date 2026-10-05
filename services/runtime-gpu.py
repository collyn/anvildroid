"""Read-only graphics inventory; configured APIs do not prove acceleration."""
from pathlib import Path
import re
import json
import os
import stat


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


def software_supported(instance):
    try:
        report = json.loads((instance / 'support/compatibility.json').read_text())
        return report.get('software_renderer') == 'angle-pastel'
    except (OSError, ValueError):
        return False


def info(instance):
    path = instance / 'gpu.json'
    selected = json.loads(path.read_text()) if path.exists() else {'node': 'auto'}
    devices = inventory()
    for device in devices:
        experimental = device['vendor_id'] == '0x10de' or device['driver'] in ('nvidia', 'nouveau')
        device['experimental'] = experimental
        device['reason'] = 'NVIDIA · Experimental — not available yet' if experimental else None
        device['selectable'] = not experimental and device['available'] and device['driver'] in DRIVERS
    return {'selection': selected['node'], 'devices': devices,
            'software_supported': software_supported(instance)}


def choose(node):
    if not isinstance(node, str):
        raise RuntimeError('Invalid GPU selection')
    if node in ('auto', 'software'):
        return {'node': node}
    device = next((d for d in inventory() if d['node'] == node), None)
    if not device or device['vendor_id'] == '0x10de' or not device['available'] or device['driver'] not in DRIVERS:
        raise RuntimeError('GPU is unavailable or its driver is unsupported')
    metadata = Path(node).lstat()
    if not stat.S_ISCHR(metadata.st_mode) or os.major(metadata.st_rdev) != 226:
        raise RuntimeError('GPU must be a DRM character device')
    return {key: device[key] for key in ('node', 'identity', 'vendor_id', 'device_id', 'driver')}


def resolve(instance):
    path = instance / 'gpu.json'
    selected = json.loads(path.read_text()) if path.exists() else {'node': 'auto'}
    if selected['node'] == 'software':
        if not software_supported(instance):
            raise RuntimeError('This image has no validated ANGLE/Pastel software rendering support. Prepare a compatible image first.')
        return None
    if selected['node'] == 'auto':
        devices = [d for d in inventory() if d['available'] and d['driver'] in DRIVERS and d['vendor_id'] != '0x10de']
        if not devices:
            raise RuntimeError('No supported host GPU is available')
        device = choose(devices[0]['node'])
        # Navi 33 (0x73ff) is currently unsafe with the Waydroid minigbm
        # composer on the affected Mesa/kernel combination: starting the
        # allocator can wedge gfx_0.0.0 and reset the host GPU.  Refuse to
        # start rather than taking down the host display.  An explicit opt-in
        # is available for testing newer drivers.
        if (device.get('driver') == 'amdgpu' and device.get('device_id') == '0x73ff'
                and os.environ.get('ANVIL_GPU_SAFETY_GUARD') == '1'
                and os.environ.get('ANVIL_ALLOW_UNSTABLE_AMDGPU') != '1'):
            raise RuntimeError(
                'AMDGPU device 0x73ff is blocked: Waydroid minigbm triggered a host GPU reset. '
                'Update Mesa/kernel or set ANVIL_ALLOW_UNSTABLE_AMDGPU=1 to test at your own risk.')
        return device['node']
    current = choose(selected['node'])
    if (current.get('driver') == 'amdgpu' and current.get('device_id') == '0x73ff'
            and os.environ.get('ANVIL_GPU_SAFETY_GUARD') == '1'
            and os.environ.get('ANVIL_ALLOW_UNSTABLE_AMDGPU') != '1'):
        raise RuntimeError(
            'AMDGPU device 0x73ff is blocked: Waydroid minigbm triggered a host GPU reset. '
            'Update Mesa/kernel or set ANVIL_ALLOW_UNSTABLE_AMDGPU=1 to test at your own risk.')
    if current != selected:
        raise RuntimeError('Selected GPU identity changed. Select the GPU again before starting.')
    return current['node']


def properties(props, node):
    keys = ('gralloc.gbm.device=', 'ro.hardware.egl=', 'ro.hardware.gralloc=')
    if node is None:
        keys += ('ro.hardware.vulkan=', 'ro.waydroid.software_rendering=',
                 'ro.waydroid.override_props=', 'debug.hwui.renderer=')
        return [p for p in props if not p.startswith(keys)] + [
            'ro.hardware.egl=angle', 'ro.hardware.gralloc=default',
            'ro.hardware.vulkan=pastel', 'ro.waydroid.software_rendering=1',
            'ro.waydroid.override_props=false', 'debug.hwui.renderer=skiagl']
    return [p for p in props if not p.startswith(keys)] + [
        'gralloc.gbm.device=' + node, 'ro.hardware.egl=mesa',
        'ro.hardware.gralloc=minigbm_gbm_mesa']


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
    if not active_node or graphics.get('android_node') is None or not graphics.get('compositor_nodes'):
        return {'status': 'unknown', 'detail': 'Device usage has not been verified. Stop/start an older worker, then check again.'}
    if ((selected != 'auto' and selected != active_node)
            or graphics['android_node'] != active_node
            or graphics['compositor_nodes'] != [active_node]):
        return {'status': 'mismatch', 'detail': 'Saved, booted or observed GPU devices differ. Stop/start and check again.'}
    if graphics.get('renderer_kind') != 'reported':
        return {'status': 'unknown', 'detail': 'The device is open but the compositor renderer is unavailable.'}
    return {'status': 'verified', 'detail': 'Android compositor opens the selected GPU and reports a non-software renderer. This does not verify individual games.'}
