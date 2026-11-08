#!/usr/bin/env python3
"""CI-only backup/restore exercise. All source and destination media are REGULAR FILES.

The production backup function is exercised with a fixture identity adapter because
the cloud runner is not an N1 and has no eMMC. ARM64 tools and NTFS-3G are real.
No fixture bypass is installed in the rescue image. Hardware identity remains untested.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import time
from unittest.mock import patch

assert os.environ.get('GITHUB_ACTIONS') == 'true' and os.geteuid() == 0, 'Disposable Linux CI runner only'
ROOT = Path(sys.argv[1]).resolve()
assert ROOT == Path.cwd() / 'rescue-work/root'
TEST = ROOT / 'test'
TEST.mkdir(exist_ok=False)
assert not Path('/run/backup-device').exists()
assert not os.path.ismount('/run/backup')
Path('/run/backup').mkdir(exist_ok=True)
(ROOT / 'run/backup').mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('guard', 'rescue/guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while data := f.read(4 * 1024 * 1024):
            h.update(data)
    return h.hexdigest()


def run(*cmd):
    return subprocess.check_output([str(c) for c in cmd], text=True).strip()


def arm(tool, *args):
    mapped = [str(a).removeprefix(str(ROOT)) if str(a).startswith(str(ROOT) + '/') else str(a) for a in args]
    return run('chroot', ROOT, '/bin/sh', '-ec', 'exec "$@"', 'sh', tool, *mapped)


def file(path, size):
    with path.open('xb') as f:
        f.truncate(size)


report = {'scope': 'regular-file fixtures; real ARM64 tools and NTFS-3G; mocked device identity only',
          'hardware_identity_and_boot': 'PENDING', 'checks': []}
mounted = []
original_path = os.environ['PATH']
try:
    subprocess.run(['mount', '-t', 'proc', '-o', 'ro,nosuid,nodev,noexec', 'proc', str(ROOT / 'proc')], check=True)
    mounted.append(ROOT / 'proc')
    subprocess.run(['modprobe', 'fuse'], check=True)
    fuse = ROOT / 'dev/fuse'
    if not fuse.exists():
        os.mknod(fuse, stat.S_IFCHR | 0o600, os.makedev(10, 229))
    dest = TEST / 'backup.ntfs'
    file(dest, 8 * 1024**3)
    arm('mkntfs', '-F', '-Q', '-L', 'CI-ONLY', dest)
    arm('ntfs-3g', '-o', 'rw,norecover,nosuid,nodev,noexec', dest, '/run/backup')
    mounted.append(ROOT / 'run/backup')
    subprocess.run(['mount', '--bind', str(ROOT / 'run/backup'), '/run/backup'], check=True)
    mounted.append(Path('/run/backup'))
    # NTFS-3G uses plain fuse for a regular-file fixture; physical block devices use fuseblk.
    fixture_fstype = run('findmnt', '-n', '-o', 'FSTYPE', '-M', '/run/backup')
    assert fixture_fstype in ('fuse', 'fuseblk', 'fuse.ntfs-3g'), repr(fixture_fstype)
    report['ntfs_fixture_mount_type'] = fixture_fstype
    report['checks'].append('actual ARM64 NTFS-3G mounted a regular-file NTFS image with norecover')

    payload = TEST / 'payload'
    payload.mkdir()
    content = payload / 'important.bin'
    content.write_bytes(os.urandom(1024 * 1024))
    os.chown(content, 1234, 2345)
    os.chmod(content, 0o640)
    os.setxattr(content, 'user.rescue_demo', b'keep-this-attribute')
    subprocess.run(['setfacl', '-m', 'u:3456:r--', str(content)], check=True)
    os.link(content, payload / 'hardlink.bin')
    os.symlink('important.bin', payload / 'symlink.bin')
    (payload / 'config.txt').write_text('synthetic application data, not user data\n')
    source = TEST / 'emmc.raw'
    parts = []
    cursor = 2048
    paths = {}
    for i, size in enumerate([1024**2, 4 * 1024**2, 64 * 1024**2, 64 * 1024**2], 1):
        path = Path(str(source) + 'p' + str(i))
        paths['fixturep' + str(i)] = path
        if i <= 2:
            path.write_bytes(os.urandom(size))
        else:
            file(path, size)
            arm('mkfs.ext4', '-F', '-q', '-E', 'lazy_itable_init=0,lazy_journal_init=0', '-d', payload, path)
        parts.append({'node': str(path), 'start': cursor, 'size': size // 512, 'type': '83'})
        cursor += size // 512 + 128
    size = (cursor + 2048) * 512
    file(source, size)
    paths['fixture'] = source
    mbr = bytearray(os.urandom(512))
    for i, p in enumerate(parts):
        mbr[446 + i * 16:462 + i * 16] = struct.pack('<B3sB3sII', 0, b'\0'*3, 0x83, b'\0'*3, p['start'], p['size'])
    mbr[510:512] = b'\x55\xaa'
    with source.open('r+b') as f:
        f.write(mbr)
        for p in parts:
            f.seek(p['start'] * 512)
            with Path(p['node']).open('rb') as part:
                shutil.copyfileobj(part, f)
    for name in ('boot0', 'boot1'):
        paths['fixture' + name] = TEST / (name + '.raw')
        paths['fixture' + name].write_bytes(os.urandom(4 * 1024**2))
    before = {name: sha(path) for name, path in paths.items()}
    for path in paths.values():
        path.chmod(0o444)
    paths['destination'] = dest
    Path('/run/backup-device').write_text('destination')
    table = {'label': 'dos', 'unit': 'sectors', 'sectorsize': 512, 'partitions': parts}

    # Execute the actual packaged ARM64 e2fsprogs/Partclone binaries via QEMU.
    wrappers = TEST / 'wrappers'
    wrappers.mkdir()
    for tool in ('e2fsck', 'dumpe2fs', 'partclone.ext4', 'partclone.chkimg', 'partclone.restore'):
        target = wrappers / tool
        target.write_text('#!/usr/bin/python3\nimport os,sys\nroot=' + repr(str(ROOT)) + '\n'
            'args=[a.removeprefix(root) if a.startswith(root+"/") else a for a in sys.argv[1:]]\n'
            'os.execv("/usr/sbin/chroot",["chroot",root,"/bin/sh","-ec",\'exec "$@"\',"sh",' + repr(tool) + ']+args)\n')
        target.chmod(0o755)
    os.environ['PATH'] = str(wrappers) + ':' + original_path
    real_run = guard.run

    def fixture_guard(protect=False):
        assert all(stat.S_ISREG(path.stat().st_mode) for name, path in paths.items())
        assert all(sha(paths[name]) == digest for name, digest in before.items()), 'Fixture source changed'
        return 'fixture', list(before)

    def fixture_run(*args):
        if args[:2] == ('blockdev', '--getsize64'):
            return str(Path(args[2]).stat().st_size)
        if args[:2] == ('sfdisk', '--json'):
            return json.dumps({'partitiontable': table})
        if args[:2] == ('sfdisk', '--dump'):
            return 'label: dos\n# synthetic fixture; see partition-table.json\n'
        if args[0] == 'lsblk':
            return json.dumps({'fixture_only': True})
        if args[0] == 'findmnt':
            return json.dumps({'filesystems': [{'source': str(dest), 'fstype': 'fuseblk', 'options': 'rw'}]})
        return real_run(*args)

    adapters = [patch.object(guard, 'guard', fixture_guard), patch.object(guard, 'dev', lambda name: str(paths[name])),
                patch.object(guard, 'run', fixture_run), patch.object(guard, 'usb', lambda name: name == 'destination'),
                patch.object(guard, 'parent', lambda name: name), patch.object(guard, 'rescue_root', lambda: 'fixture-root')]
    for adapter in adapters:
        adapter.start()
    try:
        guard.backup(size)
        completed = list(Path('/run/backup').glob('n1-backup-*/COMPLETE.json'))
        assert len(completed) == 1
        backup = completed[0].parent
        mapping = json.loads((backup / 'restore-map.json').read_text())
        restored_disk = TEST / 'restored-emmc.raw'
        file(restored_disk, size)
        for p in mapping['partclone']:
            restored = TEST / ('restored-' + str(p['partition']) + '.raw')
            arm('partclone.restore', '-s', backup / p['file'], '-o', restored, '--restore_raw_file', '-L', '/test/restore.log')
            arm('e2fsck', '-f', '-n', restored)
            original = paths['fixturep' + str(p['partition'])]
            assert sha(restored) == sha(original), 'Synthetic filesystem was not restored byte-for-byte'
            attrs = arm('debugfs', '-R', 'ea_list /important.bin', restored)
            assert 'user.rescue_demo' in attrs and 'system.posix_acl_access' in attrs
            for item in ('important.bin', 'hardlink.bin', 'symlink.bin', 'config.txt'):
                assert arm('debugfs', '-R', 'stat /' + item, original) == arm('debugfs', '-R', 'stat /' + item, restored)
            with restored_disk.open('r+b') as output, restored.open('rb') as input_file:
                output.seek(p['offset'])
                shutil.copyfileobj(input_file, output)
        for region in mapping['ranges']:
            saved = backup / region['file']
            assert sha(saved) == region['source_sha256']
            with restored_disk.open('r+b') as output, saved.open('rb') as input_file:
                output.seek(region['offset'])
                shutil.copyfileobj(input_file, output)
        assert sha(restored_disk) == before['fixture']
        for area in mapping['boot_areas']:
            assert sha(backup / area['file']) == before['fixture' + area['area']] == area['source_sha256']
        report['checks'].append('production backup -> actual ARM64 Partclone restore -> e2fsck -fn -> exact full-disk fixture hash')
        report['checks'].append('file contents, ownership, mode, hardlinks, symlinks, user xattrs and POSIX ACL preserved')
        for feature in ('needs_recovery', 'orphan_present'):
            dirty = TEST / ('dirty-' + feature + '.raw')
            shutil.copyfile(paths['fixturep3'], dirty)
            arm('debugfs', '-w', '-R', 'feature ' + feature, dirty)
            try:
                guard.ext4_used(str(dirty))
            except RuntimeError as e:
                assert 'Pending journal/orphan recovery' in str(e)
            else:
                raise AssertionError('Dirty real ext4 image was accepted')
        report['checks'].append('actual ext4 images with pending journal/orphan flags rejected before cloning')
        corrupted = TEST / 'corrupt.partclone'
        shutil.copyfile(backup / 'partition-3.partclone', corrupted)
        with corrupted.open('r+b') as f:
            f.seek(corrupted.stat().st_size // 2)
            b = f.read(1)
            f.seek(-1, 1)
            f.write(bytes([b[0] ^ 1]))
        try:
            arm('partclone.chkimg', '-s', corrupted, '-L', '/test/corrupt-check.log')
        except subprocess.CalledProcessError:
            report['checks'].append('corrupted Partclone image rejected')
        else:
            raise AssertionError('Corrupted Partclone image passed')
        real_subprocess = subprocess.run
        def fail_clone(cmd, **kwargs):
            if cmd[0] == 'partclone.ext4':
                raise subprocess.CalledProcessError(97, cmd)
            return real_subprocess(cmd, **kwargs)
        time.sleep(1.1)
        with patch.object(guard.subprocess, 'run', fail_clone):
            try:
                guard.backup(size)
            except subprocess.CalledProcessError:
                pass
            else:
                raise AssertionError('Injected clone failure not propagated')
        failed = list(Path('/run/backup').glob('n1-backup-*/INCOMPLETE'))
        assert len(failed) == 1 and not (failed[0].parent / 'COMPLETE.json').exists()
        fixture_guard()
        report['checks'].append('clone failure leaves INCOMPLETE only; all fixture source hashes unchanged')
    finally:
        for adapter in reversed(adapters):
            adapter.stop()
    report['result'] = 'PASS'
    (Path('rescue-work') / 'rehearsal.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
finally:
    os.environ['PATH'] = original_path
    for mount in reversed(mounted):
        subprocess.run(['umount', str(mount)], check=True)
    Path('/run/backup-device').unlink(missing_ok=True)
    shutil.rmtree(TEST)
