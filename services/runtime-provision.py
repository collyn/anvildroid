#!/usr/bin/python3
"""Build pinned support for newly extracted official images in a private namespace."""
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
_format_spec = importlib.util.spec_from_file_location('runtime_image_format', Path(__file__).with_name('runtime-image-format.py'))
image_format = importlib.util.module_from_spec(_format_spec)
_format_spec.loader.exec_module(image_format)


ASSETS = Path('/usr/local/lib/anvildroid-controller')
spec = importlib.util.spec_from_file_location('images', Path(__file__).with_name('runtime-images.py'))
images = importlib.util.module_from_spec(spec); spec.loader.exec_module(images)

def run(*args):
    result = subprocess.run(list(map(str,args)), capture_output=True, text=True, timeout=90)
    if result.returncode: raise RuntimeError(str(args[0]) + ': ' + result.stderr[-1200:])
    return result.stdout.strip()


def caption_compatibility(framework, layout):
    """Do not substitute IDs: DecorCaptionView expects its own framework IDs."""
    root = ET.parse(layout).getroot()
    required = {'layout/decor_caption'}
    for element in root.iter():
        for value in element.attrib.values():
            if value.startswith('@*android:'):
                required.add(value.removeprefix('@*android:'))
    resources = run('aapt', 'dump', 'resources', framework)
    available = set(re.findall(r'\bandroid:([A-Za-z0-9_]+/[A-Za-z0-9_]+)\b', resources))
    missing = sorted(required - available)
    return {'caption_mode': 'android' if missing else 'host',
            'caption_detail': ('Keeping the image\'s Android caption; framework lacks ' + ', '.join(missing))
                              if missing else 'Framework supports the AnvilDroid caption overlay'}

