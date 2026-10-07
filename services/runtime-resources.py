"""Per-runtime systemd scopes, cgroup v2 limits and readback."""
import json
import os
from pathlib import Path
import re
import shutil

CGROUP = Path('/sys/fs/cgroup')
DEFAULT = {'memory_mib': 0, 'cpu_count': 0}


def host():
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in ('MemTotal', 'MemAvailable'): mem[key] = int(value.split()[0]) // 1024
    controllers = (CGROUP / 'cgroup.controllers').read_text().split() if (CGROUP / 'cgroup.controllers').exists() else []
    supported = {'cpu', 'memory'} <= set(controllers) and Path('/run/systemd/system').is_dir() and bool(shutil.which('systemd-run'))
    return {'memory_total_mib': mem['MemTotal'], 'memory_available_mib': mem['MemAvailable'],
            'cpu_count': len(os.sched_getaffinity(0)), 'supported': supported,
            'reason': None if supported else 'Requires systemd and cgroup v2 CPU/memory controllers.'}


def validate(value, capacity=None):
    capacity = capacity or host()
    if not isinstance(value, dict) or set(value) != set(DEFAULT) or any(type(v) is not int for v in value.values()):
        raise RuntimeError('Expected integer memory_mib and cpu_count')
    memory, cpu = value['memory_mib'], value['cpu_count']
    if memory != 0 and not 1024 <= memory <= max(0, capacity['memory_total_mib'] - 1024):
        raise RuntimeError('RAM limit must be at least 1024 MiB and leave 1024 MiB for the host; use 0 for no explicit limit')
    if not 0 <= cpu <= capacity['cpu_count']:
        raise RuntimeError('CPU quota exceeds available logical CPUs; use 0 for no explicit quota')
    return dict(value)


def load(instance):
    path = instance / 'resources.json'
    return validate(json.loads(path.read_text()) if path.exists() else dict(DEFAULT),
                    {'memory_total_mib': 2**40, 'cpu_count': 2**20})


def unit(identifier, generation):
    if not re.fullmatch(r'r-[a-f0-9]{32}', identifier) or not re.fullmatch(r'[a-f0-9]{32}', generation):
        raise RuntimeError('Invalid resource scope identity')
    return f'anvildroid-runtime-{identifier}-{generation}.scope'


def scope_path(identifier, generation):
    return CGROUP / 'anvildroid.slice/anvildroid-runtimes.slice' / unit(identifier, generation)


def prefix(identifier, generation, limits):
    if not host()['supported']: raise RuntimeError('Resource scopes require systemd and cgroup v2 CPU/memory controllers')
    limits = validate(limits)
    args = ['systemd-run', '--scope', '--quiet', '--collect', '--slice=anvildroid-runtimes.slice',
            '--unit=' + unit(identifier, generation), '-p', 'MemoryAccounting=yes', '-p', 'CPUAccounting=yes',
            # LXC must not delegate the host unified hierarchy. The worker
            # hides cgroup mounts from Android; systemd scope still enforces
            # the runtime RAM/CPU limits without nested controller delegation.
            '-p', 'OOMPolicy=kill', '-p', 'MemoryMax=' + (str(limits['memory_mib'] * 1024**2) if limits['memory_mib'] else 'infinity'),
            '-p', 'MemorySwapMax=' + ('0' if limits['memory_mib'] else 'infinity')]
    if limits['cpu_count']: args += ['-p', f"CPUQuota={limits['cpu_count'] * 100}%", '-p', 'CPUQuotaPeriodSec=100ms']
    return args + ['--']


def readback(identifier, generation):
    path = scope_path(identifier, generation)
    if not path.exists(): return None
    def read(name): return (path / name).read_text().strip()
    # With unlimited CPU, systemd may use accounting without enabling the CPU
    # controller. Absence means no quota here; bounded settings still fail verify.
    try: cpu_max = read('cpu.max')
    except FileNotFoundError: cpu_max = 'max 100000'
    quota, period = cpu_max.split()
    def number(value): return None if value == 'max' else int(value)
    events = dict(line.split() for line in read('memory.events').splitlines())
    stats = dict(line.split() for line in read('cpu.stat').splitlines())
    return {'memory_max_bytes': number(read('memory.max')), 'memory_current_bytes': int(read('memory.current')),
            'swap_max_bytes': number(read('memory.swap.max')), 'swap_current_bytes': int(read('memory.swap.current')),
            'cpu_quota_count': None if quota == 'max' else int(quota) / int(period),
            'cpu_usage_usec': int(stats.get('usage_usec', 0)), 'cpu_throttled_usec': int(stats.get('throttled_usec', 0)),
            'oom_kills': int(events.get('oom_kill', 0)), 'scope': unit(identifier, generation)}


