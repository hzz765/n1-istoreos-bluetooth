"""Configure and inspect the source-built volatile USB-only chainloader."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys

ROOT = Path('rescue-work/uboot')
BOOT = Path('rescue-work/boot')

if len(sys.argv) == 1:
    kconfig = ROOT / 'arch/arm/mach-meson/Kconfig'
    text = kconfig.read_text()
    assert text.count('\tselect MMC_PWRSEQ\n') == 1
    kconfig.write_text(text.replace('\tselect MMC_PWRSEQ\n', ''))
    header = ROOT / 'include/configs/meson64.h'
    text = header.read_text()
    text = text[:text.index('/* ROM USB boot support')]
    text += '#define CFG_EXTRA_ENV_SETTINGS "stdin=serial\\0stdout=serial,vidconsole\\0stderr=serial,vidconsole\\0"\n#endif\n'
    header.write_text(text)
    # The generic GXL board otherwise reads MAC/serial and calls networking helpers.
    (ROOT / 'board/amlogic/p212/p212.c').write_text('// SPDX-License-Identifier: GPL-2.0+\n/* N1 rescue: no board-specific persistent storage or networking setup. */\n')
    (ROOT / 'arch/arm/dts/meson-gxl-s905d-phicomm-n1-u-boot.dtsi').write_text(
        '// SPDX-License-Identifier: (GPL-2.0+ OR MIT)\n#include "meson-gxl-u-boot.dtsi"\n'
        '&sd_emmc_a { status = "disabled"; };\n&sd_emmc_b { status = "disabled"; };\n&sd_emmc_c { status = "disabled"; };\n'
        '&ethmac { status = "disabled"; };\n&usb { status = "okay"; dr_mode = "host"; };\n')
    shutil.copyfile('rescue/uboot_main.c', ROOT / 'common/main.c')
    shutil.copyfile('rescue/boot_policy.h', ROOT / 'common/n1_boot_policy.h')
    signature = Path('rescue-work/signature.txt').read_text().strip()
    lines = ['/* Generated from the exact image inputs. */', '#define N1_ROOT_PARTUUID "' + signature + '-02"']
    profile_file = Path('rescue-work/boot-profile.json')
    profile = json.loads(profile_file.read_text()) if profile_file.exists() else {'mode': 'rescue'}
    assert profile['mode'] in ('rescue', 'bluetooth')
    bootargs = ('console=ttyAML0,115200n8 console=tty1 no_console_suspend root=PARTUUID='
                + signature + '-02 ' + ('rw rootwait panic=0' if profile['mode'] == 'bluetooth'
                                        else 'ro rootwait panic=0 init=/sbin/n1-rescue-init'))
    lines.append('#define N1_BOOTARGS ' + json.dumps(bootargs))
    for name, macro, array, limit in [('kernel.img', 'N1_KERNEL_SIZE', 'n1_kernel_sha', 64*1024**2),
                                    ('amlogic.dtb', 'N1_DTB_SIZE', 'n1_dtb_sha', 128*1024)]:
        data = (BOOT / name).read_bytes()
        assert 0 < len(data) < limit
        lines += [f'#define {macro} {len(data)}UL',
                  'static const unsigned char ' + array + '[32] = {' + ','.join(str(b) for b in hashlib.sha256(data).digest()) + '};']
    (ROOT / 'common/n1_boot_inputs.h').write_text('\n'.join(lines) + '\n')
else:
    text = (ROOT / '.config').read_text()
    config = dict(line.split('=', 1) for line in text.splitlines() if line.startswith('CONFIG_') and '=' in line)
    disabled = ['MMC', 'NET', 'NETDEVICES', 'CMD_MMC', 'CMD_NET', 'EFI_LOADER', 'DISTRO_DEFAULTS', 'BOOTSTD',
                'BOOT_RETRY', 'BOOTCOUNT_LIMIT', 'USE_PREBOOT', 'CMD_SOURCE', 'CMD_SAVEENV', 'FAT_WRITE',
                'CMD_EXT4_WRITE', 'MTD', 'SPI_FLASH', 'SCSI', 'NVME', 'USB_GADGET', 'FASTBOOT', 'DFU',
                'UPDATE_TFTP', 'WATCHDOG_AUTOSTART']
    for name in disabled:
        assert config.get('CONFIG_' + name) != 'y', 'Unsafe loader config enabled: ' + name
    env_backends = {k for k,v in config.items() if k.startswith('CONFIG_ENV_IS_') and v == 'y'}
    # ENV_IS_DEFAULT is the upstream helper selecting NOWHERE when no backend exists.
    assert env_backends <= {'CONFIG_ENV_IS_NOWHERE', 'CONFIG_ENV_IS_DEFAULT'}, env_backends
    assert 'CONFIG_ENV_IS_NOWHERE' in env_backends
    assert config['CONFIG_DEFAULT_DEVICE_TREE'] == '"amlogic/meson-gxl-s905d-phicomm-n1"'
    assert config['CONFIG_WDT_MESON_GXBB'] == 'y'
    for option in ['VIDEO', 'VIDEO_MESON', 'VIDEO_DT_SIMPLEFB', 'CONSOLE_MUX', 'SYS_CONSOLE_IS_IN_ENV', 'POWER_DOMAIN', 'MESON_GX_VPU_POWER_DOMAIN']:
        assert config.get('CONFIG_' + option) == 'y', 'Missing diagnostic console option: ' + option
    symbols = subprocess.check_output(['aarch64-linux-gnu-nm', str(ROOT / 'u-boot')], text=True)
    names = {line.split()[-1] for line in symbols.splitlines() if line.split()}
    for name in ['mmc_init', 'mmc_bwrite', 'net_loop', 'env_mmc_load', 'env_mmc_save', 'efi_bootmgr_load']:
        assert name not in names, 'Forbidden implementation linked: ' + name
    assert 'main_loop' in names and 'sha256_csum_wd' in names
    dtb = ROOT / 'u-boot.dtb'
    assert subprocess.check_output(['fdtget','-t','s',str(dtb),'/', 'model'], text=True).strip() == 'Phicomm N1'
    binary = (ROOT / 'u-boot.bin').read_bytes()
    profile_file = Path('rescue-work/boot-profile.json')
    profile = json.loads(profile_file.read_text()) if profile_file.exists() else {'mode': 'rescue'}
    signature = Path('rescue-work/signature.txt').read_text().strip()
    expected_args = ('console=ttyAML0,115200n8 console=tty1 no_console_suspend root=PARTUUID=' + signature + '-02 ' + ('rw rootwait panic=0' if profile['mode'] == 'bluetooth' else 'ro rootwait panic=0 init=/sbin/n1-rescue-init'))
    assert expected_args.encode() + b'\0' in binary, 'Packed boot arguments differ'
    if profile['mode'] == 'bluetooth':
        assert b'init=/sbin/n1-rescue-init' not in binary

    for marker in [b'stdout=serial,vidconsole\0', b'stderr=serial,vidconsole\0', b'N1 stage A:', b'N1 stage B:', b'N1 stage C:', b'N1 stage D:', b'N1 stage E:']:
        assert marker in binary, 'Missing packed diagnostic marker: ' + repr(marker)
    report = {'profile': profile['mode'], 'bootargs': expected_args, 'diagnostics': 'HDMI + serial configured; stages A-E present; actual TV output pending hardware',
              'source': 'https://github.com/u-boot/u-boot/tree/v2024.10',
              'archive_sha256': '6c99df5f9402d05b5a5cfc60f941f0a584d0d9355fce46261bef008487a0f6c4',
              'loader_sha256': hashlib.sha256((ROOT / 'u-boot.bin').read_bytes()).hexdigest(),
              'environment': 'NOWHERE (RAM defaults only)', 'disabled': disabled,
              'policy': 'USB 0..3; exact PARTUUID, file sizes and SHA256; return/failure -> hang; no CLI or fallback',
              'hardware_boot': 'PENDING', 'original_vendor_boot_before_script_entry': 'NOT CONTROLLED BY THIS IMAGE'}
    Path('rescue-work/loader-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
