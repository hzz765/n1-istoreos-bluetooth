# N1 iStoreOS Bluetooth USB test

Experimental, pinned-source cloud build for Phicomm N1 on iStoreOS 24.10 / Linux 6.6.144.
Hardware testing and rollback verification are pending. Cloud build success alone does not authorize booting the image.

## Build

Use Actions → N1 isolated USB build → Run workflow. Run `probe`, then `baseline`, inspect its results, then `usb-test`.
The baseline compiles the unmodified kernel and records its configuration/ABI; it does not build a second complete application image. The USB stage builds the full firmware. Each stage has its own Actions step, verbose compiler output and elapsed time. LLVM reports completed/total build edges; a five-minute activity snapshot reports CPU use and the latest LLVM build edge without process arguments. Disk checks stay quiet except for low-space warnings.

The pinned source is a candidate corresponding to the official August 2026 build timestamp. Exact reproduction remains subject to baseline verification.
Feeds and public build configuration are pinned in `configs/`. The Bluetooth firmware candidate comes from Armbian firmware commit `5d685ad233b4dfd03a4d025fa0061f6b0f850cb3` and is SHA256-checked.

The build records space before and after clearing named disposable runner tools. A watchdog terminates the build below 5 GiB remaining. Resource availability is measured per run.

## Download

Open the successful `usb-test` run and download its `n1-usb-test-<run id>` artifact. Extract the ZIP. On macOS run `shasum -a 256 -c SHA256SUMS` in the extracted directory. Inspect `verification.txt` before using `n1-usb-test.img.gz`.

## Required local rollback gates

1. Identify the actual boot chain and a working recovery entry point. Read U-Boot environment only after validating its location/configuration. Keep the original boot files locally.
2. Back up system configuration, application databases, container definitions and stopped Docker storage to independent storage. Verify the backups. Identify every SSD/bind mount separately.
3. Prepare an offline eMMC image including all data partitions and any exposed boot areas from a verified recovery environment. Record device identity and size. Keep this backup outside this repository.
4. Compare the image's root PARTUUID and boot filesystem UUID from `verification.txt` with every disk attached to the N1. USB builds generate a fresh disk signature and FAT volume ID. This reduces collision risk but does not replace the local comparison; use only one copy of the test USB.
5. Shut down, disconnect business SSDs, and use an isolated network. Use a disposable USB of at least 4 GB, with actual capacity greater than `minimum_usb_capacity_bytes` in the report. Identify it by model, capacity and serial number before writing it. Read back and verify the written image.
6. Use an already verified USB boot entry point. The test boot scripts intentionally do not enable persistent multiboot. If USB boot is unverified, stop and establish a recovery method first.
7. On first boot verify `/etc/n1-bt-usb-test`, `/proc/cmdline`, `mount`, `block info` and `/proc/partitions`. Root and overlay must be on USB. The eMMC controller is disabled in the test DTB.
8. Shut down, remove USB, restore original peripherals, then confirm the original system and applications boot correctly. Only after this test should Bluetooth testing proceed.

## Bluetooth acceptance

Check `dmesg`, `bluetoothctl list`, `bluetoothctl show` and `btmgmt info`. Require successful firmware initialization, a valid stable address, scan/pair/connect/reconnect, three cold boots and Wi-Fi coexistence. Use the kernel serdev driver exclusively for this UART.

## Recovery

For a failed isolated USB test, power down and remove the test USB, then use the previously verified original boot path. If boot settings or storage are damaged, use the verified recovery entry point and locally held backups. Raw restoration requires matching device identity and size and all target partitions unmounted. Obtain device-specific review before any recovery write.

This repository contains only generic build inputs. Backups, secrets, device inventories and Docker data must remain outside it. Git uses an allowlist-style ignore file and Actions uploads named output files only. The image is exclusively for USB testing; eMMC deployment is outside this project stage.

## Limits of the boot guard

The test Linux DTB disables the eMMC controller. This takes effect only once that DTB is used by Linux. It does not disable eMMC access by the already installed vendor U-Boot or the official prebuilt chainloaded U-Boot. The latter remains the pinned upstream image dependency; this project does not claim to have rebuilt or fully audited its binary. Therefore an independently verified boot/recovery path remains mandatory.

`aml_autoscript` and `emmc_autoscript` deliberately stop. `s905_autoscript` requires the existing USB entry to supply `usbdev`; it never substitutes MMC when that variable is missing. `boot.scr` searches USB only and checks kernel/DTB loading before booting. These protections may prevent booting on an unverified boot chain. Do not solve this by writing boot environment variables or overwriting eMMC.

## Read-only first-boot checks

Run on the isolated test system, and retain output privately:

```sh
cat /etc/n1-bt-usb-test
cat /proc/cmdline
cat /proc/partitions
mount
block info
ls -l /sys/bus/mmc/devices
ls -l /sys/bus/serial/devices
```

The Wi-Fi SDIO function may still appear under MMC: its presence is not an eMMC failure. Identify the eMMC controller, not merely any name containing `mmc`. If root or overlay is not the verified USB, or eMMC storage is visible, stop the test. Never run an installer, sysupgrade, or boot-environment writer during this phase.

After the successful unplug-and-boot rollback test, on a subsequent USB boot:

```sh
dmesg | grep -iE 'bluetooth|hci|brcm|firmware|uart'
bluetoothctl list
bluetoothctl show
btmgmt info
```

Use `bluetoothctl` interactively to power on and scan, then pair only with a device you own. Stop on firmware-loading errors, all-zero controller addresses or repeated HCI timeouts. Do not run `hciattach` alongside the serdev driver.
