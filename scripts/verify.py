import zlib
import hashlib
import pathlib
import shutil
import subprocess
import sys

def script_text(path):
    """Read a single, uncompressed legacy U-Boot script, including its size table."""
    data = path.read_bytes()
    assert len(data) >= 72 and data[:4] == bytes.fromhex('27051956'), path.name
    header = bytearray(data[:64])
    header_crc = int.from_bytes(header[4:8], 'big')
    header[4:8] = bytes(4)
    assert zlib.crc32(header) == header_crc, f'{path.name}: header CRC'
    assert data[30:32] == bytes([6, 0]), f'{path.name}: expected uncompressed script'
    payload = data[64:]
    assert len(payload) == int.from_bytes(data[12:16], 'big'), f'{path.name}: size'
    assert zlib.crc32(payload) == int.from_bytes(data[24:28], 'big'), f'{path.name}: data CRC'
    length = int.from_bytes(payload[:4], 'big')
    assert payload[4:8] == bytes(4) and length == len(payload) - 8, f'{path.name}: script table'
    return payload[8:].decode('utf-8')


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
    assert f'{signature:08x}' == (root / 'usb-signature.txt').read_text().strip(), 'Wrong USB partition signature'
    report.append(f'root_partuuid={signature:08x}-02; compare against ALL attached disks before boot')
    loop = subprocess.check_output(['sudo', 'losetup', '--find', '--show', '--partscan', str(image)], text=True).strip()
    boot = root / 'inspect-boot'
    fs = root / 'inspect-root'
    boot.mkdir(exist_ok=True)
    mounted = False
    try:
        subprocess.run(['sudo', 'mount', '-o', 'ro', loop + 'p1', str(boot)], check=True)
        mounted = True
        fat_uuid = subprocess.check_output(['sudo', 'blkid', '-s', 'UUID', '-o', 'value', loop + 'p1'], text=True).strip()
        assert fat_uuid.replace('-', '').lower() == f'{signature:08x}', 'Wrong FAT volume ID'
        report.append(f'boot_filesystem_uuid={fat_uuid}')
        dtb = boot / 'amlogic.dtb'
        dts = subprocess.check_output(['dtc', '-I', 'dtb', '-O', 'dts', str(dtb)], text=True)
        assert 'brcm,bcm43438-bt' in dts
        def getprop(node, prop):
            return subprocess.check_output(['fdtget', '-t', 's', str(dtb), node, prop], text=True).strip()
        uart = '/soc/bus@c1100000/serial@84c0'
        assert getprop(uart, 'status') == 'okay'
        assert getprop(uart + '/bluetooth', 'compatible') == 'brcm,bcm43438-bt'
        properties = subprocess.check_output(['fdtget', '-p', str(dtb), uart], text=True).splitlines()
        assert 'uart-has-rtscts' in properties and 'pinctrl-0' in properties
        speed = subprocess.check_output(['fdtget', '-t', 'u', str(dtb), uart + '/bluetooth', 'max-speed'], text=True).strip()
        assert speed == '2000000'
        assert getprop(uart + '/bluetooth', 'clock-names') == 'lpo'
        emmc = getprop('/aliases', 'mmc1')
        assert getprop(emmc, 'status') == 'disabled', 'eMMC must be disabled'
        for script in ['boot.scr', 'aml_autoscript', 's905_autoscript', 'emmc_autoscript']:
            data = script_text(boot / script)
            assert all(command not in data for command in ['saveenv', 'env save', 'mmc write', 'mmc erase']), script
            if script in ['aml_autoscript', 'emmc_autoscript']:
                assert data.strip().endswith('exit') and 'fatload' not in data, script
            if script == 's905_autoscript':
                assert 'setenv devtype \"mmc\"' not in data, script
            if script == 'boot.scr':
                assert 'for l_devtype in "usb"' in data
        subprocess.run(['sudo', 'unsquashfs', '-d', str(fs), loop + 'p2'], check=True, stdout=subprocess.DEVNULL)
        assert (fs / 'etc/n1-bt-usb-test').is_file()
        hcd = fs / 'lib/firmware/brcm/BCM4345C0.hcd'
        assert hashlib.sha256(hcd.read_bytes()).hexdigest() == '8bbf245399e66f68304ba0f9185159dd1d557c48b534f2e7be75e2e0f4cb4a9a'
        modules = {}
        for module in ['bluetooth', 'hci_uart', 'btbcm']:
            matches = list((fs / 'lib/modules').rglob(module + '.ko'))
            assert len(matches) == 1, module
            modules[module] = matches[0]
        releases = {p.parent.name for p in modules.values()}
        assert releases == {'6.6.144'}, releases
        available = {p.stem.replace('-', '_') for p in (fs / 'lib/modules').rglob('*.ko')}
        for name, path in modules.items():
            dependencies = subprocess.check_output(['modinfo', '-F', 'depends', str(path)], text=True).strip()
            missing = {d.replace('-', '_') for d in dependencies.split(',') if d} - available
            assert not missing, f'{name}: unpackaged dependencies {missing}'
            report.append(f'{name}_depends={dependencies}')
        vermagics = {subprocess.check_output(['modinfo', '-F', 'vermagic', str(p)], text=True).strip() for p in modules.values()}
        assert len(vermagics) == 1, vermagics
        report.append(f'module_vermagic={next(iter(vermagics))}')
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
