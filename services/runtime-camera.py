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


def boot_properties(props, nodes):
    """Select Android's V4L2 camera HAL only when a camera is passed through."""
    props = [line for line in props if not line.startswith('ro.hardware.camera=')]
    return props + (['ro.hardware.camera=v4l2'] if nodes else [])
