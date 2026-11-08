#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT=$PWD
mkdir -p rescue-work/uboot
curl -fsSL --retry 3 --max-time 180 https://codeload.github.com/u-boot/u-boot/tar.gz/refs/tags/v2024.10 -o rescue-work/uboot.tar.gz
echo '6c99df5f9402d05b5a5cfc60f941f0a584d0d9355fce46261bef008487a0f6c4  rescue-work/uboot.tar.gz' | sha256sum -c -
tar -xzf rescue-work/uboot.tar.gz -C rescue-work/uboot --strip-components=1
cc -Wall -Wextra -Werror rescue/test_boot_policy.c -o rescue-work/test-boot-policy
rescue-work/test-boot-policy > rescue-work/boot-policy-tests.txt
python3 rescue/configure_loader.py
cd rescue-work/uboot
export CROSS_COMPILE=aarch64-linux-gnu-
make p212_defconfig
scripts/config --set-str DEFAULT_DEVICE_TREE amlogic/meson-gxl-s905d-phicomm-n1 --set-str IDENT_STRING ' n1-usb-rescue'
for option in MMC MMC_WRITE MMC_PWRSEQ DM_MMC CMD_MMC NET NETDEVICES CMD_NET CMD_DHCP CMD_PXE EFI_LOADER EFI_CAPSULE_ON_DISK DISTRO_DEFAULTS BOOTSTD BOOT_RETRY BOOTCOUNT_LIMIT USE_PREBOOT AUTOBOOT MENU CMD_SOURCE CMD_SAVEENV CMD_NVEDIT_LOAD CMD_NVEDIT_SAVE ENV_IS_IN_MMC ENV_IS_IN_FAT ENV_IS_IN_EXT4 ENV_IS_IN_SPI_FLASH ENV_IS_IN_FLASH ENV_IS_IN_NAND ENV_IS_IN_UBI ENV_IS_IN_EEPROM ENV_IS_IN_REMOTE ENV_IS_IN_SATA CMD_EXT4_WRITE FAT_WRITE CMD_MEMORY CMD_MEMTEST CMD_EEPROM CMD_MTD MTD MTD_RAW_NAND SPI_FLASH CMD_SF CMD_SCSI SCSI AHCI PCI NVME CMD_NVME USB_GADGET FASTBOOT CMD_FASTBOOT DFU CMD_DFU UPDATE_TFTP USB_KEYBOARD MISC_INIT_R WATCHDOG_AUTOSTART; do
    scripts/config --disable "$option"
done
for option in ENV_IS_NOWHERE CMDLINE HUSH_PARSER CMD_USB USB_STORAGE CMD_FAT FS_FAT CMD_PART PARTITION_UUIDS DOS_PARTITION CMD_BOOTI SHA256 WDT WDT_MESON_GXBB PANIC_HANG VIDEO VIDEO_MESON VIDEO_DT_SIMPLEFB SYS_WHITE_ON_BLACK CONSOLE_MUX SYS_CONSOLE_IS_IN_ENV POWER_DOMAIN MESON_GX_VPU_POWER_DOMAIN; do
    scripts/config --enable "$option"
done
make olddefconfig
make -j4
cd "$REPO_ROOT"
python3 rescue/configure_loader.py verify
cp rescue-work/uboot/u-boot.bin rescue-work/boot/n1-rescue-loader.bin
cp rescue-work/uboot/.config rescue-work/loader.config
