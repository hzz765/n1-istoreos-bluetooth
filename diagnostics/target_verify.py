#!/usr/bin/env python3
"""Run isolated target userspace under QEMU, without proc/sys/device mounts."""
import hashlib
import json
import os
import signal
from pathlib import Path
import shutil
import subprocess
import sys

root=Path(sys.argv[1]).resolve(); report=Path(sys.argv[2])
assert root.is_dir() and root != Path('/')
qemu=root/'tmp/n1-diag-qemu'
assert not qemu.exists()
records={}

def binary(path):
    # Resolve absolute links inside the target, never into host /usr/bin.
    p=root/path.lstrip('/')
    for _ in range(12):
        if not p.is_symlink(): break
        link=p.readlink()
        p=root/str(link).lstrip('/') if link.is_absolute() else p.parent/link
    p=p.resolve()
    assert p.is_relative_to(root) and p.is_file(), path
    data=p.read_bytes()
    assert data[:4]==b'\x7fELF' and data[18:20]==b'\xb7\x00', f'Not ARM64 ELF: {path}'
    records[str(p.relative_to(root))]=hashlib.sha256(data).hexdigest()
    return '/'+str(p.relative_to(root))

def run(*args, expected=0):
    process=subprocess.Popen(['chroot',str(root),'/tmp/n1-diag-qemu',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:
        stdout,stderr=process.communicate(timeout=15)
    finally:
        # Includes an unexpectedly surviving child even if its parent exits.
        try: os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
    assert process.returncode in (expected if isinstance(expected, tuple) else (expected,)), (args,process.returncode,stdout,stderr)
    return (stdout+stderr).strip()

try:
    shutil.copyfile('/usr/bin/qemu-aarch64-static',qemu);qemu.chmod(0o755)
    timeout=binary('/usr/bin/timeout'); busybox=binary('/bin/busybox'); btmon=binary('/usr/bin/btmon')
    version=run(timeout,'--version'); assert 'GNU coreutils' in version
    btversion=run(btmon,'--version'); assert btversion
    run(busybox,'sh','-n','/usr/sbin/n1-bt-collect')
    # Explicit QEMU for the child avoids reliance on host binfmt registration.
    run(timeout,'-s','TERM','-k','1','0.3','/tmp/n1-diag-qemu',busybox,'sh','-c',
        "trap 'echo TERM-observed > /tmp/n1-diag-term; exit 0' TERM; while :; do :; done",expected=124)
    assert (root/'tmp/n1-diag-term').read_text().strip()=='TERM-observed'
    run(timeout,'-s','TERM','-k','0.3','0.3','/tmp/n1-diag-qemu',busybox,'sh','-c',
        "trap '' TERM; while :; do :; done",expected=(137,-9))
    records['usr/sbin/n1-bt-collect']=hashlib.sha256((root/'usr/sbin/n1-bt-collect').read_bytes()).hexdigest()
    report.write_text(json.dumps({'status':'PASS_ARM_USERSPACE','timeout_version':version,'btmon_version':btversion,
        'tests':['ARM64 ELF','GNU timeout','TERM exit124 with child trap','KILL escalation137','BusyBox shell syntax','btmon version'],
        'limits':'QEMU userspace only; no kernel/device/hardware execution','sha256':records},indent=2)+'\n')
finally:
    qemu.unlink(missing_ok=True)
    (root/'tmp/n1-diag-term').unlink(missing_ok=True)
