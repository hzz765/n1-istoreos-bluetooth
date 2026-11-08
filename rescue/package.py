"""Package a separate rescue USB using the checksum-pinned, matching kernel/modules.

Only operates on regular files under rescue-work. Never opens a host block device.
"""
import ast
import gzip
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import zlib

WORK = Path('rescue-work')
BASE = Path('base-artifact')
OUT = Path('rescue-output')
BASE_SHA = 'e689f8798f9fa962950fd1dab513f58270c81195e162dba093331a7183721a6b'


def run(*args):
    return subprocess.check_output([str(a) for a in args], text=True).strip()


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while chunk := f.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def script_text(path):
    # Share the existing CRC-checked U-Boot script parser without executing verify.py.
    tree = ast.parse(Path('scripts/verify.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'script_text')
    namespace = {'zlib': zlib}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<script-parser>', 'exec'), namespace)
    return namespace['script_text'](path)


def extract_region(source, target, offset, length):
    with source.open('rb') as src, target.open('wb') as dst:
        src.seek(offset)
        while length:
            chunk = src.read(min(length, 4 * 1024 * 1024))
            assert chunk
            dst.write(chunk)
            length -= len(chunk)


def prepare():
    image = BASE / 'n1-usb-test.img.gz'
    assert checksum(image) == BASE_SHA, 'Base image differs from the verified build'
    assert checksum(BASE / 'kernel.config') == '3185c23daa3b69ce80e16b5d78bf7c6af8befe838d410b3b8185359d12b800cc'
    WORK.mkdir(exist_ok=False)
    signature = secrets.token_hex(4)
    assert signature not in ['00000000', '1234abcd', '4631f166', '52454260']
    (WORK / 'signature.txt').write_text(signature + '\n')
    # Ignore only the known OpenWrt fwtool trailer following the gzip member.
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = WORK / 'base.img'
    with image.open('rb') as src, raw.open('wb') as dst:
        while chunk := src.read(1024 * 1024):
            dst.write(decoder.decompress(chunk))
            if decoder.eof:
                break
        assert decoder.eof
        dst.write(decoder.flush())
    with raw.open('rb') as f:
        mbr = f.read(512)
    assert mbr[510:512] == b'\x55\xaa'
    for i, filename in enumerate(['boot.fat', 'root.squashfs']):
        entry = mbr[446 + i * 16:462 + i * 16]
        start, sectors = struct.unpack('<II', entry[8:16])
        assert start > 0 and sectors > 0
        extract_region(raw, WORK / filename, start * 512, sectors * 512)
    boot = WORK / 'boot'
    boot.mkdir()
    for name in ['kernel.img', 'amlogic.dtb', 'u-boot.emmc', 'boot.scr', 'aml_autoscript', 's905_autoscript', 'emmc_autoscript']:
        subprocess.run(['mcopy', '-i', str(WORK / 'boot.fat'), '::/' + name, str(boot / name)], check=True)
    subprocess.run(['unsquashfs', '-d', str(WORK / 'original-root'), str(WORK / 'root.squashfs'), 'lib/modules', 'lib/firmware'], check=True)
    for name in ['fuse.ko', 'ntfs3.ko']:
        assert len(list((WORK / 'original-root/lib/modules').rglob(name))) == 1, 'Missing ' + name
    for name in ['boot.scr', 'aml_autoscript', 's905_autoscript', 'emmc_autoscript']:
        text = script_text(boot / name)
        assert not any(c in text for c in ['saveenv', 'env save', 'mmc write', 'mmc erase'])
        if name in ['aml_autoscript', 'emmc_autoscript']:
            assert text.strip().endswith('exit') and 'fatload' not in text
    text = script_text(boot / 'boot.scr')
    assert 'for l_devtype in "usb"' in text and text.count(' rw rootwait') == 1
    text = text.replace(' rw rootwait', ' ro rootwait init=/sbin/n1-rescue-init')
    (WORK / 'boot.cmd').write_text(text)
    subprocess.run(['mkimage', '-A', 'arm', '-O', 'linux', '-T', 'script', '-C', 'none', '-a', '0', '-e', '0',
                    '-d', str(WORK / 'boot.cmd'), str(boot / 'boot.scr')], check=True)
    dtb = boot / 'amlogic.dtb'
    node = run('fdtget', '-t', 's', dtb, '/aliases', 'mmc1')
    assert run('fdtget', '-t', 's', dtb, node, 'status') == 'disabled'
    subprocess.run(['fdtput', '-t', 's', str(dtb), node, 'status', 'okay'], check=True)
    # Recovery does not probe Bluetooth; all unused services are bypassed by custom PID1.
    subprocess.run(['fdtput', '-t', 's', str(dtb), '/soc/bus@c1100000/serial@84c0', 'status', 'disabled'], check=True)
    (boot / 'RESCUE-README.txt').write_text('USB rescue only. Never flash via sysupgrade.\n'
        'Before boot, add rescue-authorized_keys containing your LOCAL Ed25519 PUBLIC key.\n'
        'Never copy a private key. No public key is distributed with this image.\n'
        'Direct isolated Ethernet: computer 192.168.77.2/24, N1 192.168.77.1.\n'
        'Hardware boot and rollback gates remain unverified.\n')
    print('Prepared matching kernel/modules and USB-only, non-persistent boot scripts.')


def finish():
    root = WORK / 'root'
    boot = WORK / 'boot'
    assert (root / 'etc/n1-rescue').is_file()
    assert not (root / 'root/.ssh/authorized_keys').exists()
    assert not list(root.rglob('*host_key'))
    assert '::sysinit:' not in (root / 'etc/inittab').read_text()
    loader = boot / 'n1-rescue-loader.bin'
    loader_check = json.loads((WORK / 'loader-verification.json').read_text())
    assert checksum(loader) == loader_check['loader_sha256']
    rehearsal = json.loads((WORK / 'rehearsal.json').read_text())
    assert rehearsal['result'] == 'PASS'
    (boot / 'u-boot.emmc').unlink()  # Do not ship the old unbounded fallback loader.
    assert loader.stat().st_size < 4 * 1024**2
    scratch = 0x01000000 + ((loader.stat().st_size + 4095) // 4096 + 1) * 4096
    crc = zlib.crc32(loader.read_bytes())
    swapped_crc = int.from_bytes(crc.to_bytes(4, 'big'), 'little')
    sentinel = 0
    while sentinel in (crc, swapped_crc):
        sentinel += 1
    entry = ('echo "N1 rescue: USB-only volatile chainload"\n'
             'if usb start; then\n'
             '  for n1_usb in 0 1 2 3; do\n'
             f'    if fatload usb ${{n1_usb}}:1 0x01000000 n1-rescue-loader.bin {loader.stat().st_size + 1:x}; then\n'
             f'      if test "${{filesize}}" = "{loader.stat().st_size:x}"; then\n'
             f'        if mw.l {scratch:x} {sentinel:x} 2; then\n'
             f'          if crc32 0x01000000 ${{filesize}} {scratch:x}; then\n'
             f'            if itest.l *{scratch:x} == {crc:x} || itest.l *{scratch:x} == {swapped_crc:x}; then\n'
             '              go 0x01000000\n'
             '            fi\n'
             '          fi\n'
             '        fi\n'
             '      fi\n'
             '      echo "N1 rescue: loader failed; STOP"\n'
             '      while test 1 = 1; do sleep 1; done\n'
             '    fi\n'
             '  done\n'
             'fi\n'
             'echo "N1 rescue: no loader; STOP"\n'
             'while test 1 = 1; do sleep 1; done\n')
    (WORK / 'entry.cmd').write_text(entry)
    for name in ['boot.scr','aml_autoscript','s905_autoscript','emmc_autoscript']:
        subprocess.run(['mkimage','-A','arm','-O','linux','-T','script','-C','none','-a','0','-e','0',
                        '-d',str(WORK/'entry.cmd'),str(boot/name)],check=True)
        assert script_text(boot/name) == entry
        assert not any(s in entry for s in ['saveenv','env save','mmc ', 'dhcp','tftp','reboot','reset','exit'])
    modules = root / 'lib/modules/6.6.144'
    assert modules.is_dir()
    for path in modules.rglob('*.ko'):
        relative = path.relative_to(root / 'lib/modules')
        assert checksum(path) == checksum(WORK / 'original-root/lib/modules' / relative)
    squash = WORK / 'rescue.squashfs'
    subprocess.run(['mksquashfs', str(root), str(squash), '-noappend', '-comp', 'xz', '-b', '256k', '-no-xattrs', '-all-root'], check=True)
    signature = (WORK / 'signature.txt').read_text().strip()
    fat = WORK / 'rescue.fat'
    subprocess.run(['mkfs.fat', '-F', '16', '-i', signature, '-n', 'N1-RESCUE', '-C', str(fat), '65536'], check=True)
    for path in sorted(boot.iterdir()):
        subprocess.run(['mcopy', '-i', str(fat), str(path), '::/'], check=True)
    start1, size1 = 2048, 64 * 1024 * 1024 // 512
    start2 = start1 + size1
    size2 = ((squash.stat().st_size + 1024 * 1024 - 1) // (1024 * 1024)) * 2048
    disk = WORK / 'n1-rescue.img'
    mbr = bytearray(512)
    mbr[440:444] = bytes.fromhex(signature)[::-1]
    for i, (start, size, typ) in enumerate([(start1, size1, 0x0e), (start2, size2, 0x83)]):
        # Saturated CHS; U-Boot/Linux use LBA fields.
        mbr[446 + 16 * i:462 + 16 * i] = struct.pack('<B3sB3sII', 0, b'\xfe\xff\xff', typ, b'\xfe\xff\xff', start, size)
    mbr[510:512] = b'\x55\xaa'
    with disk.open('wb') as target:
        target.truncate((start2 + size2) * 512)
        target.seek(0)
        target.write(mbr)
        for start, source in [(start1, fat), (start2, squash)]:
            target.seek(start * 512)
            with source.open('rb') as f:
                shutil.copyfileobj(f, target)
    # Read back actual packed partitions and scripts; no loop mount or host block device.
    extract_region(disk, WORK / 'verify.fat', start1 * 512, size1 * 512)
    assert checksum(WORK / 'verify.fat') == checksum(fat)
    extract_region(disk, WORK / 'verify.squashfs', start2 * 512, squash.stat().st_size)
    assert checksum(WORK / 'verify.squashfs') == checksum(squash)
    listing = run('unsquashfs', '-ll', WORK / 'verify.squashfs')
    for name in ['sbin/n1-rescue-init', 'sbin/n1-rescue', 'etc/n1-rescue', 'partclone.ext4', 'partclone.chkimg', 'ntfs-3g']:
        assert name in listing, 'Missing packed file: ' + name
    assert 'rescue-authorized_keys' not in run('mdir', '-i', WORK / 'verify.fat', '::/')
    OUT.mkdir(exist_ok=False)
    with disk.open('rb') as source, (OUT / 'n1-rescue.img.gz').open('wb') as target:
        with gzip.GzipFile(filename='', mode='wb', fileobj=target, compresslevel=6, mtime=0) as compressed:
            shutil.copyfileobj(source, compressed)
    shutil.copyfile(WORK / 'packages.txt', OUT / 'packages.txt')
    for name in ['rehearsal.json','loader.config','loader-verification.json','boot-policy-tests.txt']:
        shutil.copyfile(WORK / name, OUT / name)
    shutil.copyfile('rescue/README.md', OUT / 'README.md')
    report = [
        'BUILD AND STATIC CHECKS PASSED; HARDWARE BOOT/ROLLBACK NOT YET VERIFIED',
        'Base run: 42432191825', 'Base image SHA256: ' + BASE_SHA,
        'Kernel and modules unchanged: Linux 6.6.144; ABI 2e339f86210929e8b3920cf3c91ace89',
        'Userspace: Debian bookworm ARM64 snapshot 20261101T000000Z; see packages.txt',
        'eMMC controller enabled for reads; custom init enforces software RO before network login',
        'No normal distro init, automount, overlay initialization or swap service',
        'Root partition is squashfs; no overlay partition',
        'Source-built U-Boot 2024.10: no MMC/network/persistent environment backend; verified fixed USB boot policy',
        'Entry scripts verify loader CRC and stop on return/failure; original vendor boot before entry remains outside image control',
        'ARM64 tools completed NTFS backup/restore fixtures and failure cases; see rehearsal.json; hardware I/O not tested',
        'Public SSH key must be added locally; no keys or backups are build inputs',
        'root_partuuid=' + signature + '-02',
        'boot_filesystem_uuid=' + signature[:4].upper() + '-' + signature[4:].upper(),
        'minimum_usb_capacity_bytes=' + str(disk.stat().st_size),
        'Do NOT upload this image to a firmware upgrade page. USB boot only.',
    ]
    (OUT / 'verification.txt').write_text('\n'.join(report) + '\n')
    for p in OUT.iterdir():
        assert p.is_file() and not p.is_symlink()
        if p.suffix != '.gz':
            text = p.read_text()
            assert 'PRIVATE KEY-----' not in text and 'github_pat_' not in text
    (OUT / 'SHA256SUMS').write_text(''.join(checksum(p) + '  ' + p.name + '\n' for p in sorted(OUT.iterdir())))
    print('\n'.join(report))


if __name__ == '__main__':
    {'prepare': prepare, 'finish': finish}[sys.argv[1]]()