def build_overlay(overlay, system_root, require_caption=True, vendor_root=None,
                  safe_codec_fallback=False, flavor=None):
    """Populate support for a Waydroid image with the expected composer contract:
    the LD_PRELOAD bridge marker in init.waydroid.rc, the bridge itself, the task
    companion scripts and (when Android build tools exist) the zero-height caption.
    Shared by the downloaded-image build and the copied-host-image snapshot."""
    props = (system_root / 'system/build.prop').read_text()
    version = re.search(r'^ro.build.version.sdk=(\d+)\s*$', props, re.MULTILINE)
    if not version or int(version[1]) < 33:
        raise RuntimeError('Waydroid Android SDK 33 or newer is required')
    sdk = int(version[1])
    framework = system_root / 'system/framework/framework-res.apk'
    if not framework.is_file(): raise RuntimeError('Image is missing the Android framework required for desktop support')
    init = system_root / 'system/etc/init/init.waydroid.rc'
    marker = 'service vendor.hwcomposer-2-1 /vendor/bin/hw/android.hardware.graphics.composer@2.1-service --desktop_file_hint=Waydroid.desktop\n'
    text = init.read_text()
    if text.count(marker) != 1: raise RuntimeError('Unsupported composer service')
    overlay.parent.mkdir(parents=True, exist_ok=True)
    compatibility = {
        'flavor': flavor,
        'sdk': sdk, 'status': 'experimental' if sdk > 33 else 'baseline',
        'detail': 'Newer Android: desktop bridge, app controls and boot need validation with this image.' if sdk > 33 else 'Android 13 compatibility baseline',
        'caption_mode': 'android', 'caption_detail': 'Keeping the image caption; overlay tools unavailable',
        'desktop_bridge': 'anvildroid',
    }
    surfaceflinger = system_root / 'system/bin/surfaceflinger'
    native_metadata = (surfaceflinger.is_file() and
                       b'vendor.waydroid.display@' in surfaceflinger.read_bytes())
    compatibility['presentation_mode'] = 'app-windows' if native_metadata else 'android-display'
    # WayDroidATV images use a newer HWC Wayland path which rejects the
    # AnvilDroid decoration bridge's shared-memory format negotiation.
    if 'WayDroidATV' in props or 'eng.minh' in props:
        compatibility['desktop_bridge'] = 'native-wayland'
    if vendor_root is not None:
        initializer = vendor_root / 'bin/waydroid-init'
        if (initializer.is_file() and initializer.stat().st_size <= 32 * 1024**2
                and (vendor_root / 'lib64/hw/vulkan.pastel.so').is_file()
                and (system_root / 'system/lib64/libEGL_angle.so').is_file()
                and (system_root / 'system/lib64/libGLESv2_angle.so').is_file()):
            binary = initializer.read_bytes()
            if (compatibility['desktop_bridge'] == 'native-wayland'
                    or all(key in binary for key in (b'ro.waydroid.software_rendering', b'ro.waydroid.override_props'))):
                compatibility['software_renderer'] = 'angle-pastel'
    # Some Android 15 custom images ship a malformed C2 audio capability
    # which aborts mediaserver while MediaCodecList is built. Keep the
    # vendor codec entry point empty so Android can boot; codec support can
    # be restored after the image is validated.
    if safe_codec_fallback and sdk >= 35:
        codec = overlay / 'vendor/etc/media_codecs.xml'
        codec.parent.mkdir(parents=True, exist_ok=True)
        codec.write_text('<?xml version="1.0" encoding="utf-8"?>\n<MediaCodecs/>\n')
        compatibility['codec_mode'] = 'safe-empty-vendor-codecs'
    target = overlay / 'system/etc/init/init.waydroid.rc'; target.parent.mkdir(parents=True, exist_ok=True)
    preload = ('/vendor/lib64/libanvildroid-hwc-shim.so'
               if compatibility['desktop_bridge'] == 'native-wayland'
               else '/vendor/lib64/libanvildroid-window.so')
    target.write_text(text.replace(marker, marker + '    setenv LD_PRELOAD ' + preload + '\n'))
    bridge = overlay / 'vendor/lib64/libanvildroid-window.so'; bridge.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / 'libanvildroid-window.so', bridge)
    bridge.chmod(0o644)
    # Keep the RPC-pool shim available for stock custom composers too. It
    # does not interpose Wayland window calls; it only prevents Android 15
    # HIDL from aborting when the composer configures Binder pools twice.
    shim = overlay / 'vendor/lib64/libanvildroid-hwc-shim.so'
    shutil.copyfile(ASSETS / 'libanvildroid-hwc-shim.so', shim)
    shim.chmod(0o644)
    if require_caption or (shutil.which('aapt') and shutil.which('apksigner')):
        compatibility.update(caption_compatibility(framework, ASSETS / 'provision/overlay/res/layout/decor_caption.xml'))
    if compatibility['caption_mode'] == 'host':
        # Rebuild caption resources against this image's framework, never the host framework.
        caption = overlay / 'vendor/overlay/AnvilDroidCaption/AnvilDroidCaption.apk'; caption.parent.mkdir(parents=True, exist_ok=True)
        unsigned = overlay.parent / 'caption.apk'
        run('aapt', 'package', '-f', '-M', ASSETS / 'provision/overlay/AndroidManifest.xml', '-S', ASSETS / 'provision/overlay/res',
            '-I', system_root / 'system/framework/framework-res.apk', '-F', unsigned)
        run('apksigner', 'sign', '--ks', ASSETS / 'provision/overlay-key.p12', '--ks-pass', 'pass:anvildroid', '--out', caption, unsigned)
        caption.chmod(0o644)
        unsigned.unlink(missing_ok=True)
    (overlay.parent / 'compatibility.json').write_text(json.dumps(compatibility))
    for name, subdirectory in (('anvildroid-apps.rc', 'etc/init'), ('anvildroid-tasks.rc', 'etc/init'),
                               ('anvildroid-apps.sh', 'bin'), ('anvildroid-tasks.sh', 'bin')):
        target = overlay / 'vendor' / subdirectory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ASSETS / 'provision' / name, target)
        target.chmod(0o644)
    # Newer Waydroid images abort init when cpuset v1 profiles cannot be applied
    # (the container is cgroup-v2-only; no /dev/cpuset exists). Strip those
    # profiles so task profile application uses the v2 hierarchy instead.
    profiles = system_root / 'system/etc/task_profiles.json'
    if profiles.exists():
        data = json.loads(profiles.read_text())
        cpuset = {a['Name'] for a in data.get('Attributes', []) if a.get('Controller') == 'cpuset'}

        def references_cpuset(action):
            # Actions carry their controller inside "Params" (JoinCgroup,
            # SetAttribute, ...), not as a flat "Controller" field.
            params = action.get('Params', {}) if isinstance(action, dict) else {}
            if params.get('Controller') == 'cpuset':
                return True
            attribute = params.get('Attribute', {}) if isinstance(params.get('Attribute'), dict) else {}
            return attribute.get('Name') in cpuset

        if cpuset:
            data['Attributes'] = [a for a in data.get('Attributes', []) if a.get('Controller') != 'cpuset']
            for profile in data.get('Profiles', []):
                profile['Actions'] = [action for action in profile.get('Actions', [])
                                      if not references_cpuset(action)]
            data['Aggregations'] = [a for a in data.get('Aggregations', [])
                                    if not references_cpuset(a)]
            target = overlay / 'system/etc/task_profiles.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, indent=2))
            target.chmod(0o644)


