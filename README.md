# N1 iStoreOS Bluetooth USB test

Experimental, pinned-source cloud build for Phicomm N1 on iStoreOS 24.10 / Linux 6.6.144.
Hardware testing and rollback verification are pending. Cloud build success alone does not authorize booting the image.

## Current USB test path

Use the isolated repack workflow and [Bluetooth test instructions](bluetooth/README.md).
It reuses the checksum-pinned matching kernel/modules, rebuilds the restricted loader,
and replaces automatic storage setup with verified USB-only overlay initialization.
The original full build below remains useful for changing kernel inputs; its older
image is not the current reviewed USB test artifact.

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
3. A full offline eMMC backup provides broader recovery coverage, but is not required for the current isolated USB test. Configuration/application backups do not constitute a full-disk recovery image. No offline backup is made as part of the repack workflow.
4. Compare the image's root PARTUUID and boot filesystem UUID from `verification.txt` with every disk attached to the N1. USB builds generate a fresh disk signature and FAT volume ID. This reduces collision risk but does not replace the local comparison; use only one copy of the test USB.
5. Shut down, disconnect business SSDs, and use an isolated network. Use a disposable USB of at least 4 GB, with actual capacity greater than `minimum_usb_capacity_bytes` in the report. Identify it by model, capacity and serial number before writing it. Read back and verify the written image.
6. Use an already verified USB boot entry point. The test boot scripts intentionally do not enable persistent multiboot. If USB boot is unverified, stop and establish a recovery method first.
7. On first boot verify `/etc/n1-bt-usb-test`, `/proc/cmdline`, `mount`, `block info` and `/proc/partitions`. Root and overlay must be on USB. The eMMC controller is disabled in the test DTB.
8. Where a successful rescue USB/unplug rollback is already documented, perform isolation checks and initial Bluetooth checks in the same test session, then shut down, remove USB and confirm the original system and applications. Cold-boot Bluetooth persistence remains a separate optional validation.

## Bluetooth acceptance

Check `dmesg`, `bluetoothctl list`, `bluetoothctl show` and `btmgmt info`. Require successful firmware initialization, a valid stable address, scan/pair/connect/reconnect, three cold boots and Wi-Fi coexistence. Use the kernel serdev driver exclusively for this UART.

## Recovery

For a failed isolated USB test, power down and remove the test USB, then use the previously verified original boot path. If boot settings or storage are damaged, use the verified recovery entry point and locally held backups. Raw restoration requires matching device identity and size and all target partitions unmounted. Obtain device-specific review before any recovery write.

This repository contains only generic build inputs. Backups, secrets, device inventories and Docker data must remain outside it. Git uses an allowlist-style ignore file and Actions uploads named output files only. The image is exclusively for USB testing; eMMC deployment is outside this project stage.

## Limits of the boot guard

The current repack rebuilds a source-pinned USB-only loader with no MMC or persistent
boot-environment backend. Linux additionally disables eMMC and validates USB root and
overlay before services start. The already installed vendor boot code runs first and
remains outside these protections. See the current Bluetooth instructions for checks
and limitations; older full-build boot scripts are not the current test path.

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

After isolation checks pass, during the same USB test session:

```sh
dmesg | grep -iE 'bluetooth|hci|brcm|firmware|uart'
bluetoothctl list
bluetoothctl show
btmgmt info
```

Use `bluetoothctl` interactively to power on and scan, then pair only with a device you own. Stop on firmware-loading errors, all-zero controller addresses or repeated HCI timeouts. Do not run `hciattach` alongside the serdev driver.

### Compiler cache

Later runs restore only `source/.ccache` from GitHub Actions cache, capped at 3 GiB by `CCACHE_MAXSIZE`. The cache key includes runner OS/architecture, Ubuntu version, pinned configuration, patches and build script. ccache still checks compiler and input identities; no unsafe sloppiness options are enabled. Successful host-tool compilation saves the cache immediately so a later firmware failure does not discard all LLVM work. A cold cache still requires a full compile; no speedup percentage is promised. Running jobs cannot adopt workflow changes. Neither personal files nor the workspace/home directory are cached.

USB builds also cache up to 3 GiB of public source downloads, keyed to pinned input files. Restored files are SHA256-checked against their saved manifest before entering `source/dl`; normal download rules and source pins remain in effect. The cache is saved only after the download stage succeeds. Final compiler-cache saving also retains work from kernel/package stages, including a failed later stage. GitHub may evict old caches; no paid storage limit is enabled and a cache miss falls back to normal compilation. These optimizations apply to newly started workflow runs, not jobs already running.

The source cache and each of the two compiler-cache snapshots are capped at 3 GiB (at most 9 GiB of file payload per run before cache compression). Older cache entries remain subject to GitHub eviction; no repository storage quota or billing setting is changed.
