#!/usr/bin/env python3
"""Offline HCD-only control. Opens regular files, never block devices or mounts.

The reference is the published, independently reviewed isolated diagnostic image.
MBR, USB identity, FAT boot, coherent kernel/DTB/modules and empty overlay remain
unchanged. This retains identity eed4d114 (not the internal eMMC identity); never
attach two clones together. Only HCD payload, collector's pinned digest and an
explicit experiment record change. This is a candidate experiment, not a fix.
Run as root on Linux for faithful squashfs metadata and unmounted QEMU chroot.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys

BASE_GZ_SHA = 'b7c94a1d64865e95187f484324fd17c41165d5b80d31ff25c4d65e2e522ca096'
BASE_RAW_SHA = 'a94463ab4785f1114ebc24ab7da6e4cacee6bc6c7855f3c20411f0a21866b270'
BASE_LENGTH = 632291328
OLD_HCD_SHA = '8bbf245399e66f68304ba0f9185159dd1d557c48b534f2e7be75e2e0f4cb4a9a'
NEW_HCD_SHA = '51c45e77ddad91a19e96dc8fb75295b2087c279940df2634b23baf71b6dea42c'
COLLECTOR_SHA = '6accb35a5184a790b6b1057197cc082b8698549c1767511a21b9fc02cba950a8'
DTB_SHA = 'e9e20686916f49fd04c2ff552039784f5bec6e0976ef631cd2d3e66c625d1863'
KERNEL_SHA = '017cd5d3fe46365826a1fc0446bf1a1db2302b5e3ea3d737aa98444382de6c45'
HCD = 'lib/firmware/brcm/BCM4345C0.hcd'
COLLECTOR = 'usr/sbin/n1-bt-collect'
MARKER = 'etc/n1-bt-hcd-control.json'
SIGNATURE = 'eed4d114'
BASE_RUN = 42491553515

def require(condition, message):
    if not condition:
        raise ValueError(message)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def regular(path):
    p = Path(path)
    require(stat.S_ISREG(p.lstat().st_mode), f'Expected regular non-symlink file: {p}')
    return p

def validate_inputs(base, hcd):
    regular(base); regular(hcd)
    require(sha(base) == BASE_GZ_SHA, 'Reference compressed image checksum differs')
    require(hcd.stat().st_size == 63806 and sha(hcd) == NEW_HCD_SHA, 'Control HCD checksum/size differs')
    data = hcd.read_bytes(); off = 0; ops = {}
    while off < len(data):
        require(off + 3 <= len(data), 'Truncated HCD header')
        opcode, length = struct.unpack_from('<HB', data, off); off += 3
        require(off + length <= len(data), 'Truncated HCD payload')
        off += length; ops[hex(opcode)] = ops.get(hex(opcode), 0) + 1
    require(ops == {'0xfc4c': 322, '0xfc4e': 1}, 'Unexpected HCD command structure')

def partitions(mbr, length=BASE_LENGTH):
    require(len(mbr) == 512 and mbr[510:] == b'\x55\xaa', 'Bad MBR')
    require(mbr[440:444] == bytes.fromhex(SIGNATURE)[::-1], 'Unexpected disk signature')
    require(mbr[494:510] == bytes(16), 'Unexpected fourth partition')
    result = []
    for i in range(3):
        start, sectors = struct.unpack_from('<II', mbr, 454 + i * 16)
        require(start > 0 and sectors > 0, 'Empty partition')
        require(mbr[450 + i * 16] == (14 if i == 0 else 131), 'Unexpected partition type')
        result.append((start * 512, sectors * 512))
    require(result == [(1048576, 67108864), (68157440, 27262976), (95420416, 536870912)], 'Unexpected reference layout')
    require(sum(result[-1]) == length, 'Disk length mismatch')
    return result

def copy_region(source, target, offset, length):
    with regular(source).open('rb') as src, target.open('xb') as dst:
        src.seek(offset)
        while length:
            data = src.read(min(length, 1024 * 1024))
            require(data, 'Short region read'); dst.write(data); length -= len(data)

def region_sha(path, offset, length):
    h = hashlib.sha256()
    with regular(path).open('rb') as f:
        f.seek(offset)
        while length:
            data = f.read(min(length, 1024 * 1024))
            require(data, 'Short region read'); h.update(data); length -= len(data)
    return h.hexdigest()

def tree(root):
    """lstat-only walk; symlinks are recorded, never followed into the host."""
    entries = {}; hardlinks = {}
    paths = [root]
    def visit(folder):
        for child in sorted(folder.iterdir()):
            paths.append(child)
            if stat.S_ISDIR(child.lstat().st_mode):
                visit(child)
    visit(root)
    for p in paths:
        s = p.lstat(); mode = s.st_mode; name = str(p.relative_to(root))
        item = {'mode': mode, 'uid': s.st_uid, 'gid': s.st_gid, 'mtime': int(s.st_mtime)}
        item['xattrs'] = {k: os.getxattr(p, k, follow_symlinks=False).hex()
                          for k in sorted(os.listxattr(p, follow_symlinks=False))}
        if stat.S_ISREG(mode):
            item.update(size=s.st_size, sha256=sha(p))
            hardlinks.setdefault((s.st_dev, s.st_ino), []).append(name)
        elif stat.S_ISLNK(mode):
            item['target'] = os.readlink(p)
        elif stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
            item['rdev'] = [os.major(s.st_rdev), os.minor(s.st_rdev)]
        else:
            require(stat.S_ISDIR(mode) or stat.S_ISFIFO(mode), f'Unsupported file type: {name}')
        entries[name] = item
    for names in hardlinks.values():
        if len(names) > 1:
            for name in names: entries[name]['hardlinks'] = sorted(names)
    return entries

def check_path(root, name):
    parts = Path(name).parts
    require(not Path(name).is_absolute() and '..' not in parts, 'Unsafe relative path')
    p = root
    for component in parts:
        p = p / component
        require(not p.is_symlink(), f'Mutation path contains symlink: {name}')
    return regular(p)

def experiment():
    return {'schema': 1, 'experiment': 'HCD-only-control-B', 'not_a_proven_fix': True,
            'baseline_run': BASE_RUN, 'baseline_image_sha256': BASE_RAW_SHA,
            'baseline_hcd_sha256': OLD_HCD_SHA, 'hcd_sha256': NEW_HCD_SHA,
            'hcd_size': 63806, 'identity': SIGNATURE + '-02',
            'unchanged': ['kernel', 'all modules', 'DTB', 'GPIO', 'RTS/CTS', '2Mbps',
                          'eMMC isolation', 'USB-only loader', 'empty USB overlay'],
            'notes': 'Reuses reference USB identity; attach only one test USB. Collector digest updated to the single control HCD. No automatic power/scan actions.'}

def change_root(root, hcd):
    before = tree(root)
    aliases = [n for n in before if Path(n).name.lower().startswith('bcm4345c0')]
    require(aliases == [HCD], 'Unexpected BCM4345C0 firmware alias/path')
    require(MARKER not in before, 'Control marker already present')
    hp = check_path(root, HCD); cp = check_path(root, COLLECTOR)
    require(hp.stat().st_nlink == cp.stat().st_nlink == 1, 'Mutation target has hard links')
    require(sha(hp) == OLD_HCD_SHA and sha(cp) == COLLECTOR_SHA, 'Reference runtime files differ')
    text = cp.read_bytes()
    require(text.count(OLD_HCD_SHA.encode()) == 1, 'Expected exactly one pinned collector HCD digest')
    directory_times = {root: root.stat(), root/'etc': (root/'etc').stat()}
    for p, data in [(hp, hcd.read_bytes()), (cp, text.replace(OLD_HCD_SHA.encode(), NEW_HCD_SHA.encode()))]:
        s = p.stat(); p.write_bytes(data); os.utime(p, ns=(s.st_atime_ns, s.st_mtime_ns))
    marker = root / MARKER
    require(not marker.parent.is_symlink(), 'Marker parent is symlink')
    with marker.open('x') as f: f.write(json.dumps(experiment(), sort_keys=True, indent=2) + '\n')
    marker.chmod(0o644)
    os.utime(marker, (int(directory_times[root/'etc'].st_mtime),) * 2)
    for p, s in directory_times.items(): os.utime(p, ns=(s.st_atime_ns, s.st_mtime_ns))
    after = tree(root); audit_changes(before, after, marker.read_bytes())
    return before, after

def audit_changes(before, after, marker_bytes):
    require(set(after) - set(before) == {MARKER} and not set(before) - set(after), 'Unexpected root file addition/removal')
    differences = {n for n in before if before[n] != after[n]}
    require(differences == {HCD, COLLECTOR}, f'Unexpected root changes: {sorted(differences)}')
    for n in [HCD, COLLECTOR]:
        require({k:v for k,v in before[n].items() if k not in ['size','sha256']} ==
                {k:v for k,v in after[n].items() if k not in ['size','sha256']}, f'Metadata changed: {n}')
    require(after[HCD]['sha256'] == NEW_HCD_SHA, 'Wrong rebuilt HCD')
    expected = (json.dumps(experiment(), sort_keys=True, indent=2) + '\n').encode()
    require(marker_bytes == expected, 'Unexpected marker contents')
    require(after[MARKER]['sha256'] == hashlib.sha256(expected).hexdigest(), 'Marker hash mismatch')

def call(*args):
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.STDOUT, text=True)

def arm_check(root):
    # Never mount proc, sys or dev. No Bluetooth command other than version runs.
    tmp = root/'tmp'
    require(tmp.is_dir() and not tmp.is_symlink(), 'QEMU temporary directory must be a real in-image directory')
    original = tmp.stat(); q = tmp/'hcd-control-qemu'
    require(not q.exists() and not q.is_symlink(), 'QEMU temp path exists')
    shutil.copyfile('/usr/bin/qemu-aarch64-static', q); q.chmod(0o755)
    def run(*args):
        return subprocess.check_output(['chroot', str(root.resolve()), '/tmp/hcd-control-qemu', *args],
                                       stderr=subprocess.STDOUT, text=True, timeout=20).strip()
    try:
        run('/bin/busybox', 'sh', '-n', '/' + COLLECTOR)
        timeout_version = run('/usr/bin/timeout', '--version')
        btmon_version = run('/usr/bin/btmon', '--version')
        require('GNU coreutils' in timeout_version, 'Missing GNU timeout')
        return {'status': 'PASS_ARM_USERSPACE', 'checks': ['collector BusyBox syntax', 'GNU timeout version', 'btmon version'],
                'timeout_version': timeout_version, 'btmon_version': btmon_version,
                'limits': 'No device/driver execution; no HCI command; no proc/sys/dev mounts'}
    finally:
        q.unlink(); os.utime(tmp, ns=(original.st_atime_ns, original.st_mtime_ns))

def install_root_partition(image, squashfs, offset, capacity):
    size = regular(squashfs).stat().st_size
    require(size > 0 and size <= capacity, 'Rebuilt squashfs exceeds fixed root partition')
    regular(image)
    with image.open('r+b') as dst, squashfs.open('rb') as src:
        dst.seek(offset); shutil.copyfileobj(src, dst)
        left = capacity - size
        while left:
            n = min(left, 1024 * 1024); dst.write(bytes(n)); left -= n

def inflate_verified(base, disk):
    with gzip.open(base, 'rb') as src, disk.open('xb') as dst:
        count = 0
        while data := src.read(1024 * 1024):
            count += len(data); require(count <= BASE_LENGTH, 'Oversized decompressed image'); dst.write(data)
    require(count == BASE_LENGTH and sha(disk) == BASE_RAW_SHA, 'Reference raw image checksum/length differs')

def package(base, hcd, work, output):
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Use Linux root for faithful metadata; no device mounts are performed')
    validate_inputs(base, hcd)
    require(not work.exists() and not work.is_symlink() and not output.exists() and not output.is_symlink(), 'Work/output must be new paths')
    require(work.resolve() != output.resolve() and work.resolve() not in output.resolve().parents and output.resolve() not in work.resolve().parents, 'Work and output must be separate')
    work.mkdir(parents=True); disk = work/'reference.img'
    inflate_verified(base, disk)
    with disk.open('rb') as f: regions = partitions(f.read(512))
    boot = work/'boot.fat'; oldfs = work/'reference.squashfs'; overlay = work/'overlay.ext4'
    for target, (offset, size) in zip([boot, oldfs, overlay], regions): copy_region(disk, target, offset, size)
    root = work/'root'; print(call('unsquashfs', '-d', root, oldfs))
    # Exact source identity already bounds all safety files; also check key invariants directly.
    call('mcopy', '-i', boot, '::/amlogic.dtb', work/'amlogic.dtb')
    call('mcopy', '-i', boot, '::/kernel.img', work/'kernel.img')
    require(sha(work/'amlogic.dtb') == DTB_SHA and sha(work/'kernel.img') == KERNEL_SHA, 'Reviewed kernel/DTB differ')
    mmc = call('fdtget','-t','s',work/'amlogic.dtb','/aliases','mmc1').strip()
    require(call('fdtget','-t','s',work/'amlogic.dtb',mmc,'status').strip() == 'disabled', 'eMMC is not disabled')
    require((root/'etc/n1-bt-partuuid').read_text().strip() == SIGNATURE+'-02', 'Root identity differs')
    require('rescue-authorized_keys' not in call('mdir','-i',boot,'::/'), 'Public source image unexpectedly personalized')
    before, changed = change_root(root, hcd)
    arm = arm_check(root); require(tree(root) == changed, 'ARM smoke test altered root tree')
    newfs = work/'control.squashfs'
    print(call('mksquashfs',root,newfs,'-noappend','-comp','xz','-b','256k','-all-root'))
    actual = work/'control.img'; shutil.copyfile(disk, actual)
    install_root_partition(actual,newfs,*regions[1])
    checked = work/'readback.squashfs'; copy_region(actual,checked,*regions[1])
    verified = work/'verified-root'; print(call('unsquashfs','-d',verified,checked))
    after = tree(verified); require(after == changed, 'Rebuilt squashfs tree/metadata differs')
    audit_changes(before, after, (verified/MARKER).read_bytes())
    unchanged = {}
    for name, offset, length in [('mbr_gap_and_entire_boot', 0, regions[1][0]), ('entire_fresh_overlay', *regions[2])]:
        digest = region_sha(disk, offset, length)
        require(region_sha(actual, offset, length) == digest, f'Protected region changed: {name}')
        unchanged[name] = {'offset':offset, 'length':length, 'sha256':digest}
    require(actual.stat().st_size == BASE_LENGTH, 'Final image length changed')
    with actual.open('rb') as f: partitions(f.read(512))
    output.mkdir(parents=True)
    image = output/'n1-hcd-control.img.gz'
    with actual.open('rb') as src, image.open('xb') as dst:
        with gzip.GzipFile(filename='',mode='wb',fileobj=dst,mtime=0) as gz: shutil.copyfileobj(src,gz)
    raw_digest = sha(actual)
    gz_digest = hashlib.sha256(); gz_count = 0
    with gzip.open(image, 'rb') as f:
        while data := f.read(1024 * 1024):
            gz_count += len(data); require(gz_count <= BASE_LENGTH, 'Compressed artifact expands too far'); gz_digest.update(data)
    require(gz_count == BASE_LENGTH and gz_digest.hexdigest() == raw_digest, 'Compressed artifact readback differs')
    report = dict(experiment(), status='PASS_STATIC_HCD_CONTROL', raw_image_sha256=raw_digest, raw_image_length=BASE_LENGTH,
                  compressed_image_sha256=sha(image), unchanged_regions=unchanged, root_partition=regions[1],
                  squashfs_bytes=newfs.stat().st_size, root_entries=len(after),
                  validation_limits='No hardware success claim. No device opened or mounted. Baseline behavior comparison still requires the controlled USB test.')
    (output/'comparison.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    (output/'tree-diff.json').write_text(json.dumps({n:{'before':before.get(n),'after':after[n]} for n in [HCD,COLLECTOR,MARKER]},indent=2,sort_keys=True)+'\n')
    (output/'arm-checks.json').write_text(json.dumps(arm,indent=2,sort_keys=True)+'\n')
    (output/'SHA256SUMS').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in sorted(output.iterdir())))
    print('PASS: only HCD, pinned collector digest and explicit marker differ; boot and overlay remain byte-identical.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['baseline','hcd','work','output']: parser.add_argument('--'+name,required=True,type=Path)
    args = parser.parse_args(); package(args.baseline,args.hcd,args.work,args.output)
