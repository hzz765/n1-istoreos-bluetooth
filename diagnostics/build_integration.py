#!/usr/bin/env python3
"""Install reviewed diagnostic inputs, then attest the actual prepared kernel."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PATCH_NAME = '008-n1-uart-observe.patch'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def install(source):
    patch = source / 'target/linux/amlogic/patches-6.6' / PATCH_NAME
    if patch.exists() or patch.is_symlink():
        raise ValueError('diagnostic patch destination already exists')
    if not patch.parent.is_dir():
        raise ValueError('unexpected source layout')
    shutil.copyfile(HERE/'kernel-observe.patch', patch)
    collector = source/'files/usr/sbin/n1-bt-collect'
    collector.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE/'collect.sh', collector)
    collector.chmod(0o755)
    marker = source/'files/etc/n1-bt-diagnostic'
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('N1 UART diagnostic instrumentation; not a Bluetooth fix\n')


def attest(kernel, output):
    manifest = json.loads((HERE/'kernel-sources.json').read_text())
    original, patched = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        temp = Path(tmp)
        for rel, expected in manifest['sha256'].items():
            src = kernel/rel
            if src.is_symlink() or not src.is_file():
                raise ValueError('missing or symlink prepared source: '+rel)
            dst = temp/rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            patched[rel] = digest(src)
        # Reverse only a disposable copy. Any additional vendor change fails SHA.
        subprocess.run(['git','apply','--reverse','--check',str(HERE/'kernel-observe.patch')], cwd=temp, check=True)
        subprocess.run(['git','apply','--reverse',str(HERE/'kernel-observe.patch')], cwd=temp, check=True)
        for rel, expected in manifest['sha256'].items():
            original[rel] = digest(temp/rel)
            if original[rel] != expected:
                raise ValueError('prepared source differs from pinned Linux: '+rel)
    record = {'linux_version': manifest['version'], 'original_sha256': original,
              'prepared_sha256': patched, 'patch_sha256': digest(HERE/'kernel-observe.patch'),
              'collector_sha256': digest(HERE/'collect.sh')}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('install'); a.add_argument('source',type=Path)
    a=sub.add_parser('attest'); a.add_argument('kernel',type=Path); a.add_argument('output',type=Path)
    args=p.parse_args()
    if args.action=='install': install(args.source)
    else: attest(args.kernel,args.output)
    print('PASS: diagnostic '+args.action)

if __name__=='__main__': main()