def verify(identifier, generation, limits):
    actual = readback(identifier, generation)
    expected_memory = limits['memory_mib'] * 1024**2 if limits['memory_mib'] else None
    expected_cpu = limits['cpu_count'] or None
    if not actual or actual['memory_max_bytes'] != expected_memory or actual['cpu_quota_count'] != expected_cpu or (limits['memory_mib'] and actual['swap_max_bytes'] != 0):
        raise RuntimeError('Runtime resource limits could not be verified in cgroup v2')
    return actual


def info(instance, identifier, running=False):
    actual = None
    error = None
    if running:
        try:
            identity = json.loads((instance / 'worker-identity.json').read_text())
            if identity.get('runtime_id') != identifier: raise RuntimeError('Worker resource identity mismatch')
            actual = readback(identifier, identity['generation'])
            if actual is None: error = 'Stop/start this runtime to enable resource readback.'
        except (OSError, ValueError, KeyError, RuntimeError) as exc: error = str(exc)
    return {'configured': load(instance), 'host': host(), 'effective': actual, 'error': error}


def verify_container(pid, identifier, generation):
    if type(pid) is not int or pid <= 0: raise RuntimeError('Invalid Android init PID')
    expected = '/' + str(scope_path(identifier, generation).relative_to(CGROUP))
    groups = (Path('/proc') / str(pid) / 'cgroup').read_text().splitlines()
    group = next((line[3:] for line in groups if line.startswith('0::')), '')
    if group != expected and not group.startswith(expected + '/'):
        raise RuntimeError('Android container escaped its runtime resource scope')


def presets(capacity):
    usable = max(0, capacity['memory_total_mib'] - 1024)
    result = []
    for name, ram, cpus, fraction in [('Light', 2048, 2, 4), ('Balanced', 4096, 4, 4), ('Performance', 8192, 6, 2)]:
        memory = min(ram, (usable // fraction // 256) * 256)
        if memory >= 1024:
            result.append({'name': name, 'memory_mib': memory, 'cpu_count': min(cpus, capacity['cpu_count'])})
    return result + [{'name': 'Unlimited', **DEFAULT}]


def budget(capacity, others, current_bytes=0):
    """Same-owner active runtimes only; limits are potential demand, not reservations."""
    summary = {'active_count': len(others), 'memory_limit_mib': 0, 'cpu_quota_count': 0,
               'unbounded_memory_count': 0, 'unbounded_cpu_count': 0, 'unknown_count': 0,
               'unused_memory_mib': 0, 'target_current_mib': (current_bytes + 1024**2 - 1) // 1024**2,
               'host_reserve_mib': 1024}
    for entry in others:
        if entry is None:
            summary['unknown_count'] += 1
            continue
        actual = entry.get('effective')
        # Prefer live limits: saved configuration does not prove what an old worker uses.
        memory = (actual['memory_max_bytes'] // 1024**2 if actual['memory_max_bytes'] is not None else 0) if actual else entry['configured']['memory_mib']
        cpu = (actual['cpu_quota_count'] or 0) if actual else entry['configured']['cpu_count']
        used = actual['memory_current_bytes'] // 1024**2 if actual else 0
        summary['memory_limit_mib'] += memory
        summary['cpu_quota_count'] += cpu
        summary['unbounded_memory_count'] += int(memory == 0)
        summary['unbounded_cpu_count'] += int(cpu == 0)
        summary['unused_memory_mib'] += max(0, memory - used)
        if not actual: summary['unknown_count'] += 1
    summary['available_for_growth_mib'] = max(0, capacity['memory_available_mib'] - 1024 - summary['unused_memory_mib'])
    return summary


def admission(limits, summary, capacity):
    additional = max(0, limits['memory_mib'] - summary['target_current_mib'])
    blocked = bool(limits['memory_mib'] and additional > summary['available_for_growth_mib'])
    return {'memory_blocked': blocked,
            'cpu_oversubscribed': bool(limits['cpu_count'] and limits['cpu_count'] + summary['cpu_quota_count'] > capacity['cpu_count']),
            'detail': ('Not enough RAM headroom for this limit and other active runtimes. Lower the RAM limit or stop another runtime, then retry.' if blocked else None)}
