"""Shared, generation-scoped bridge keymap protocol for all runtimes."""
import importlib.util
import os
import stat
from pathlib import Path
import re
import time
import uuid
spec = importlib.util.spec_from_file_location('keymap_linux', Path(__file__).with_name('runtime-linux.py'))
linux = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linux)
require = linux.require

def keymap_profiles(lxc_path, container, identifier):
    script = ('n=0; for f in /data/waydroid_tmp/anvildroid/keymaps/*; do '
              '[ -f "$f" ] && [ ! -L "$f" ] || continue; '
              'n=$((n+1)); [ "$n" -le 128 ] || exit 2; '
              'size=$(wc -c < "$f") || exit 2; '
              'printf "SIZE %s\\n" "$size"; head -c 16385 "$f"; printf "\\n"; done')
    output = linux.run_bounded('lxc-attach', '-P', lxc_path, '-n', container, '--uid','1000','--gid','1000','--',
                              '/system/bin/sh', '-c', script, timeout=4, operation='Keymap inventory', output_limit=3*1024*1024, full_output=True, preserve_whitespace=True)
    return parse_keymap_frames(output, identifier)


def keymap_profiles_at(data_fd, identifier):
    """Read a frozen guest through its verified data directory, without waking it."""
    directory = os.dup(data_fd)
    blocks = []
    invalid = 0
    try:
        for part in ('waydroid_tmp', 'anvildroid', 'keymaps'):
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            except FileNotFoundError:
                return {'runtime_id': identifier, 'profiles': [], 'invalid_count': 0}
            os.close(directory)
            directory = child
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                require(index < 512, 'Keymap directory exceeds limit')
                if entry.name.startswith('.'): continue  # Atomic save temporaries.
                require(len(blocks) + invalid < 128, 'Keymap inventory exceeds limit')
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384:
                    invalid += 1
                    continue
                fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
                try:
                    current = os.fstat(fd)
                    require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1 and
                            (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), 'Keymap file changed')
                    raw = os.read(fd, 16385)
                finally:
                    os.close(fd)
                try:
                    require(len(raw) <= 16384, 'Keymap profile exceeds limit')
                    text = raw.decode('utf-8')
                    require(text.endswith('\n'), 'Incomplete keymap profile')
                    blocks.append('SIZE '+str(len(raw))+'\n'+text+'\n')
                except (UnicodeError, RuntimeError):
                    invalid += 1
        result = parse_keymap_frames(''.join(blocks), identifier)
        result['invalid_count'] += invalid
        return result
    finally:
        os.close(directory)


def parse_keymap_frames(output, identifier):
    """Guest bytes cannot create inventory boundaries or hide oversized tails."""
    raw=output.encode('utf-8'); profiles=[]; invalid=0; offset=0; count=0
    require(len(raw)<=3*1024*1024, 'Keymap inventory exceeds limit')
    packages=set()
    while offset<len(raw):
        end=raw.find(b'\n',offset)
        require(end>=0 and re.fullmatch(rb'SIZE [ \t]*[0-9]+[ \t]*',raw[offset:end]), 'Invalid keymap inventory framing')
        size=int(raw[offset:end].split()[1]); offset=end+1; count+=1
        require(count<=128, 'Keymap inventory exceeds limit')
        length=min(size,16385)
        require(offset+length<len(raw) and raw[offset+length:offset+length+1]==b'\n', 'Incomplete keymap inventory frame')
        payload=raw[offset:offset+length]; offset+=length+1
        if size>16384 or not payload.endswith(b'\n') or b'BEGIN\n' in payload or b'END\n' in payload:
            invalid+=1; continue
        result=parse_keymaps('BEGIN\n'+payload.decode('utf-8')+'END\n',identifier)
        invalid+=result['invalid_count']
        for profile in result['profiles']:
            if profile['package'] in packages: invalid+=1; continue
            packages.add(profile['package']); profiles.append(profile)
    return {'runtime_id':identifier,'profiles':profiles,'invalid_count':invalid}


