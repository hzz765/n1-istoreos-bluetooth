#!/usr/bin/env python3
"""Verify exact pristine Linux files; check only unless --apply is explicit."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent

def verify(root):
    manifest = json.loads((HERE/'kernel-sources.json').read_text())
    for rel, expected in manifest['sha256'].items():
        p = root/rel
        if p.is_symlink() or not p.is_file():
            raise ValueError('missing or symlink source: '+rel)
        if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise ValueError('source hash mismatch: '+rel)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path, help='pristine Linux 6.6.144 source root')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    verify(args.source)
    subprocess.run(['git','apply','--check',str(HERE/'kernel-observe.patch')],cwd=args.source,check=True)
    if args.apply:
        subprocess.run(['git','apply',str(HERE/'kernel-observe.patch')],cwd=args.source,check=True)
    print('PASS: exact source and patch '+('applied' if args.apply else 'checked; no changes'))

if __name__ == '__main__':
    main()
