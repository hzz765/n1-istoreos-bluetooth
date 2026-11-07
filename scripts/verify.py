import zlib
import hashlib
import pathlib
import shutil
import subprocess
import sys

mode, root_arg = sys.argv[1:]
root = pathlib.Path(root_arg)
source = root / 'source'
out = root / 'public-artifacts'
kernels = list((source / 'build_dir').glob('target-*/linux-amlogic_meson/linux-6.6.144'))
assert len(kernels) == 1, f'Unexpected kernel directories: {kernels}'
kernel = kernels[0]
config = (kernel / '.config').read_text()
shutil.copyfile(kernel / '.config', out / 'kernel.config')
abi = (kernel / '.vermagic').read_text().strip()
report = [f'mode={mode}', f'generated_kernel_abi={abi}']
if mode == 'baseline':
    report.append('Compare this ABI and kernel.config with the locally retained original system inventory.')
else:
    required = ['BT=m', 'BT_HCIUART=m', 'BT_HCIUART_BCM=y', 'BT_HCIUART_SERDEV=y',
                'BT_BCM=m', 'SERIAL_DEV_BUS=y', 'SERIAL_DEV_CTRL_TTYPORT=y']
    for option in required:
        assert f'CONFIG_{option}\n' in config, option
    images = list((source / 'bin/targets/amlogic/meson').glob('*phicomm_n1*sysupgrade.img.gz'))
    assert len(images) == 1, f'Expected one N1 image: {images}'
    image = root / 'test.img'
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    with images[0].open('rb') as src, image.open('wb') as dst:
        while chunk := src.read(1024 * 1024):
            dst.write(decoder.decompress(chunk))
            if decoder.eof:
                break
        assert decoder.eof, 'Truncated gzip image'
        dst.write(decoder.flush())
    with image.open('rb') as f:
        mbr = f.read(512)
    signature = int.from_bytes(mbr[440:444], 'little')
    assert signature != 0, 'Invalid MBR signature'
    report.append(f'root_partuuid={signature:08x}-02; compare against ALL attached disks before boot')
    loop = subprocess.check_output(['sudo', 'losetup', '--find', '--show', '--partscan', str(image)], text=True).strip()
    boot = root / 'inspect-boot'
    fs = root / 'inspect-root'
    boot.mkdir(exist_ok=True)
    mounted = False
    try:
        subprocess.run(['sudo', 'mount', '-o', 'ro', loop + 'p1', str(boot)], check=True)
        mounted = True
        dtb = boot / 'amlogic.dtb'
        dts = subprocess.check_output(['dtc', '-I', 'dtb', '-O', 'dts', str(dtb)], text=True)
        assert 'brcm,bcm43438-bt' in dts
        def getprop(node, prop):
            return subprocess.check_output(['fdtget', '-t', 's', str(dtb), node, prop], text=True).strip()
        emmc = getprop('/aliases', 'mmc1')
        assert getprop(emmc, 'status') == 'disabled', 'eMMC must be disabled'
        for script in ['boot.scr', 'aml_autoscript', 's905_autoscript']:
            data = (boot / script).read_bytes()[64:].decode('utf-8')
            assert 'saveenv' not in data and 'mmc write' not in data, script
            if script == 'boot.scr':
                assert 'for l_devtype in "usb"' in data
        subprocess.run(['sudo', 'unsquashfs', '-d', str(fs), loop + 'p2'], check=True, stdout=subprocess.DEVNULL)
        assert (fs / 'etc/n1-bt-usb-test').is_file()
        hcd = fs / 'lib/firmware/brcm/BCM4345C0.hcd'
        assert hashlib.sha256(hcd.read_bytes()).hexdigest() == '8bbf245399e66f68304ba0f9185159dd1d557c48b534f2e7be75e2e0f4cb4a9a'
        for module in ['bluetooth', 'hci_uart', 'btbcm']:
            assert list((fs / 'lib/modules').rglob(module + '.ko')), module
        for binary in ['bluetoothctl', 'bluetoothd', 'btmgmt']:
            assert (fs / 'usr/bin' / binary).is_file(), binary
        report.append('PASS: kernel options, packed modules, HCD, BlueZ, eMMC-disabled DTB, boot scripts')
    finally:
        if mounted:
            subprocess.run(['sudo', 'umount', str(boot)], check=True)
        subprocess.run(['sudo', 'losetup', '-d', loop], check=True)
    shutil.copyfile(images[0], out / 'n1-usb-test.img.gz')
    report.append('HARDWARE TEST PENDING. Boot-chain, backup, PARTUUID collision and rollback gates require local verification.')
(out / 'verification.txt').write_text('\n'.join(report) + '\n')
files = [p for p in out.iterdir() if p.is_file() and p.name not in ['SHA256SUMS', 'build.log']]
(out / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(files)))
print('\n'.join(report))
