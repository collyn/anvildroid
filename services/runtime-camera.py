"""Host cameras for managed runtimes (V4L2 passthrough, off by default).

Only video-capture nodes are offered (a webcam usually also exposes a metadata
node). Cameras are remembered by their /dev/v4l/by-id name, which survives
/dev/videoN renumbering after replugging or reboots.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import struct

POLICY = 'camera.json'
VIDIOC_QUERYCAP = 0x80685600            # _IOR('V', 0, struct v4l2_capability[104])
CAP_VIDEO_CAPTURE = 0x00000001
CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
CAP_DEVICE_CAPS = 0x80000000
MAX_SELECTED = 8
CAMERA_ID = re.compile(r'[A-Za-z0-9_.:+@-]{1,255}')
NODE = re.compile(r'video([0-9]{1,3})')


def capabilities(node):
    """The node's own V4L2 capabilities, or None when it cannot be queried."""
    try:
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        buffer = bytearray(104)
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, buffer, True)
    except OSError:
        return None
    finally:
        os.close(fd)
    caps = int.from_bytes(buffer[84:88], 'little')
    device_caps = int.from_bytes(buffer[88:92], 'little')
    return device_caps if caps & CAP_DEVICE_CAPS else caps


def discover(dev=Path('/dev'), sysfs=Path('/sys/class/video4linux'), by_id=Path('/dev/v4l/by-id'), query=capabilities):
    """Capture-capable cameras: [{'id', 'name', 'node'}], ordered by node number."""
    stable = {}
    try:
        for link in by_id.iterdir():
            try:
                stable.setdefault(link.resolve().name, link.name)
            except OSError:
                continue
    except OSError:
        pass
    cameras = []
    nodes = [path for path in dev.glob('video*') if NODE.fullmatch(path.name)]
    for node in sorted(nodes, key=lambda path: int(NODE.fullmatch(path.name).group(1))):
        caps = query(node)
        if caps is None or not caps & (CAP_VIDEO_CAPTURE | CAP_VIDEO_CAPTURE_MPLANE):
            continue
        try:
            name = (sysfs / node.name / 'name').read_text(errors='replace').strip()
        except OSError:
            name = ''
        name = ''.join(ch for ch in name if ch.isprintable())[:120] or node.name
        identifier = stable.get(node.name) or f'{node.name}:{re.sub(r"[^A-Za-z0-9_.+@-]", "_", name)}'[:255]
        cameras.append({'id': identifier, 'name': name, 'node': node.name})
    return cameras


def validate(policy):
    if not isinstance(policy, dict) or set(policy) - {'enabled', 'devices'}:
        raise ValueError('Invalid camera settings')
    enabled, devices = policy.get('enabled', False), policy.get('devices', [])
    if type(enabled) is not bool or not isinstance(devices, list) or len(devices) > MAX_SELECTED \
            or len(set(devices)) != len(devices) \
            or not all(isinstance(item, str) and CAMERA_ID.fullmatch(item) for item in devices):
        raise ValueError('Invalid camera selection')
    return {'enabled': enabled, 'devices': list(devices)}


def read_policy(path):
    """Saved settings; a missing or damaged file means cameras stay off."""
    try:
        return validate(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        return {'enabled': False, 'devices': []}


def write_policy(path, policy):
    policy = validate(policy)
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(policy))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return policy


def selected_nodes(policy, cameras):
    """Device nodes to pass to Android. An empty selection means every camera;
    selected cameras that are unplugged are skipped."""
    if not policy.get('enabled'):
        return []
    if not policy.get('devices'):
        return [camera['node'] for camera in cameras]
    wanted = set(policy['devices'])
    return [camera['node'] for camera in cameras if camera['id'] in wanted]


GRALLOC_ALIAS = 'anvilgbm'
HAL_DIRS = ('lib64/hw', 'lib/hw')


