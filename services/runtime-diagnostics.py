"""User-requested, bounded diagnostic reports; no app-private files or uploads."""
import datetime
import json
import os
import platform
from pathlib import Path
import re
import stat
import unicodedata

SCHEMA = 1
MAX_LOG = 64 * 1024
MAX_REPORT = 1024 * 1024
LOG_NAMES = ('worker.log', 'nvidia-renderer.log', 'console.log')
SENSITIVE_FIELD = r'(?:password|passwd|secret|token|credential|cookie|authorization|api[_-]?key|access[_-]?key|private[_-]?key|android[_-]?id|gsf|serial|host[_-]?name|ssid|owner_uid|data_path|storage_path|source_path|endpoint|address)'
SECRET_KEY = re.compile(SENSITIVE_FIELD, re.I)
# A quoted value may itself contain escaped quotes. Stopping at the first quote
# exports the rest of that same credential.
QUOTED_VALUE = r'''(?:"(?:\\[^\r\n]|[^"\\\r\n])*(?:"|\\?(?=[\r\n]|\Z))|'(?:\\[^\r\n]|[^'\\\r\n])*(?:'|\\?(?=[\r\n]|\Z)))'''
ASSIGNMENT = re.compile(r'\b([\w-]*' + SENSITIVE_FIELD + r'''[\w-]*)\b(["']?\s*[:=]\s*|\s+)(?:'''
                        + QUOTED_VALUE + r'|[^\s,;}]+)', re.I)
AUTH_HEADER = re.compile(r'''\b((?:proxy[_-]?)?authorization|(?:set[_-]?)?cookie)(["']?\s*[:=]\s*)(?:'''
                         + QUOTED_VALUE + r'|[^\r\n]+)', re.I)
LOG_ESCAPE = re.compile(r'''\\(?:[\\/"']|[nrt]|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2})''')


def log_unescape(match):
    escape = match[0][1:]
    if escape[0] in 'uxU':
        number = int(escape[1:], 16)
        return chr(number) if number <= 0x10ffff and not 0xd800 <= number <= 0xdfff else match[0]
    return {'n': '\n', 'r': '\r', 't': '\t'}.get(escape, escape)


def visible_log_text(value):
    # Keep line boundaries and ordinary Unicode, but remove presentation/control
    # characters that can split a sensitive field name without changing its text.
    value = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', value)
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    return ''.join(char for char in value if char in '\r\n\t' or
                   (ord(char) >= 32 and ord(char) != 127 and unicodedata.category(char) != 'Cf'))


def redact_plain_text(value):
    value = visible_log_text(value)
    value = re.sub(r'-----BEGIN ([A-Z ]*PRIVATE KEY)-----.*?(?:-----END \1-----|\Z)',
                   '<redacted-private-key>', value, flags=re.S)
    # A bounded tail can begin inside a PEM block, with only its END marker left.
    value = re.sub(r'\A.*?-----END [A-Z ]*PRIVATE KEY-----', '<redacted-private-key>', value, flags=re.S)
    value = AUTH_HEADER.sub(lambda match: match[1] + match[2] + '<redacted>', value)
    value = re.sub(r'(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/=._-]+', '<redacted-auth>', value)
    value = re.sub(r'(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+@', r'\1<redacted>@', value)
    value = re.sub(r'(?i)([?&](?:token|key|secret|password|auth)=)[^&\s]+', r'\1<redacted>', value)
    value = ASSIGNMENT.sub(lambda match: match[1] + match[2] + '<redacted>', value)
    value = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '<redacted-jwt>', value)
    value = re.sub(r'/(?:home|Users|media|mnt|run/user)/[^\s/"\']+(?:/[^\s"\']*)?', '<user-path>', value)
    value = re.sub(r'''(?i)\b[A-Z]:\\(?:Users|Documents and Settings)\\[^\r\n"']*''', '<user-path>', value)
    value = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?![\w.])', '<ip-address>', value)
    value = re.sub(r'(?i)(?<![\w:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f:]{0,39}(?:%[\w-]+)?', '<ip-address>', value)
    value = re.sub(r'(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b', '<mac-address>', value)
    value = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', '<email>', value)
    return value


def redact_text(value):
    value = value[:256 * 1024]
    # Logs commonly contain JSON/Python string escapes. Redact before decoding
    # so an escaped quote in a credential cannot turn into a false value end.
    # Three rounds cover nested log serialization without parsing arbitrary data.
    for _ in range(3):
        value = redact_plain_text(value)
        decoded = LOG_ESCAPE.sub(log_unescape, value)
        if decoded == value:
            return value
        value = decoded
    return redact_plain_text(value)


def sensitive_key(value):
    # Decide whether to hide the value before masking an identifying key path.
    # Otherwise /home/password/file becomes <user-path> and loses its meaning.
    value = value[:256 * 1024]
    for _ in range(3):
        value = visible_log_text(value)
        if SECRET_KEY.search(value):
            return True
        decoded = LOG_ESCAPE.sub(log_unescape, value)
        if decoded == value:
            return False
        value = decoded
    return bool(SECRET_KEY.search(visible_log_text(value)))


def redact(value, depth=0):
    if depth > 12:
        return '<depth-limit>'
    if isinstance(value, dict):
        return {redact_text(str(key))[:160]: '<redacted>' if sensitive_key(str(key)) else redact(item, depth + 1)
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
            offset = max(0, before.st_size - MAX_LOG)
            data = os.pread(descriptor, MAX_LOG, offset)
            if offset and os.pread(descriptor, 1, offset - 1) != b'\n':
                # A partial line can contain the tail of a secret without its
                # identifying key. Keep only complete lines from bounded tails.
                data = data.partition(b'\n')[2]
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
