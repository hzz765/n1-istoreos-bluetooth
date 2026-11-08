"""Explicit diagnostic provenance checks; no device access."""
import hashlib
import json
from pathlib import Path
import subprocess

HCD_SHA = '8bbf245399e66f68304ba0f9185159dd1d557c48b534f2e7be75e2e0f4cb4a9a'
# Actual, previously reviewed isolated image DTB: no DT change is permitted.
DTB_SHA = 'e9e20686916f49fd04c2ff552039784f5bec6e0976ef631cd2d3e66c625d1863'
SOURCE_COMMIT = '5fe2cddefc93d59156ca66740ed896ae954d8e8e'

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while data := f.read(1024*1024): h.update(data)
    return h.hexdigest()

def manifest(base, repo=Path('.')):
    m = json.loads((base/'diagnostic-build.json').read_text())
    assert m['schema'] == 1 and m['mode'] == 'usb-diagnostic'
    assert m['source_commit'] == SOURCE_COMMIT
    assert m['workflow_commit'] == subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    for key, path in [('image_sha256',base/'n1-usb-test.img.gz'),
                      ('kernel_config_sha256',base/'kernel.config'),
                      ('patch_sha256',repo/'diagnostics/kernel-observe.patch'),
                      ('collector_sha256',repo/'diagnostics/collect.sh')]:
        assert m[key] == sha(path), key
    return m

def inspect(root, boot, m):
    assert sha(boot/'kernel.img') == m['kernel_sha256']
    assert sha(boot/'amlogic.dtb') == m['dtb_sha256']
    actual={str(p.relative_to(root)):sha(p) for p in (root/'lib/modules').rglob('*.ko')}
    assert actual == m['modules'], 'Kernel module set differs from same-run build'
    assert sha(boot/'amlogic.dtb') == DTB_SHA, 'DTB differs from reviewed hardware/isolation configuration'
    assert sha(root/'lib/firmware/brcm/BCM4345C0.hcd') == HCD_SHA
    assert sha(root/'usr/sbin/n1-bt-collect') == m['collector_sha256']
    assert b'N1DIAG termios' in (boot/'kernel.img').read_bytes(), 'Missing built-in UART instrumentation'
    uart = list((root/'lib/modules').rglob('hci_uart.ko'))
    assert len(uart) == 1 and b'N1DIAG controller-baud' in uart[0].read_bytes(), 'Missing BCM instrumentation'
    for name in ['coreutils-timeout','bluez-utils-btmon']:
        assert (root/f'usr/lib/opkg/info/{name}.control').is_file(), name
    marker=root/'etc/n1-bt-diagnostic'
    assert marker.is_file(), 'Diagnostic build marker missing'

def autoload_entries(root):
    entries=[]
    for directory in ['etc/modules.d','etc/modules-boot.d']:
        folder=root/directory
        if not folder.exists(): continue
        assert not folder.is_symlink(), 'Unexpected autoload directory link'
        for p in folder.iterdir():
            target=p
            if p.is_symlink():
                # OpenWrt normally links early module entries into modules.d.
                link=p.readlink()
                assert directory=='etc/modules-boot.d' and link == Path('../modules.d')/p.name, f'Unexpected autoload link: {p}'
                target=root/'etc/modules.d'/p.name
                assert not target.is_symlink() and target.is_file(), f'Invalid autoload target: {p}'
            assert target.is_file(), f'Unexpected autoload entry: {p}'
            entries.append((p,target))
    return entries

def remove_nbd_autoload(root):
    # Resolve/validate the complete normal OpenWrt layout before any mutation.
    entries=autoload_entries(root)
    for target in {target for _,target in entries}:
        lines=target.read_text().splitlines(keepends=True)
        kept=[line for line in lines if not line.split('#',1)[0].split() or line.split('#',1)[0].split()[0] != 'nbd']
        if kept != lines:
            if any(x.strip() and not x.lstrip().startswith('#') for x in kept): target.write_text(''.join(kept))
            else: target.unlink()
    for p,target in entries:
        if p != target and not target.exists(): p.unlink()

def verify_no_nbd_autoload(root):
    for _,target in autoload_entries(root):
        for line in target.read_text().splitlines():
            words=line.split('#',1)[0].split()
            assert not words or words[0] != 'nbd', f'Unexpected NBD autoload: {target}'

def verify_target_report(root, report):
    d=json.loads(report.read_text())
    assert d['status']=='PASS_ARM_USERSPACE'
    for path, digest in d['sha256'].items():
        assert not path.startswith('/') and '..' not in Path(path).parts
        assert sha(root/path)==digest, path
