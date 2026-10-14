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
              'printf "BEGIN\\n"; head -c 4096 "$f"; printf "END\\n"; done')
    output = linux.run_bounded('lxc-attach', '-P', lxc_path, '-n', container, '--uid','1000','--gid','1000','--',
                              '/system/bin/sh', '-c', script, timeout=4, operation='Keymap inventory', output_limit=512*1024, full_output=True)
    return parse_keymaps(output, identifier)


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
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
                    invalid += 1
                    continue
                fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
                try:
                    current = os.fstat(fd)
                    require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1 and
                            (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), 'Keymap file changed')
                    raw = os.read(fd, 4097)
                finally:
                    os.close(fd)
                try:
                    require(len(raw) <= 4096, 'Keymap profile exceeds limit')
                    text = raw.decode('utf-8')
                    require(text.endswith('\n'), 'Incomplete keymap profile')
                    blocks.append('BEGIN\n' + text + 'END\n')
                except (UnicodeError, RuntimeError):
                    invalid += 1
        result = parse_keymaps(''.join(blocks), identifier)
        result['invalid_count'] += invalid
        return result
    finally:
        os.close(directory)


def parse_keymaps(output, identifier):
    require(len(output.encode()) <= 512*1024, 'Keymap inventory exceeds limit')
    profiles=[]; errors=0
    for block in output.split('BEGIN\n')[1:]:
        try:
            lines=block.splitlines(); header=lines[0].split()
            require(len(header)==5 and header[:2]==['ANVIL_KEYMAP','1'] and header[2]==identifier, 'Invalid keymap scope')
            package=header[3]; count=int(header[4])
            require(re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+',package) and len(package)<=240 and 0<=count<=64, 'Invalid keymap')
            require(len(lines)==count+2 and lines[-1]=='END', 'Incomplete keymap')
            bindings=[];keys=set()
            for index,line in enumerate(lines[1:-1]):
                key,x,y,hold=map(int,line.split())
                require(2<=key<=767 and key not in keys and 0<=x<=65535 and 0<=y<=65535 and hold in (0,1), 'Invalid binding')
                keys.add(key);bindings.append({'id':f'point-{index}','evdev':key,'nx':x,'ny':y,'kind':'hold' if hold else 'tap'})
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
            message=('Overlay attached. Add → place point → press key, then Save and close the editor. Focus the app to use mapped keys.'
                     if reply[1]=='editing' else 'Overlay '+reply[1]+'.')
            return {'action':'keymap_edit','package':package,'success':True,'message':message}
        time.sleep(.05)
    raise RuntimeError('Overlay did not acknowledge: update/restart this runtime and keep its app window open')

