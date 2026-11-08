#!/usr/bin/python3
"""N1 rescue: identify devices, enforce software RO, and create offline backups.

No restore, format, filesystem repair, environment-write or source-write operation.
Device inventories and backups are private runtime outputs, never build inputs.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def sysdev(name):
    check(re.fullmatch(r'[a-zA-Z0-9_-]+', name), 'Invalid block device name')
    return Path('/sys/class/block') / name


def dev(name):
    return '/dev/' + name


def parent(name):
    p = sysdev(name)
    return p.resolve().parent.name if (p / 'partition').exists() else name


def usb(name):
    return any(re.fullmatch(r'usb\d+', p) for p in sysdev(name).resolve().parts)


def mounted_numbers():
    return {line.split()[2] for line in Path('/proc/self/mountinfo').read_text().splitlines()}


def rescue_root():
    check(Path('/etc/n1-rescue').is_file(), 'Not the rescue image')
    roots = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()
             if line.split()[4] == '/']
    check(len(roots) == 1, 'Ambiguous root mount')
    record = roots[0]
    separator = record.index('-')
    check(record[separator + 1] == 'squashfs' and 'ro' in record[5].split(','), 'Root must be read-only squashfs')
    found = [p.name for p in Path('/sys/class/block').iterdir()
             if (p / 'dev').read_text().strip() == record[2]]
    check(len(found) == 1, 'Cannot identify root device')
    name = found[0]
    check(usb(name) and (sysdev(name) / 'partition').read_text().strip() == '2', 'Root must be USB partition 2')
    args = Path('/proc/cmdline').read_text().split()
    check('init=/sbin/n1-rescue-init' in args, 'Wrong init')
    return name


def emmc_devices():
    candidates = []
    for path in Path('/sys/class/block').glob('mmcblk*'):
        typ = path / 'device/type'
        if typ.exists() and typ.read_text().strip() == 'MMC' and re.fullmatch(r'mmcblk\d+', path.name):
            candidates.append(path.name)
    check(len(candidates) == 1, 'Expected exactly one physical eMMC')
    main = candidates[0]
    devices = [main] + sorted(p.name for p in Path('/sys/class/block').glob(main + 'p*'))
    devices += [main + 'boot0', main + 'boot1']
    check(all(sysdev(name).exists() for name in devices), 'Missing eMMC boot areas')
    return main, devices


def guard(protect=False):
    rescue_root()
    main, devices = emmc_devices()
    mounts = mounted_numbers()
    swaps = Path('/proc/swaps').read_text().splitlines()[1:]
    check(not swaps, 'Swap must be disabled')
    for name in devices:
        p = sysdev(name)
        check((p / 'dev').read_text().strip() not in mounts, 'An eMMC device is mounted: ' + name)
        check(not list((p / 'holders').iterdir()), 'An eMMC device has holders: ' + name)
    if protect:
        for name in devices:
            subprocess.run(['blockdev', '--setro', dev(name)], check=True)
    for name in devices:
        check(run('blockdev', '--getro', dev(name)) == '1', 'eMMC is not read-only: ' + name)
    return main, devices


def inventory():
    subprocess.run(['/bin/busybox', 'mdev', '-s'], check=True)
    main, devices = guard()
    info = json.loads(run('lsblk', '--json', '-b', '-o', 'NAME,PATH,SIZE,MODEL,SERIAL,TRAN,FSTYPE,UUID,MOUNTPOINTS,RO'))
    info['rescue_root'] = rescue_root()
    info['emmc'] = main
    info['protected_devices'] = devices
    print(json.dumps(info, indent=2))


def mount_boot():
    guard()
    disk = parent(rescue_root())
    names = [p.name for p in Path('/sys/class/block').iterdir()
             if (p / 'partition').exists() and parent(p.name) == disk
             and (p / 'partition').read_text().strip() == '1']
    check(len(names) == 1, 'Cannot identify USB boot partition')
    subprocess.run(['mount', '-t', 'vfat', '-o', 'ro,nosuid,nodev,noexec', dev(names[0]), '/boot'], check=True)


def mount_backup(device, expected_uuid):
    subprocess.run(['/bin/busybox', 'mdev', '-s'], check=True)
    guard()
    name = Path(device).resolve().name
    check(usb(name) and parent(name) != parent(rescue_root()), 'Backup must be on another USB disk')
    check((sysdev(name) / 'partition').exists(), 'Select the NTFS partition, not the whole disk')
    check((sysdev(name) / 'dev').read_text().strip() not in mounted_numbers(), 'Destination is already mounted')
    check(run('blkid', '-s', 'TYPE', '-o', 'value', dev(name)) == 'ntfs', 'Destination must be NTFS')
    check(run('blkid', '-s', 'UUID', '-o', 'value', dev(name)).lower() == expected_uuid.lower(), 'Destination UUID mismatch')
    target = Path('/run/backup')
    target.mkdir(exist_ok=True)
    subprocess.run(['ntfs-3g', '-o', 'rw,norecover,nosuid,nodev,noexec', dev(name), str(target)], check=True)
    Path('/run/backup-device').write_text(name)
    print('Mounted NTFS destination without repair or hibernation removal. Run inventory and review before backup.')


def copy_range(source, destination, offset, length):
    digest = hashlib.sha256()
    with open(source, 'rb', buffering=0) as src, destination.open('xb') as dst:
        src.seek(offset)
        left = length
        while left:
            data = src.read(min(left, 4 * 1024 * 1024))
            check(bool(data), 'Unexpected end of source')
            digest.update(data)
            dst.write(data)
            left -= len(data)
        dst.flush()
        os.fsync(dst.fileno())
    readback = hashlib.sha256()
    with destination.open('rb') as saved:
        while chunk := saved.read(4 * 1024 * 1024):
            readback.update(chunk)
    check(readback.digest() == digest.digest(), 'Source stream / destination readback mismatch')
    return digest.hexdigest()


def ext4_used(device):
    check(run('blkid', '-s', 'TYPE', '-o', 'value', device) == 'ext4', 'Expected ext4: ' + device)
    text = run('dumpe2fs', '-h', device)
    values = dict(line.split(':', 1) for line in text.splitlines() if ':' in line)
    check(values['Filesystem state'].strip() == 'clean', 'Unclean filesystem; stop, do not repair the source: ' + device)
    features = set(values.get('Filesystem features', '').split())
    check('has_journal' in features, 'Expected journaled ext4; manual review required: ' + device)
    check(not features.intersection({'needs_recovery', 'orphan_present'}),
          'Pending journal/orphan recovery; stop, do not repair the source: ' + device)
    # -n opens read-only and refuses repairs; -f checks even a nominally clean filesystem.
    # Any nonzero status (including errors left uncorrected) aborts before cloning.
    subprocess.run(['e2fsck', '-f', '-n', device], check=True)
    return (int(values['Block count']) - int(values['Free blocks'])) * int(values['Block size'])


def layout(main, size):
    table = json.loads(run('sfdisk', '--json', dev(main)))['partitiontable']
    check(table['label'] == 'dos' and table['unit'] == 'sectors' and table.get('sectorsize', 512) == 512,
          'Unsupported partition table; manual review required')
    parts = table['partitions']
    check(len(parts) == 4, 'Expected four partitions')
    end = 0
    gaps = []
    for i, p in enumerate(parts, 1):
        check(p['node'] == dev(main) + 'p' + str(i), 'Unexpected partition order')
        start, length = p['start'] * 512, p['size'] * 512
        check(length > 0 and end <= start and start + length <= size, 'Invalid/overlapping partition')
        if start > end:
            gaps.append((end, start - end))
        end = start + length
    if end < size:
        gaps.append((end, size - end))
    check(parts[0]['size'] * 512 <= 256 * 1024**2 and parts[1]['size'] * 512 <= 1024**3,
          'Unexpectedly large boot/system partitions; manual review required')
    return table, parts, gaps


def backup(expected_bytes):
    main, devices = guard()
    size = int(run('blockdev', '--getsize64', dev(main)))
    check(size == expected_bytes, 'Source capacity does not match the reviewed inventory')
    name = Path('/run/backup-device').read_text().strip()
    check(usb(name) and parent(name) != parent(rescue_root()), 'Invalid destination disk')
    mount = json.loads(run('findmnt', '-J', '-M', '/run/backup'))['filesystems'][0]
    check(Path(mount['source']).resolve() == Path(dev(name)).resolve(), 'Destination mount changed')
    check(mount['fstype'] in ('fuseblk', 'fuse.ntfs-3g') and 'rw' in mount['options'].split(','), 'Wrong destination filesystem')
    table, parts, gaps = layout(main, size)
    used = sum(ext4_used(dev(main) + 'p' + str(i)) for i in (3, 4))
    raw_size = sum(p['size'] * 512 for p in parts[:2]) + sum(length for _, length in gaps)
    raw_size += sum(int(run('blockdev', '--getsize64', dev(main + suffix))) for suffix in ('boot0', 'boot1'))
    # Conservatively budget partclone bitmap/metadata, raw areas and 5 GiB free margin.
    required = raw_size + used * 2 + size // 100 + 5 * 1024**3
    check(shutil.disk_usage('/run/backup').free > required, f'Insufficient free space; need at least {required} bytes')
    directory = Path('/run/backup') / ('n1-backup-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    directory.mkdir(mode=0o700, exist_ok=False)
    (directory / 'INCOMPLETE').write_text('Do not use this backup until COMPLETE.json exists and checksums pass.\n')
    probe = os.urandom(1024 * 1024)
    with (directory / 'write-read-test.bin').open('xb') as f:
        f.write(probe)
        f.flush()
        os.fsync(f.fileno())
    check((directory / 'write-read-test.bin').read_bytes() == probe, 'Destination write/read test failed')
    (directory / 'write-read-test.bin').unlink()
    (directory / 'partition-table.json').write_text(json.dumps(table, indent=2))
    (directory / 'partition-table.sfdisk').write_text(run('sfdisk', '--dump', dev(main)) + '\n')
    (directory / 'block-inventory.json').write_text(run('lsblk', '-J', '-b', '-o', 'NAME,PATH,SIZE,MODEL,SERIAL,TRAN,FSTYPE,UUID,MOUNTPOINTS,RO') + '\n')
    manifest = {'format': 1, 'source_bytes': size, 'source': main, 'ranges': [], 'boot_areas': [], 'partclone': []}
    for offset, length in gaps:
        guard()
        filename = f'outside-partitions-{offset}-{length}.bin'
        print('Backing up non-filesystem region:', offset, length, flush=True)
        digest = copy_range(dev(main), directory / filename, offset, length)
        manifest['ranges'].append({'file': filename, 'offset': offset, 'bytes': length, 'source_sha256': digest})
    for i in (1, 2):
        guard()
        filename = f'partition-{i}.raw'
        length = parts[i - 1]['size'] * 512
        digest = copy_range(dev(main) + 'p' + str(i), directory / filename, 0, length)
        manifest['ranges'].append({'file': filename, 'offset': parts[i - 1]['start'] * 512, 'bytes': length, 'source_sha256': digest})
    for suffix in ('boot0', 'boot1'):
        guard()
        length = int(run('blockdev', '--getsize64', dev(main + suffix)))
        filename = suffix + '.bin'
        digest = copy_range(dev(main + suffix), directory / filename, 0, length)
        manifest['boot_areas'].append({'file': filename, 'area': suffix, 'bytes': length, 'source_sha256': digest})
    for i in (3, 4):
        guard()
        filename = f'partition-{i}.partclone'
        print('Backing up used ext4 blocks, partition', i, flush=True)
        subprocess.run(['partclone.ext4', '-c', '-s', dev(main) + 'p' + str(i), '-o', str(directory / filename),
                        '-L', str(directory / f'partclone-{i}.log')], check=True)
        subprocess.run(['partclone.chkimg', '-s', str(directory / filename), '-L', str(directory / f'check-{i}.log')], check=True)
        manifest['partclone'].append({'file': filename, 'partition': i, 'offset': parts[i - 1]['start'] * 512,
                                     'bytes': parts[i - 1]['size'] * 512})
    guard()
    (directory / 'restore-map.json').write_text(json.dumps(manifest, indent=2) + '\n')
    files = sorted(p for p in directory.iterdir() if p.name != 'INCOMPLETE')
    hashes = []
    for p in files:
        digest = hashlib.sha256()
        with p.open('rb') as f:
            while chunk := f.read(4 * 1024 * 1024):
                digest.update(chunk)
        hashes.append(digest.hexdigest() + '  ' + p.name + '\n')
    (directory / 'SHA256SUMS').write_text(''.join(hashes))
    subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=directory, check=True)
    (directory / 'COMPLETE.json').write_text(json.dumps({'checksums': 'passed', 'partclone_checks': 'passed',
        'restore_rehearsal': 'PENDING', 'hardware_recovery': 'PENDING'}) + '\n')
    (directory / 'INCOMPLETE').unlink()
    os.sync()
    print('Backup verified:', directory, '\nRestore rehearsal and hardware recovery validation are still required.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('protect', 'inventory', 'mount-boot'):
        sub.add_parser(cmd)
    p = sub.add_parser('mount-backup')
    p.add_argument('device')
    p.add_argument('--expect-uuid', required=True)
    p = sub.add_parser('backup')
    p.add_argument('--expect-emmc-bytes', required=True, type=int)
    args = parser.parse_args()
    check(os.geteuid() == 0, 'Root required')
    if args.command == 'protect':
        main_device, devices = guard(protect=True)
        print(json.dumps({'emmc': main_device, 'read_only': devices}))
    elif args.command == 'inventory':
        inventory()
    elif args.command == 'mount-boot':
        mount_boot()
    elif args.command == 'mount-backup':
        mount_backup(args.device, args.expect_uuid)
    else:
        backup(args.expect_emmc_bytes)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, KeyError, ValueError) as error:
        sys.exit('STOP: ' + str(error))