def boot_properties(props, nodes, legacy_gralloc=False):
    """Select Android's V4L2 camera HAL only when a camera is passed through.

    The external camera HAL outputs YV12 preview buffers. minigbm's gbm_mesa
    allocator lays YV12 out in a way Mesa cannot import (SurfaceFlinger:
    "Failed to create a valid texture ... format:842094169"), which shows as a
    green picture. While a camera is attached the image's legacy gbm gralloc
    (allocator 2.0) is used instead. The image's init rewrites
    ro.hardware.gralloc=gbm to minigbm_gbm_mesa, so the legacy module is
    selected through an alias name (see gralloc_alias). NVIDIA and software
    rendering use other gralloc values and are left unchanged."""
    props = [line for line in props if not line.startswith('ro.hardware.camera=')]
    if not nodes:
        props = [line for line in props if not line.startswith('media.settings.xml=')]
        return props
    if legacy_gralloc:
        props = ['ro.hardware.gralloc=' + GRALLOC_ALIAS if line == 'ro.hardware.gralloc=minigbm_gbm_mesa' else line
                 for line in props]
    # This image's libmedia searches product/odm/system for media profiles,
    # never vendor/etc, so recording would use the built-in QCIF/CIF table
    # that CameraX rejects ("No supported EncoderProfiles").
    props = [line for line in props if not line.startswith('media.settings.xml=')]
    return props + ['ro.hardware.camera=v4l2', 'media.settings.xml=/vendor/' + MEDIA_PROFILES.as_posix()]


def legacy_gralloc_available(vendor):
    return all((Path(vendor) / directory / 'gralloc.gbm.so').is_file() for directory in HAL_DIRS)


def gralloc_alias(vendor, enabled):
    """Create (or remove) vendor/{lib64,lib}/hw/gralloc.anvilgbm.so pointing at
    the image's gralloc.gbm.so. The vendor tree is the runtime's own overlay."""
    for directory in HAL_DIRS:
        folder = Path(vendor) / directory
        if folder.is_symlink() or not folder.is_dir():
            continue
        alias = folder / f'gralloc.{GRALLOC_ALIAS}.so'
        if alias.is_symlink() or alias.exists():
            if not alias.is_symlink():
                raise RuntimeError('Unexpected file at ' + alias.name)
            alias.unlink()
        if enabled:
            alias.symlink_to('gralloc.gbm.so')


EXTERNAL_CONFIG = Path('etc/external_camera_config.xml')
MEDIA_PROFILES = Path('etc/media_profiles_V1_0.xml')
PROFILES_BLOCK = re.compile(r'[ \t]*<CamcorderProfiles cameraId="(\d+)">.*?</CamcorderProfiles>[ \t]*\n?', re.S)


def camera_ids(nodes):
    """Android's external HAL numbers a camera CameraIdOffset + N for /dev/videoN."""
    return [int(NODE.fullmatch(node).group(1)) for node in nodes if NODE.fullmatch(node)]


def external_config(text, enabled):
    """Number external cameras from 0 instead of the HAL's default 100.

    Apps and CameraX look up video recording profiles (media_profiles) by
    camera id; the image only defines ids 0-3, so camera 100 reports
    "no supported EncoderProfiles" and video recording is refused."""
    text = re.sub(r'[ \t]*<CameraIdOffset>[^<]*</CameraIdOffset>[ \t]*\n?', '', text)
    if not enabled:
        return text
    if '</Provider>' not in text:
        raise ValueError('Unsupported external camera configuration')
    return text.replace('</Provider>', '    <CameraIdOffset>0</CameraIdOffset>\n    </Provider>', 1)


VIDIOC_ENUM_FMT = 0xc0405602
VIDIOC_ENUM_FRAMESIZES = 0xc02c564a
VIDIOC_ENUM_FRAMEINTERVALS = 0xc034564b
# CamcorderProfile qualities Android and CameraX understand, with typical
# H.264 bitrates.
QUALITIES = (('qvga', 320, 240, 512000), ('480p', 640, 480, 2000000), ('480p', 720, 480, 2000000),
             ('720p', 1280, 720, 5000000), ('1080p', 1920, 1080, 10000000), ('2160p', 3840, 2160, 35000000))


