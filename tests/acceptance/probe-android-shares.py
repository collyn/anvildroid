#!/usr/bin/env python3
"""Probe only an explicitly identified disposable live worker; never start/stop it.

Run as root with acceptance.json and the host PID of its runtime-worker.py.
A configured source must contain `acceptance-read` with the supplied token.
RO/RW modes attempt a uniquely named write through raw-data and Android FUSE
paths. Output keeps those results separate; neither establishes app access.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex


def module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).resolve().parents[2]/'services'/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata',type=Path)
    parser.add_argument('worker_pid',type=int)
    parser.add_argument('share')
    parser.add_argument('token')
    parser.add_argument('--expect',choices=('ro','rw','removed'),required=True)
    args=parser.parse_args()
    if os.geteuid()!=0:parser.error('root required')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}',args.share):parser.error('invalid share name')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.token):parser.error('invalid token')
    metadata=args.metadata
    root=metadata.parent
    if metadata.name!='acceptance.json' or root.parent!=Path('/tmp') or not root.name.startswith('anvil-live-') or root.resolve()!=root or metadata.is_symlink():parser.error('require direct disposable /tmp/anvil-live-*/acceptance.json')
    info=root.lstat()
    if not root.is_dir() or info.st_uid!=0 or info.st_mode & 0o077:parser.error('unsafe disposable root')
    info=metadata.lstat()
    if info.st_uid!=0 or info.st_mode & 0o022:parser.error('untrusted metadata')
    value=json.loads(metadata.read_text());instance=Path(value['instance'])
    if instance.parent!=root/'instances' or instance.resolve()!=instance or instance.name!=value['id']:parser.error('instance must belong to disposable metadata root')
    backend=module('runtime-android').AndroidBackend(module('runtime-images').ImageBackend(metadata.parent,metadata.parent/'source'))
    if backend.instance(value['id']) != instance:parser.error('instance derivation mismatch')
    def authenticate():
        report=backend.status(value['id'])
        if report.get('generation')!=value['generation'] or report.get('state')!='Running':parser.error('worker generation/state changed')
        return report
    linux=module('runtime-linux')
    shares=module('runtime-shares')
    worker_start=shares.process_start(args.worker_pid)
    worker_mnt=os.readlink(f'/proc/{args.worker_pid}/ns/mnt')
    def worker_identity():
        if shares.process_start(args.worker_pid)!=worker_start or os.readlink(f'/proc/{args.worker_pid}/ns/mnt')!=worker_mnt:parser.error('worker PID or namespace changed')
    authenticate();worker_identity()
    command=Path(f'/proc/{args.worker_pid}/cmdline').read_bytes().split(b'\0')
    if not any(Path(os.fsdecode(item)).name=='runtime-worker.py' for item in command if item) or os.fsencode(instance) not in command or os.fsencode(value['generation']) not in command:parser.error('PID does not identify this disposable worker')
    if os.readlink(f'/proc/{args.worker_pid}/ns/mnt')==os.readlink('/proc/self/ns/mnt'):parser.error('worker must have a private mount namespace')
    prefix=['nsenter','-t',str(args.worker_pid),'-m','-n','-p','--','lxc-attach','-P',str(instance/'android'),'-n',value['id'],'--set-var','PATH=/system/bin:/system/xbin:/vendor/bin','--','/system/bin/sh','-c']
    def execute(command):
        output=linux.run_bounded(*prefix,command+'; rc=$?; printf "\\n__ANVIL_PROBE_RC__%s\\n" "$rc"; exit 0',timeout=10,full_output=True,output_limit=4096,operation='Disposable Android share probe')
        body,marker,code=output.rpartition('\n__ANVIL_PROBE_RC__')
        if not marker:
            if output.startswith('__ANVIL_PROBE_RC__'):body,code='',output[len('__ANVIL_PROBE_RC__'):]
            else:raise RuntimeError('Guest probe omitted result marker')
        return int(code),body
    results={}
    for label,base in [('raw','/data/media/0'),('fuse','/sdcard')]:
        target=f'{base}/AnvilDroid/{args.share}'
        read=shlex.quote(target+'/acceptance-read')
        code,output=execute('cat '+read)
        item={'read_returncode':code,'read_output':output}
        if args.expect=='removed':item['pass']=code!=0
        else:
            write=shlex.quote(target+'/acceptance-write-'+args.token+'-'+label)
            code,output=execute('printf %s '+shlex.quote(args.token)+' > '+write)
            item.update(write_returncode=code,write_output=output)
            item['pass']=item['read_returncode']==0 and item['read_output']==args.token and (code==0 if args.expect=='rw' else code!=0)
        results[label]=item
    authenticate();worker_identity()
    print(json.dumps({'runtime_id':value['id'],'generation':value['generation'],'package_sha256':value.get('package_sha256'),'expect':args.expect,'results':results},indent=2))
    raise SystemExit(0 if all(item['pass'] for item in results.values()) else 1)


if __name__=='__main__':main()