def parse_keymaps(output, identifier):
    require(len(output.encode()) <= 3*1024*1024, 'Keymap inventory exceeds limit')
    blocks=output.split('BEGIN\n')[1:]
    require(len(blocks)<=128, 'Keymap inventory exceeds limit')
    profiles=[]; errors=0; packages=set()
    for block in blocks:
        try:
            require(block.endswith('END\n') and not re.search(r'[^\t\r\n\x20-\x7e]',block), 'Invalid keymap text')
            lines=block.split('\n')[:-1]; header=lines[0].split()
            require(re.fullmatch(r'ANVIL_KEYMAP[ \t]+[^\r\n]+\r?', lines[0]), 'Invalid keymap header')
            require(len(header)==5 and header[0]=='ANVIL_KEYMAP' and header[1] in ('1','2','3','4') and header[2]==identifier, 'Invalid keymap scope')
            require(re.fullmatch(r'[0-9]{1,15}',header[4]), 'Invalid keymap count')
            package=header[3]; count=int(header[4])
            require(re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+',package) and len(package)<=240 and 0<=count<=64, 'Invalid keymap')
            require(len(block.rsplit('END',1)[0].encode()) <= 16384, 'Keymap profile exceeds limit')
            require(len(lines)==count+2 and lines[-1]=='END', 'Incomplete keymap')
            require(package not in packages, 'Duplicate keymap package')
            bindings=[];keys=set();reserved=set()
            for index,line in enumerate(lines[1:-1]):
                require(re.fullmatch(r'[ \t]*[0-9]+(?:[ \t]+[0-9]+)*[ \t]*\r?',line), 'Invalid decimal binding')
                tokens=line.split()
                require(all(len(token)<=15 for token in tokens), 'Invalid decimal width')
                values=list(map(int,tokens))
                require(all(value<=65535 for value in values), 'Invalid decimal bound')
                require(len(values)=={'1':4,'2':9,'3':19,'4':31}[header[1]], 'Invalid binding fields')
                key,x,y,hold=values[:4]
                kind,ex,ey,duration,repeats=values[4:9] if header[1]!='1' else (0,0,0,0,0)
                require(0<=kind<=({'1':0,'2':6,'3':8,'4':9}[header[1]]) and 0<=ex<=65535 and 0<=ey<=65535 and
                        0<=duration<=5000 and 0<=repeats<=20 and
                        (kind==0 or 50<=duration<=5000 and 1<=repeats<=20) and
                        (kind!=5 or duration*repeats*2<=10000) and
                        (kind!=1 or header[1]=='4' or key==17), 'Invalid advanced control')
                source,end,radius,ramp,lw,lh,sx,sy,invert,activation=values[9:19] if header[1] in ('3','4') else (0,)*10
                require(source in (0,1,2) and end in (0,1,2) and (radius==0 or 256<=radius<=32767) and
                        0<=ramp<=1000 and 0<=lw<=32767 and 0<=lh<=32767 and 0<=sx<=1000 and 0<=sy<=1000 and
                        invert in (0,1) and activation in (0,1), 'Invalid V3 parameters')
                require((source==2)==(kind==7) and (source!=2 or key==0) and
                        (source!=1 or 272<=key<=276) and (source!=0 or 2<=key<=767) and
                        (kind!=1 or source==0), 'Invalid input source')
                require(kind!=8 or (lw>=256 and lh>=256 and lw<=x<=65535-lw and lh<=y<=65535-lh and sx>=10 and sy>=10), 'Invalid look region')
                up,left,down,right,walk,walk_radius,suspend,drag,skill,cancel,cx,cy=values[19:] if header[1]=='4' else (0,)*12
                directions=(up,left,down,right)
                require(not any(directions) or kind==1 and all(directions), 'Invalid movement keys')
                require(kind!=1 or key==(up or 17), 'Invalid movement trigger')
                require((not walk or kind==1) and (not walk_radius or walk and 10<=walk_radius<=100) and
                        (not suspend or kind==8 and activation==1), 'Invalid secondary key')
                require(drag in (0,1) and (not drag or kind==0 and source==1 and (end==2 or end==0 and hold==1)), 'Invalid drag shot')
                require(0<=skill<=2 and (not any((skill,cancel,cx,cy)) or kind==4) and
                        (not (cx or cy) or cancel), 'Invalid skill parameters')
                require(kind!=9 or source==1 and key==273 and radius>0, 'Invalid pointer movement')
                claims=list(directions if up else (17,30,31,32)) if kind==1 else ([key] if source==0 else [])
                claims.extend(k for k in (walk,suspend,cancel) if k)
                require(all(2<=k<=767 and k not in (60,61,62) for k in claims) and
                        len(set(claims))==len(claims) and not reserved.intersection(claims), 'Conflicting keyboard controls')
                reserved.update(claims)
                require((source,key) not in keys and 0<=x<=65535 and 0<=y<=65535 and hold in (0,1), 'Invalid binding')
                keys.add((source,key));bindings.append({'id':f'point-{index}','evdev':key,'nx':x,'ny':y,
                    'kind':('hold' if end in (0,2) else 'tap') if not kind else ('wasd','swipe','repeat','skill','macro','aim','mouse_touch','look','walk')[kind-1],
                    **({'end_nx':ex,'end_ny':ey,'duration_ms':duration,'repeat_count':repeats} if kind else {}),
                    **({'input_source':('keyboard','mouse','pointer')[source], 'end_mode':('legacy','release','hold')[end],
                        'radius':radius,'ramp_ms':ramp,'look_width':lw,'look_height':lh,'sensitivity_x':sx,'sensitivity_y':sy,
                        'invert_y':invert,'activation':('hold','capture')[activation]} if header[1] in ('3','4') else {}),
                    **(dict(zip(('up_key','left_key','down_key','right_key','walk_key','walk_radius','suspend_key','drag_shot','skill_mode','cancel_key','cancel_x','cancel_y'), values[19:])) if header[1]=='4' else {})})
            require(sum(b['kind']=='look' for b in bindings)<=1 and sum(b['kind']=='mouse_touch' for b in bindings)<=1, 'Duplicate pointer control')
            require(sum(b.get('drag_shot',0) for b in bindings)<=1, 'Duplicate drag shot control')
            require(not any(b['kind']=='aim' for b in bindings) or (1,272) not in keys, 'Conflicting Aim fire')
            packages.add(package)
            profiles.append({'runtime':identifier,'package':package,'bindings':bindings,'source':'runtime','active':False})
        except (ValueError,RuntimeError,IndexError): errors+=1
    return {'runtime_id':identifier,'profiles':profiles,'invalid_count':errors}


def open_keymap(lxc_path, container, generation, package, android, run=linux.run):
    require(isinstance(generation,str) and re.fullmatch(r'[0-9a-f]{32}',generation), 'Missing runtime generation')
    require(isinstance(package,str) and len(package)<=240 and re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+',package), 'Invalid keymap package')
    tasks=[]
    for line in android('cmd','activity','stack','list').splitlines():
        match=re.search(r'taskId=(\d+): ([^ /]+)/\S+ .*visible=true\b',line)
        if match and match[2]==package: tasks.append(int(match[1]))
    require(len(tasks)==1, 'Open exactly one window of this app before editing keymap')
    require(len(package)<=240,'Package name exceeds editor limit')
    nonce=uuid.uuid4().hex
    command=f'1 {nonce} {generation} {tasks[0]} {package}'
    # Every interpolated value is validated above; paths and shell program are fixed.
    temporary=f'keymap-request-{nonce}.tmp'
    script=('set -C; umask 077; cd /data/waydroid_tmp/anvildroid || exit 1; '
            f'printf "%s\\n" "{command}" > {temporary} && mv {temporary} keymap-request')
    # Never perform a root write/chown through guest-controlled pathnames.
    run('lxc-attach','-P',lxc_path,'-n',container,'--uid','1000','--gid','1000',
        '--','/system/bin/sh','-c',script,timeout=3,umask=0o077)
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        reply=android('sh','-c','head -c 160 /data/waydroid_tmp/anvildroid/keymap-reply 2>/dev/null || true').split()
        if len(reply)==2 and reply[0]==nonce:
            require(reply[1] in ('editing','saved','cancelled'),'Overlay refused: finish another edit, release resize, or check profile data')
            message=('Overlay attached. Choose a tool, click the game to place it, then press a key or mouse button to bind. Click a label to rebind and use the inspector to adjust it. Try tests the draft; Save keeps it and Cancel discards it. Esc releases captured mouse input.'
                     if reply[1]=='editing' else 'Overlay '+reply[1]+'.')
            return {'action':'keymap_edit','package':package,'success':True,'message':message}
        time.sleep(.05)
    raise RuntimeError('Overlay did not acknowledge: update/restart this runtime and keep its app window open')

# Scheme metadata is guest-owned. All writes run as Android's graphics user,
# never as host root, and no request can choose a filesystem path or program.
import base64
import hashlib
import json
import tempfile

SCHEME_FIELDS = {'op', 'id', 'package', 'action', 'scheme_id', 'name', 'revision', 'content'}
SCHEME_ACTIONS = {'list', 'create', 'clone', 'select', 'rename', 'remove', 'reset', 'import', 'export'}
SCHEME_LIMIT = 16
SCHEME_META_LIMIT = 400000


def validate_scheme_request(request):
    require(isinstance(request, dict) and set(request) == SCHEME_FIELDS, 'Unexpected keymap scheme fields')
    require(request['op'] == 'keymap_scheme' and isinstance(request['id'], str) and
            re.fullmatch(r'(?:default|r-[0-9a-f]{32})', request['id']), 'Invalid scheme runtime')
    require(isinstance(request['action'], str) and request['action'] in SCHEME_ACTIONS, 'Unsupported scheme action')
    require(isinstance(request['package'], str) and len(request['package']) <= 240 and
            re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', request['package']), 'Invalid scheme package')
    require(isinstance(request['scheme_id'], str) and
            (request['scheme_id'] in ('', 'default') or re.fullmatch(r's-[0-9a-f]{32}', request['scheme_id'])), 'Invalid scheme ID')
    require(isinstance(request['name'], str) and len(request['name']) <= 48 and
            all(c.isprintable() for c in request['name']), 'Invalid scheme name')
    require(isinstance(request['revision'], str) and (request['revision'] == '' or
            re.fullmatch(r'[0-9a-f]{64}', request['revision'])), 'Invalid scheme revision')
    require(isinstance(request['content'], str) and len(request['content'].encode()) <= 24576, 'Scheme import exceeds limit')
    action = request['action']
    require((action == 'import') == bool(request['content']), 'Only import accepts content')
    require(action in ('create', 'clone', 'rename', 'import') or request['name'] == '', 'Unexpected scheme name')
    require(action in ('list', 'create', 'import') or request['scheme_id'], 'Select a scheme')
    require(action not in ('list', 'create', 'import') or request['scheme_id'] == '', 'Unexpected scheme ID')
    require(action == 'list' or request['revision'], 'Refresh schemes before this operation')
    return request


def _scheme_profile(text, identifier, package, rebase=False):
    require(isinstance(text, str) and 0 < len(text.encode()) <= 16384 and text.endswith('\n'), 'Invalid scheme profile size')
    require('BEGIN\n' not in text and 'END\n' not in text, 'Invalid scheme framing')
    header, separator, rows = text.partition('\n')
    parts = header.split()
    require(separator and len(parts) == 5, 'Invalid scheme profile header')
    if rebase:
        require(re.fullmatch(r'(?:default|r-[0-9a-f]{32})', parts[2]) and
                re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', parts[3]), 'Invalid imported scope')
        parts[2:4] = [identifier, package]
        text = ' '.join(parts) + '\n' + rows
    report = parse_keymaps('BEGIN\n' + text + 'END\n', identifier)
    require(report['invalid_count'] == 0 and len(report['profiles']) == 1 and
            report['profiles'][0]['package'] == package, 'Unsupported or invalid scheme profile')
    return text


def _scheme_reference(value):
    require(value is None or (isinstance(value, dict) and set(value) == {'width', 'height', 'orientation'} and
            all(type(value[k]) is int and 1 <= value[k] <= 8192 for k in ('width', 'height')) and
            value['orientation'] in ('portrait', 'landscape')), 'Invalid scheme reference dimensions')
    return value


def _scheme_revision(active, metadata):
    return hashlib.sha256(active.encode() + b'\0' + metadata.encode()).hexdigest()


def _scheme_state(active, metadata, identifier, package):
    empty = f'ANVIL_KEYMAP 1 {identifier} {package} 0\n'
    active = _scheme_profile(active or empty, identifier, package)
    if not metadata:
        state = {'version': 1, 'runtime': identifier, 'package': package, 'selected': 'default',
                 'schemes': [{'id': 'default', 'name': 'Default', 'profile': active, 'reference': None}]}
    else:
        state = json.loads(metadata)
        require(isinstance(state, dict) and set(state) == {'version', 'runtime', 'package', 'selected', 'schemes'} and
                type(state['version']) is int and state['version'] == 1 and state['runtime'] == identifier and state['package'] == package and
                isinstance(state['schemes'], list) and 1 <= len(state['schemes']) <= SCHEME_LIMIT, 'Invalid scheme metadata')
        ids = set()
        for scheme in state['schemes']:
            require(isinstance(scheme, dict) and set(scheme) == {'id', 'name', 'profile', 'reference'} and
                    isinstance(scheme['id'], str) and (scheme['id'] == 'default' or re.fullmatch(r's-[0-9a-f]{32}', scheme['id'])) and
                    scheme['id'] not in ids, 'Invalid scheme metadata ID')
            require(isinstance(scheme['name'], str) and 0 < len(scheme['name']) <= 48 and all(c.isprintable() for c in scheme['name']), 'Invalid scheme name')
            _scheme_profile(scheme['profile'], identifier, package)
            _scheme_reference(scheme['reference'])
            ids.add(scheme['id'])
        require('default' in ids and isinstance(state['selected'], str) and state['selected'] in ids, 'Invalid active scheme')
        # Native editor saves remain authoritative for the selected scheme.
        next(s for s in state['schemes'] if s['id'] == state['selected'])['profile'] = active
    return state


def _scheme_result(state, revision):
    active = next(s for s in state['schemes'] if s['id'] == state['selected'])
    bindings = parse_keymaps('BEGIN\n' + active['profile'] + 'END\n', state['runtime'])['profiles'][0]['bindings']
    return {'runtime_id': state['runtime'], 'package': state['package'], 'revision': revision,
            'bindings': bindings,
            'selected': state['selected'], 'schemes': [{'id': s['id'], 'name': s['name'],
             'binding_count': len(s['profile'].splitlines()) - 1, 'reference': s['reference']} for s in state['schemes']]}


def _scheme_plan(request, active, metadata):
    """Validate the complete transaction before any guest file is touched."""
    validate_scheme_request(request)
    identifier, package, action = request['id'], request['package'], request['action']
    revision = _scheme_revision(active, metadata)
    state = _scheme_state(active, metadata, identifier, package)
    if action == 'list': return _scheme_result(state, revision), None, None
    require(request['revision'] == revision, 'Schemes changed; refresh before retrying')
    selected = next((s for s in state['schemes'] if s['id'] == request['scheme_id']), None)
    current = next(s for s in state['schemes'] if s['id'] == state['selected'])
    if action not in ('create', 'import'):
        require(selected is not None, 'Scheme no longer exists; refresh')
    if action == 'export':
        content = json.dumps({'format': 'anvildroid-scheme', 'version': 1, 'name': selected['name'],
                              'profile': selected['profile'], 'reference': selected['reference']}, ensure_ascii=True, separators=(',', ':'))
        return dict(_scheme_result(state, revision), content=content), None, None
    if action in ('create', 'clone', 'import'):
        require(len(state['schemes']) < SCHEME_LIMIT, 'At most 16 schemes are supported per app')
        name = request['name'].strip()
        profile, reference = f'ANVIL_KEYMAP 1 {identifier} {package} 0\n', None
        if action == 'clone': profile, reference = selected['profile'], selected['reference']
        if action == 'import':
            imported = json.loads(request['content'])
            require(isinstance(imported, dict) and set(imported) == {'format', 'version', 'name', 'profile', 'reference'} and
                    imported['format'] == 'anvildroid-scheme' and type(imported['version']) is int and imported['version'] == 1,
                    'Only AnvilDroid scheme JSON version 1 is supported')
            require(isinstance(imported['name'], str), 'Invalid imported scheme name')
            name = name or imported['name'].strip()
            profile = _scheme_profile(imported['profile'], identifier, package, rebase=True)
            reference = _scheme_reference(imported['reference'])
        require(0 < len(name) <= 48 and all(c.isprintable() for c in name), 'Enter a scheme name (1–48 characters)')
        state['schemes'].append({'id': 's-' + uuid.uuid4().hex, 'name': name, 'profile': profile, 'reference': reference})
    elif action == 'rename':
        require(request['name'].strip(), 'Enter a scheme name')
        selected['name'] = request['name'].strip()
    elif action == 'remove':
        require(selected['id'] != 'default', 'The Default scheme cannot be removed')
        require(selected['id'] != state['selected'], 'Select another scheme before removing this one')
        state['schemes'].remove(selected)
    elif action == 'reset':
        selected['profile'] = f'ANVIL_KEYMAP 1 {identifier} {package} 0\n'
        selected['reference'] = None
    elif action == 'select': state['selected'] = selected['id']
    target = next(s for s in state['schemes'] if s['id'] == state['selected'])['profile']
    encoded = json.dumps(state, ensure_ascii=True, separators=(',', ':'))
    require(len(encoded.encode()) <= SCHEME_META_LIMIT, 'Scheme library exceeds limit')
    return _scheme_result(state, _scheme_revision(target, encoded)), target, encoded


def _scheme_attach(lxc_path, container, script, input_text=''):
    with tempfile.TemporaryFile() as source:
        source.write(input_text.encode()); source.seek(0)
        return linux.run_bounded('lxc-attach', '-P', lxc_path, '-n', container, '--uid', '1000', '--gid', '1000',
            '--', '/system/bin/sh', '-c', script, stdin=source, timeout=5, operation='Keymap schemes',
            output_limit=600000, full_output=True, preserve_whitespace=True, umask=0o077)


def keymap_schemes(lxc_path, container, identifier, request, android, generation):
    validate_scheme_request(request)
    require(request['id'] == identifier, 'Invalid scheme runtime')
    require(isinstance(generation, str) and re.fullmatch(r'[0-9a-f]{32}', generation), 'Invalid runtime generation')
    package = request['package']
    # Package-manager output is authoritative; no caller-supplied file path.
    installed = android('pm', 'path', package).splitlines()
    require(installed and any(line.startswith('package:') for line in installed), 'App is not installed in this runtime')
    base = ('[ ! -L /data/waydroid_tmp ] && [ ! -L /data/waydroid_tmp/anvildroid ] || exit 2; '
            'mkdir -p /data/waydroid_tmp/anvildroid || exit 1; cd /data/waydroid_tmp/anvildroid || exit 1; '
            '[ ! -L keymaps ] || exit 2; mkdir -p keymaps || exit 1; cd keymaps || exit 1; ')
    safe = (f'a={package}; m=.schemes-{package}; '
            'for f in "$a" "$m"; do [ ! -e "$f" ] && [ ! -L "$f" ] && continue; '
            '[ -f "$f" ] && [ ! -L "$f" ] && [ "$(stat -c %h "$f")" = 1 ] || exit 2; done; ')
    # A dead writer leaves a tiny rollback journal in the lock directory.
    # Native editor refuses this lock, so no native save can be lost during
    # recovery. Process start time and generation prevent PID reuse mistakes.
    recover = (
        '[ ! -L .scheme-lock ] || exit 2; '
        'if [ -d .scheme-lock ]; then '
        'if [ ! -e .scheme-lock/pid ]; then '
        'stamp=$(stat -c %Y .scheme-lock); now=$(date +%s); '
        'if [ "$((now-stamp))" -le 10 ]; then echo "Scheme operation initializing; retry"; exit 3; fi; '
        '[ ! -e .scheme-lock/ready ] || exit 3; '
        'rm -f .scheme-lock/pid-tmp; rmdir .scheme-lock || exit 3; '
        'else '
        '[ -f .scheme-lock/pid ] && [ ! -L .scheme-lock/pid ] || exit 3; '
        'owner=$(head -c 128 .scheme-lock/pid); set -f; set -- $owner; '
        '[ "$#" = 3 ] || exit 3; case "$1:$2" in *[!0-9:]*|:*) exit 3;; esac; '
        'current=$(cut -d " " -f 22 /proc/"$1"/stat 2>/dev/null); '
        f'if [ "$3" = {generation} ] && [ "$2" = "$current" ] && kill -0 "$1" 2>/dev/null; then echo "Scheme operation busy; retry"; exit 3; fi; '
        'if [ -e .scheme-lock/ready ]; then '
        '[ ! -L .scheme-lock/record ] && [ -f .scheme-lock/record ] || exit 3; '
        'record=$(head -c 260 .scheme-lock/record); set -- $record; '
        '[ "$#" = 3 ] || exit 3; case "$1" in ""|.*|*[!A-Za-z0-9_.]*) exit 3;; esac; '
        'case "$2:$3" in 0:0|0:1|1:0|1:1) ;; *) exit 3;; esac; '
        'ra=$1; rm=.schemes-$1; '
        'if [ "$2" = 1 ]; then [ -f .scheme-lock/old-a ] && [ ! -L .scheme-lock/old-a ] && cp -p .scheme-lock/old-a .scheme-lock/restore-a && mv -f .scheme-lock/restore-a "$ra" || exit 3; else rm -f "$ra"; fi; '
        'if [ "$3" = 1 ]; then [ -f .scheme-lock/old-m ] && [ ! -L .scheme-lock/old-m ] && cp -p .scheme-lock/old-m .scheme-lock/restore-m && mv -f .scheme-lock/restore-m "$rm" || exit 3; else rm -f "$rm"; fi; '
        'fi; '
        'rm -f .scheme-lock/new-a .scheme-lock/new-m .scheme-lock/old-a .scheme-lock/old-m .scheme-lock/restore-a .scheme-lock/restore-m .scheme-lock/reload .scheme-lock/record .scheme-lock/ready .scheme-lock/pid; '
        'rmdir .scheme-lock || exit 3; fi; fi; ')
    acquire = ('mkdir .scheme-lock 2>/dev/null || { echo "Scheme operation busy; retry"; exit 3; }; '
        f'printf "%s %s %s\\n" "$$" "$(cut -d " " -f 22 /proc/$$/stat)" {generation} > .scheme-lock/pid-tmp && mv .scheme-lock/pid-tmp .scheme-lock/pid || exit 3; ')
    read = (base + recover + acquire +
            'cleanup_read() { rm -f .scheme-lock/pid-tmp .scheme-lock/pid; rmdir .scheme-lock; }; '
            'trap cleanup_read EXIT; trap "exit 4" HUP INT TERM; ' + safe +
            'if [ -e "$a" ]; then head -c 16385 "$a" | base64 | tr -d "\\n"; fi; printf "\\n"; '
            'if [ -e "$m" ]; then head -c 400001 "$m" | base64 | tr -d "\\n"; fi; printf "\\n"; ')
    output = _scheme_attach(lxc_path, container, read)
    lines = output.splitlines()
    require(len(lines) == 2, 'Invalid scheme snapshot')
    try:
        active, metadata = (base64.b64decode(line, validate=True).decode('utf-8') for line in lines)
    except (ValueError, UnicodeError) as error: raise RuntimeError('Invalid scheme snapshot encoding') from error
    require(len(active.encode()) <= 16384 and len(metadata.encode()) <= SCHEME_META_LIMIT, 'Scheme snapshot exceeds limit')
    result, target, encoded = _scheme_plan(request, active, metadata)
    if target is None: return result
    # Per-file renames are atomic. A journal keeps the pair recoverable even
    # if the writer receives SIGKILL between renames. The journal is removed
    # only after both files commit; a dead writer rolls back on the next call.
    ah, mh = hashlib.sha256(active.encode()).hexdigest(), hashlib.sha256(metadata.encode()).hexdigest()
    reload_nonce = uuid.uuid4().hex
    script = (base + recover +
        'mkdir .scheme-lock 2>/dev/null || { echo "Scheme operation busy; retry"; exit 3; }; '
        'ok=0; changed=0; '
        'cleanup() { if [ "$ok" = 0 ] && [ "$changed" = 1 ]; then '
        'if [ "$had_a" = 1 ]; then cp -p .scheme-lock/old-a .scheme-lock/restore-a && mv -f .scheme-lock/restore-a "$a" || return; else rm -f "$a"; fi; '
        'if [ "$had_m" = 1 ]; then cp -p .scheme-lock/old-m .scheme-lock/restore-m && mv -f .scheme-lock/restore-m "$m" || return; else rm -f "$m"; fi; fi; '
        'rm -f .scheme-lock/new-a .scheme-lock/new-m .scheme-lock/old-a .scheme-lock/old-m .scheme-lock/restore-a .scheme-lock/restore-m .scheme-lock/reload .scheme-lock/record .scheme-lock/ready .scheme-lock/pid-tmp .scheme-lock/pid; rmdir .scheme-lock; }; '
        'trap cleanup EXIT; trap "exit 4" HUP INT TERM; '
        f'printf "%s %s %s\\n" "$$" "$(cut -d " " -f 22 /proc/$$/stat)" {generation} > .scheme-lock/pid-tmp && mv .scheme-lock/pid-tmp .scheme-lock/pid || exit 3; '
        + safe +
        '[ ! -L ../keymap-scheme-protocol ] && [ -f ../keymap-scheme-protocol ] || { echo "Restart runtime to enable safe scheme changes"; exit 5; }; '
        f'[ "$(head -c 64 ../keymap-scheme-protocol)" = {generation} ] || {{ echo "Runtime scheme protocol changed; restart runtime"; exit 5; }}; '
        'if [ -e ../keymap-editing ] || [ -L ../keymap-editing ]; then '
        '[ ! -L ../keymap-editing ] && [ -f ../keymap-editing ] || exit 5; '
        'marker=$(head -c 320 ../keymap-editing); set -f; set -- $marker; '
        '[ "$#" = 3 ] || [ "$#" = 4 ] || { echo "Invalid editor marker; restart runtime"; exit 5; }; '
        'case "$3" in ""|*[!0-9]*) exit 5;; esac; '
        'marker_live=1; if [ "$#" = 4 ]; then case "$4" in ""|*[!0-9]*) exit 5;; esac; '
        'marker_start=$(cut -d " " -f 22 /proc/"$3"/stat 2>/dev/null); [ "$4" = "$marker_start" ] || marker_live=0; fi; '
        f'if [ "$1" = {generation} ] && [ "$marker_live" = 1 ] && kill -0 "$3" 2>/dev/null; then echo "Save or cancel the open keymap editor first"; exit 5; fi; '
        'rm -f ../keymap-editing || exit 5; fi; '
        # Android mksh aliases hash to its tracked-alias builtin, even after
        # a same-named function definition. Use a private name for the CAS digest.
        'anvil_scheme_digest() { if [ -e "$1" ]; then sha256sum "$1" | cut -d " " -f 1; else printf "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"; fi; }; '
        f'[ "$(anvil_scheme_digest "$a")" = {ah} ] && [ "$(anvil_scheme_digest "$m")" = {mh} ] || {{ echo "Schemes changed; refresh before retrying"; exit 6; }}; '
        'set -C; IFS= read -r aa && IFS= read -r mm || exit 7; '
        'printf %s "$aa" | base64 -d > .scheme-lock/new-a || exit 7; printf %s "$mm" | base64 -d > .scheme-lock/new-m || exit 7; '
        'had_a=0; had_m=0; if [ -e "$a" ]; then had_a=1; cp -p "$a" .scheme-lock/old-a || exit 7; fi; '
        'if [ -e "$m" ]; then had_m=1; cp -p "$m" .scheme-lock/old-m || exit 7; fi; '
        'printf "%s %s %s\\n" "$a" "$had_a" "$had_m" > .scheme-lock/record || exit 7; '
        'printf ready > .scheme-lock/ready || exit 7; changed=1; '
        'mv -f .scheme-lock/new-a "$a" && mv -f .scheme-lock/new-m "$m" || exit 7; '
        f'printf "%s %s %s\\n" {generation} "$a" {reload_nonce} > .scheme-lock/reload && mv -f .scheme-lock/reload ../keymap-scheme-reload || exit 7; '
        'rm -f .scheme-lock/ready || exit 7; ok=1; ')
    payload = base64.b64encode(target.encode()).decode() + '\n' + base64.b64encode(encoded.encode()).decode() + '\n'
    _scheme_attach(lxc_path, container, script, payload)
    return result