def frame_sizes(node):
    """{(width, height): highest fps} over every discrete V4L2 capture mode."""
    sizes = {}
    try:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return sizes
    try:
        for index in range(32):
            fmt = bytearray(struct.pack('<II', index, 1) + bytes(56))
            try:
                fcntl.ioctl(fd, VIDIOC_ENUM_FMT, fmt, True)
            except OSError:
                break
            fourcc = bytes(fmt[44:48])
            for size_index in range(64):
                size = bytearray(struct.pack('<I4sI', size_index, fourcc, 0) + bytes(32))
                try:
                    fcntl.ioctl(fd, VIDIOC_ENUM_FRAMESIZES, size, True)
                except OSError:
                    break
                if struct.unpack('<I', size[8:12])[0] != 1:          # discrete sizes only
                    break
                width, height = struct.unpack('<II', size[12:20])
                best = 0
                for interval_index in range(16):
                    interval = bytearray(struct.pack('<I4sIII', interval_index, fourcc, width, height, 0) + bytes(32))
                    try:
                        fcntl.ioctl(fd, VIDIOC_ENUM_FRAMEINTERVALS, interval, True)
                    except OSError:
                        break
                    numerator, denominator = struct.unpack('<II', interval[20:28])
                    if numerator:
                        best = max(best, denominator // numerator)
                sizes[(width, height)] = max(sizes.get((width, height), 0), best)
    finally:
        os.close(fd)
    return sizes


def profiles_block(identifier, sizes):
    """A CamcorderProfiles element for the camera's real capture modes."""
    rows = []
    for quality, width, height, bitrate in QUALITIES:
        fps = min(sizes.get((width, height), 0), 30)
        if fps < 1 or any(f'quality="{quality}"' in row for row in rows):
            continue
        rows.append(f"""        <EncoderProfile quality="{quality}" fileFormat="mp4" duration="60">
            <Video codec="h264" bitRate="{bitrate}" width="{width}" height="{height}" frameRate="{fps}" />
            <Audio codec="aac" bitRate="128000" sampleRate="48000" channels="1" />
        </EncoderProfile>
""")
    if not rows:
        return None
    return (f'    <CamcorderProfiles cameraId="{identifier}">\n' + ''.join(rows) +
            '        <ImageEncoding quality="95" />\n        <ImageEncoding quality="80" />\n'
            '        <ImageEncoding quality="70" />\n        <ImageDecoding memCap="20000000" />\n'
            '    </CamcorderProfiles>\n')


def media_profiles(text, cameras):
    """Replace the recording profiles of passed-through camera ids
    ({id: frame sizes}) with ones matching the camera; other ids keep the
    image's profiles. Cameras without usable modes keep what the image has."""
    if not cameras or '</MediaSettings>' not in text:
        return text
    # The image ships quality="720p " (trailing space), which libmedia's
    # parser rejects once the file is actually read (media.settings.xml).
    text = re.sub(r'quality="\s*([^"]*?)\s*"', r'quality="\1"', text)
    blocks = {identifier: profiles_block(identifier, sizes) for identifier, sizes in cameras.items()}
    blocks = {identifier: block for identifier, block in blocks.items() if block}
    if not blocks:
        return text
    kept = PROFILES_BLOCK.sub(lambda match: '' if int(match.group(1)) in blocks else match.group(0), text)
    anchor = kept.find('<CamcorderProfiles')
    if anchor < 0:
        anchor = kept.rindex('</MediaSettings>')
    anchor = kept.rfind('\n', 0, anchor) + 1
    return kept[:anchor] + ''.join(blocks[i] for i in sorted(blocks)) + kept[anchor:]


def vendor_files(vendor, lower_dirs, nodes, sizes=frame_sizes, dev=Path('/dev')):
    """Write camera numbering and recording profiles into the runtime's vendor
    overlay. Files are rebuilt from the image every boot, so turning cameras
    off restores the original content."""
    nodes = [node for node in nodes if NODE.fullmatch(node)]
    ids = camera_ids(nodes)
    cameras = {identifier: sizes(dev / node) for identifier, node in zip(ids, nodes)}
    for relative, change in ((EXTERNAL_CONFIG, lambda text: external_config(text, bool(ids))),
                             (MEDIA_PROFILES, lambda text: media_profiles(text, cameras))):
        source = next((Path(directory) / relative for directory in lower_dirs
                       if (Path(directory) / relative).is_file()), None)
        target = Path(vendor) / relative
        if source is None or target.is_symlink():
            continue
        original = source.read_text()
        try:
            wanted = change(original)
        except ValueError:
            wanted = original
        if not target.is_file() or target.read_text() != wanted:
            target.write_text(wanted)
