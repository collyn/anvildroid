"""User-requested, bounded diagnostic reports; no app-private files or uploads."""
import datetime
import json
import os
import platform
from pathlib import Path
import re
import stat

SCHEMA = 1
MAX_LOG = 64 * 1024
MAX_REPORT = 1024 * 1024
LOG_NAMES = ('worker.log', 'nvidia-renderer.log', 'console.log')
SECRET_KEY = re.compile(r'(?:password|passwd|secret|token|credential|cookie|authorization|api[_-]?key|access[_-]?key|private[_-]?key|android_id|gsf|serial|hostname|owner_uid|data_path|storage_path|source_path|endpoint|address)', re.I)
ASSIGNMENT = re.compile(r'''(?ix)\b([\w-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|authorization|cookie|credential|android[_-]?id|serial)[\w-]*)\b(["']?\s*[:=]\s*|\s+)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')


def redact_text(value):
    value = value[:256 * 1024]
    value = re.sub(r'(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/=._-]+', '<redacted-auth>', value)
    value = re.sub(r'\b(https?://)[^\s/@:]+:[^\s/@]+@', r'\1<redacted>@', value)
    value = re.sub(r'(?i)([?&](?:token|key|secret|password|auth)=)[^&\s]+', r'\1<redacted>', value)
    value = ASSIGNMENT.sub(lambda match: match[1] + match[2] + '<redacted>', value)
    value = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '<redacted-jwt>', value)
    value = re.sub(r'/(?:home|Users)/[^\s/"\']+(?:/[^\s"\']*)?', '<user-path>', value)
    value = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?![\w.])', '<ip-address>', value)
    value = re.sub(r'(?i)(?<![\w:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f:]{0,39}(?:%[\w-]+)?', '<ip-address>', value)
    value = re.sub(r'(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b', '<mac-address>', value)
    value = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', '<email>', value)
    return value


def redact(value, depth=0):
    if depth > 12:
        return '<depth-limit>'
    if isinstance(value, dict):
        return {str(key)[:160]: '<redacted>' if SECRET_KEY.search(str(key)) else redact(item, depth + 1)
                for key, item in list(value.items())[:256]}
    if isinstance(value, (list, tuple)):
        return [redact(item, depth + 1) for item in value[:256]]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or type(value) in (int, float, bool):
        return value
    return '<unsupported-value>'


def host_summary():
    # Deliberately omit nodename, environment, command lines and user identity.
    result = {'kernel': platform.release(), 'architecture': platform.machine(),
              'python': platform.python_version(), 'system': platform.system()}
    try:
        with open('/etc/os-release', encoding='utf-8') as stream:
            for line in stream.read(16384).splitlines():
                key, separator, value = line.partition('=')
                if separator and key in ('ID', 'VERSION_ID', 'PRETTY_NAME'):
                    result[key.lower()] = value.strip('"')
    except OSError:
        result['distribution'] = 'unavailable'
    return result


def log_tail(directory, name):
    if name not in LOG_NAMES and not re.fullmatch(r'x11-[0-9a-f]{32}\.log', name):
        raise ValueError('Diagnostic log name is not allowed')
    folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(folder)
        if parent.st_uid != 0 or parent.st_mode & 0o077:
            raise ValueError('Diagnostic log directory is not private and root-owned')
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or
                    before.st_mode & 0o022 or before.st_nlink != 1):
                raise ValueError('Unsafe diagnostic log file')
            data = os.pread(descriptor, MAX_LOG, max(0, before.st_size - MAX_LOG))
            after = os.fstat(descriptor)
            return {'status': 'collected', 'text': redact_text(data.decode('utf-8', 'replace')),
                    'truncated': before.st_size > MAX_LOG,
                    'changed_during_read': (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)}
        finally:
            os.close(descriptor)
    finally:
        os.close(folder)


def process_start(pid):
    if not isinstance(pid, str) or not pid.isdecimal() or int(pid) <= 0:
        raise ValueError('Default runtime process identity is unavailable')
    # comm can contain spaces and parentheses; starttime is field 22.
    try:
        value = Path('/proc', pid, 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError) as error:
        raise ValueError('Default runtime process start time is unavailable') from error
    if not value.isdecimal():
        raise ValueError('Default runtime process start time is invalid')
    return value


def collect(runtime_id, collectors, *, include_logs=False, log_directory=None, generation=None,
            verify_session=None, logs_unavailable_reason=None):
    if type(include_logs) is not bool:
        raise ValueError('include_logs must be boolean')
    if not isinstance(runtime_id, str) or (runtime_id != 'default' and not re.fullmatch(r'r-[0-9a-f]{32}', runtime_id)):
        raise ValueError('Invalid diagnostic runtime ID')
    report = {'schema_version': SCHEMA, 'runtime_id': runtime_id,
              'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'host': host_summary(), 'sections': {}, 'logs': {},
              'privacy': {'logs_requested': include_logs, 'redacted': True,
                          'redaction': 'best_effort', 'app_private_files_collected': False}}
    if any(name not in ('runtime', 'health', 'resources', 'display', 'components', 'image') for name in collectors):
        raise ValueError('Unknown diagnostic section')
    session_error = None
    if verify_session is not None:
        report['session'] = {'status': 'verified'}

    def verify():
        nonlocal session_error
        if session_error is not None:
            return False
        if verify_session is not None:
            try:
                verify_session()
            except Exception as error:
                session_error = 'Runtime session could not be verified: ' + redact_text(str(error))[:1600] + '. Retry diagnostic collection.'
                report['session'] = {'status': 'unavailable', 'error': session_error}
                return False
        return True

    verify()
    for name, callback in collectors.items():
        if not verify():
            continue
        try:
            value = redact(callback())
            if len(json.dumps(value).encode()) > 128 * 1024:
                raise ValueError('Diagnostic section exceeds its size limit')
            report['sections'][name] = {'status': 'collected', 'data': value}
        except Exception as error:
            report['sections'][name] = {'status': 'unavailable', 'error': redact_text(str(error))[:2048]}
        verify()
    names = []
    if include_logs:
        names = list(LOG_NAMES)
        if isinstance(generation, str) and re.fullmatch(r'[0-9a-f]{32}', generation):
            names.append('x11-' + generation + '.log')
        for name in names:
            if not verify():
                continue
            try:
                if log_directory is None:
                    raise ValueError(logs_unavailable_reason or 'Controller-owned logs are unavailable for this runtime')
                report['logs'][name] = log_tail(log_directory, name)
            except (OSError, ValueError) as error:
                report['logs'][name] = {'status': 'unavailable', 'error': redact_text(str(error))[:1024]}
            verify()
    verify()
    if session_error is not None:
        # Never present a mixture of generations, even when earlier reads succeeded.
        report['sections'] = {name: {'status': 'unavailable', 'error': session_error} for name in collectors}
        report['logs'] = {name: {'status': 'unavailable', 'error': session_error} for name in names}
    if len(json.dumps(report).encode()) > MAX_REPORT:
        raise ValueError('Diagnostic report exceeds its size limit')
    return report