def build(instance, flavor):
    if os.geteuid() != 0 or flavor not in ('VANILLA', 'GAPPS', 'CUSTOM'): raise RuntimeError('Invalid support request')
    if os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'): raise RuntimeError('Private mount namespace required')
    images.ImageBackend.check_directory(instance)
    os.umask(0o022)  # Android must be able to traverse newly generated overlay directories.
    backend = images.ImageBackend(instance.parent.parent, Path('/var/lib/waydroid/images'))
    manifest = backend.inspect(instance.name)
    if not manifest: raise RuntimeError('Verified images required')
    if (instance / 'support').exists():
        if json.loads((instance / 'support/images.json').read_text()) != manifest['images']: raise RuntimeError('Support/image mismatch')
        # Rebuild support generated by older AnvilDroid versions so Android 15
        # custom images receive the codec crash workaround.
        compatibility_file = instance / 'support/compatibility.json'
        compatibility = json.loads(compatibility_file.read_text()) if compatibility_file.exists() else {}
        if not (flavor == 'CUSTOM' and compatibility.get('sdk', 0) >= 35
                and (compatibility.get('codec_mode') != 'safe-empty-vendor-codecs'
                     or 'desktop_bridge' not in compatibility)):
            return
        shutil.rmtree(instance / 'support')
    staging = Path(tempfile.mkdtemp(prefix='.support-build-', dir=instance))
    mounts = []
    try:
        lower = staging / 'system'; lower.mkdir()
        vendor = staging / 'vendor'; vendor.mkdir()
        for image, target in (('system.img', lower), ('vendor.img', vendor)):
            run('mount', *image_format.mount_options(instance / 'prepared' / image), instance / 'prepared' / image, target)
            mounts.append(target)
        support = staging / 'support'; support.mkdir()
        overlay = support / 'overlay'
        build_overlay(overlay, lower, vendor_root=vendor,
                      safe_codec_fallback=(flavor == 'CUSTOM'), flavor=flavor)
        props = Path('/var/lib/waydroid/waydroid.prop').read_text().splitlines()
        # No ARM properties without the matching native translation libraries.
        drop = ('ro.product.cpu.', 'ro.dalvik.vm.', 'ro.enable.native.bridge.', 'ro.vendor.enable.native.bridge.', 'ro.ndk_translation.', 'waydroid.system_ota=')
        props = [p for p in props if not p.startswith(drop)]
        if flavor != 'CUSTOM':
            props += ['waydroid.system_ota=https://ota.waydro.id/system/lineage/waydroid_x86_64/' + flavor + '.json']
        (support / 'waydroid.prop').write_text('\n'.join(props) + '\n')
        for name in ('config', 'waydroid.seccomp'):
            shutil.copyfile(Path('/var/lib/waydroid/lxc/waydroid') / name, support / name)
        (support / 'images.json').write_text(json.dumps(manifest['images']))
        for mount in reversed(mounts): run('umount', mount)
        mounts.clear()
        for directory, _, files in os.walk(support):
            for name in files:
                with (Path(directory) / name).open('rb') as stream: os.fsync(stream.fileno())
            images.ImageBackend.sync_directory(Path(directory))
        os.rename(support, instance / 'support')
        images.ImageBackend.sync_directory(instance)
    finally:
        for mount in reversed(mounts): run('umount', mount)
        shutil.rmtree(staging)

if __name__ == '__main__':
    if len(sys.argv) != 3: raise SystemExit('Expected instance and image flavor')
    build(Path(sys.argv[1]), sys.argv[2])
