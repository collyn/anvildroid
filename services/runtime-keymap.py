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
                              '/system/bin/sh', '-c', script, timeout=4, operation='Keymap inventory', output_limit=3*1024*1024, full_output=True)
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
            require(len(header)==5 and header[0]=='ANVIL_KEYMAP' and header[1] in ('1','2','3') and header[2]==identifier, 'Invalid keymap scope')
            require(re.fullmatch(r'[0-9]{1,15}',header[4]), 'Invalid keymap count')
            package=header[3]; count=int(header[4])
            require(re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+',package) and len(package)<=240 and 0<=count<=64, 'Invalid keymap')
            require(len(block.rsplit('END',1)[0].encode()) <= 16384, 'Keymap profile exceeds limit')
            require(len(lines)==count+2 and lines[-1]=='END', 'Incomplete keymap')
            require(package not in packages, 'Duplicate keymap package')
            bindings=[];keys=set()
            for index,line in enumerate(lines[1:-1]):
                require(re.fullmatch(r'[ \t]*[0-9]+(?:[ \t]+[0-9]+)*[ \t]*\r?',line), 'Invalid decimal binding')
                tokens=line.split()
                require(all(len(token)<=15 for token in tokens), 'Invalid decimal width')
                values=list(map(int,tokens))
                require(all(value<=65535 for value in values), 'Invalid decimal bound')
                require(len(values)=={'1':4,'2':9,'3':19}[header[1]], 'Invalid binding fields')
                key,x,y,hold=values[:4]
                kind,ex,ey,duration,repeats=values[4:9] if header[1]!='1' else (0,0,0,0,0)
                require(0<=kind<=(8 if header[1]=='3' else 6) and 0<=ex<=65535 and 0<=ey<=65535 and
                        0<=duration<=5000 and 0<=repeats<=20 and
                        (kind==0 or 50<=duration<=5000 and 1<=repeats<=20) and
                        (kind!=5 or duration*repeats*2<=10000) and
                        (kind!=1 or key==17), 'Invalid advanced control')
                source,end,radius,ramp,lw,lh,sx,sy,invert,activation=values[9:] if header[1]=='3' else (0,)*10
                require(source in (0,1,2) and end in (0,1,2) and (radius==0 or 256<=radius<=32767) and
                        0<=ramp<=1000 and 0<=lw<=32767 and 0<=lh<=32767 and 0<=sx<=1000 and 0<=sy<=1000 and
                        invert in (0,1) and activation in (0,1), 'Invalid V3 parameters')
                require((source==2)==(kind==7) and (source!=2 or key==0) and
                        (source!=1 or 272<=key<=276) and (source!=0 or 2<=key<=767) and
                        (kind!=1 or source==0), 'Invalid input source')
                require(kind!=8 or (lw>=256 and lh>=256 and lw<=x<=65535-lw and lh<=y<=65535-lh and sx>=10 and sy>=10), 'Invalid look region')
                require((source,key) not in keys and 0<=x<=65535 and 0<=y<=65535 and hold in (0,1), 'Invalid binding')
                keys.add((source,key));bindings.append({'id':f'point-{index}','evdev':key,'nx':x,'ny':y,
                    'kind':('hold' if end in (0,2) else 'tap') if not kind else ('wasd','swipe','repeat','skill','macro','aim','mouse_touch','look')[kind-1],
                    **({'end_nx':ex,'end_ny':ey,'duration_ms':duration,'repeat_count':repeats} if kind else {}),
                    **({'input_source':('keyboard','mouse','pointer')[source], 'end_mode':('legacy','release','hold')[end],
                        'radius':radius,'ramp_ms':ramp,'look_width':lw,'look_height':lh,'sensitivity_x':sx,'sensitivity_y':sy,
                        'invert_y':invert,'activation':('hold','capture')[activation]} if header[1]=='3' else {})})
            require(not any(b['kind']=='wasd' for b in bindings) or not keys.intersection({(0,30),(0,31),(0,32)}), 'Conflicting WASD keys')
            require(sum(b['kind']=='look' for b in bindings)<=1 and sum(b['kind']=='mouse_touch' for b in bindings)<=1, 'Duplicate pointer control')
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
            message=('Overlay attached. Choose a control type, Add → place it, then use Key... or Mouse... to bind. Adjust the control and Save. Focus the app to play; Esc releases captured mouse input.'
                     if reply[1]=='editing' else 'Overlay '+reply[1]+'.')
            return {'action':'keymap_edit','package':package,'success':True,'message':message}
        time.sleep(.05)
    raise RuntimeError('Overlay did not acknowledge: update/restart this runtime and keep its app window open')

