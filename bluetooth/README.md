# Isolated N1 Bluetooth USB test

This image is exclusively a temporary USB test, never an iStoreOS upgrade or an
internal-storage installer. It is not the offline-backup rescue image.

## What is rebuilt

The pinned `n1-usb-test-42432191825` artifact supplies the matching Linux 6.6.144
kernel, modules, Linux device tree and BCM4345C0 firmware. Their checksums are
verified; no modules from another OpenWrt build are installed. The root filesystem
is repackaged with a bounded USB overlay startup and fixed isolated networking.
U-Boot v2024.10 is rebuilt from pinned source with MMC, networking, persistent
boot environments and fallback boot disabled. The legacy entry uses exact-length
reads and CRC checks, including the tested Amlogic 2015 short-read compatibility fix.
A firmware rebuild is unnecessary when these kernel/module inputs do not change.

The Linux device tree disables the eMMC controller, while leaving UART Bluetooth
and Wi-Fi SDIO available. An early startup check refuses to proceed unless root is
USB partition 2, the expected PARTUUID matches, and no other storage block device
is visible. Only the same USB's preformatted third partition may become writable
storage. Its identity is checked read-only without journal replay before mounting
it writable. No automatic formatting, repair, resize, extroot, swap or disk discovery
is used. Bluetooth pairing state and SSH host keys persist on this USB overlay.

The root filesystem does not start the normal iStoreOS web interface or Docker.
SSH is public-key-only, with a local Ed25519 public key supplied on the FAT partition
as `rescue-authorized_keys`. No personal keys are distributed in GitHub artifacts.

These checks are software protections, not a physical write blocker. The installed
vendor boot code runs before the USB script and is outside this image's control.
If it does not enter the USB script, the original system may start normally. Do not
interpret that as a successful isolated test. No persistent boot settings should
be modified to force USB startup.

## Build and review

Run Actions → **N1 isolated Bluetooth repack**. Download only a successful run's
`n1-bluetooth-<run id>` artifact, verify `SHA256SUMS`, and inspect the verification
report. Tests exercise legacy file-read errors and storage guard failure paths;
they do not emulate the N1 hardware. Cloud success does not prove radio operation.
An independent review of the actual downloaded artifact precedes USB preparation.

## Prepare without interrupting the N1

1. Check existing system configuration and application backups are readable and
   include the current application data and container definitions. Keep them private.
   An application backup and firmware installer do not recreate every internal
   partition or the boot environment. A full offline image is optional for this
   isolated test, not claimed to exist or to be replaceable by these backups.
2. Retain the known-good rescue artifact, its local SSH key and previous USB
   verification report on the computer before reusing the single test USB.
3. Identify the disposable USB by model, capacity and serial number. Disconnect
   other external storage while writing. Write only this USB and read it back.
   Verify all boot files, the root and overlay partitions, and partition identifiers.
4. Add only the dedicated Ed25519 **public** key to the FAT partition; retain the
   private key on the computer used for SSH. Safely eject the USB after verification.
5. Keep Wi-Fi connected on the computer if Internet is needed. Set only the direct
   Ethernet adapter to 192.168.77.2/24, without a gateway or DNS server. Do not enable
   Internet sharing or bridging. Connect Ethernet directly to N1, and HDMI to a TV.

## One planned test session

The rescue USB entry and return to the original system must already have been
verified for this N1. There is no need to repeat the rescue boot before every test.
The Bluetooth image itself still needs its own isolation checks.

1. Cleanly stop active business applications and shut down the original system.
   Insert only the prepared Bluetooth USB and power on. Do not attach the backup
   hard disk. Record HDMI stage text if startup stalls.
2. SSH to 192.168.77.1 using the dedicated key. First run `n1-bt-check` and retain
   its output privately. Require USB root and writable overlay, no internal eMMC
   block devices, no swap, and the expected image identity. Stop if any check fails.
3. In the same boot, collect `dmesg`, `bluetoothctl list`, `bluetoothctl show` and
   `btmgmt info`. Require successful HCD loading and a non-zero controller address.
   Do not run `hciattach` alongside the kernel serdev driver.
4. Use `bluetoothctl` to power on and scan. Pair only a device you own that is not
   needed by the running production system. Test connect, disconnect and reconnect.
   Changing a peripheral's pairing may affect that peripheral even though internal
   N1 storage is isolated. Do not reset or remove existing peripheral pairings.
5. Shut down normally, remove the USB, and power on the original system. Check
   iStoreOS, Home Assistant and devices. Report this result together with test logs.

This is one test boot and one return boot in one maintenance window. It can validate
isolation, firmware initialization, scanning and same-session pairing/reconnection.
It cannot prove reconnect after a cold boot or multi-boot stability; those checks
require additional boots only if the user later chooses them. Wi-Fi coexistence
also requires a separately agreed test network; it is not established by scanning
Bluetooth alone. Integration into the production Home Assistant is outside this test.

If startup hangs, capture the screen and wait for guidance rather than retrying or
changing boot settings. If a running test reports failure, shut it down normally.
For a hung test, power off before removing USB. Use the already verified original
boot path. Unexpected failure of the original boot requires diagnosis; do not flash
an installer or restore raw sectors speculatively.
