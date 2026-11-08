# N1 iStoreOS Bluetooth USB test

Temporary, isolated Bluetooth testing for Phicomm N1. **Never install these test
images through the firmware update page or write them to internal storage.**

## Current build

Use Actions → **N1 isolated Bluetooth USB repack** and follow the complete
[Bluetooth USB instructions](bluetooth/README.md). The workflow rebuilds a
restricted USB-only loader and repackages a guarded root filesystem with its own
preformatted USB overlay. It does not rebuild LLVM or mix kernel modules from
another distribution.

The input is the checksum-pinned full build `42432191825`: Linux 6.6.144, matching
modules, a device tree with UART Bluetooth enabled and eMMC disabled, and pinned
BCM4345C0 HCD firmware. A source-built U-Boot 2024.10 disables MMC, networking,
persistent environments and fallback boot. The legacy entry uses exact-length
reads compatible with the inspected Amlogic 2015 loader.

Build [42483222236](https://github.com/hzz765/n1-istoreos-bluetooth/actions/runs/42483222236)
passed cloud tests and packaging. Download its `n1-bluetooth-42483222236` artifact,
verify `SHA256SUMS`, and inspect the build records. Independent artifact review and
local USB readback are separate gates; cloud success does not prove hardware
Bluetooth operation.

## Local preparation and recovery scope

Keep existing configuration/application backups private and check them before
testing. They are not a full-disk image and do not reproduce every internal
partition. A full offline disk backup is not mandatory for this expressly isolated
USB test. Do not overwrite internal boot files or change persistent boot settings.

Preserve the known-good rescue image locally before reusing a single disposable
USB. Identify that USB by model, capacity and serial number, then read back and
verify it after writing. Add only a locally held Ed25519 **public** key to its FAT
partition. The artifact contains no login keys or device-specific settings.

Where the rescue USB entry and unplug rollback have already been verified for the
N1, isolation checks and initial Bluetooth tests can be performed in one USB boot,
followed by one return boot to the original system. Cold-boot pairing persistence
and Wi-Fi coexistence require their own later tests; do not claim them from a
single session. See the detailed instructions for connections, checks and exit steps.

The installed vendor boot code executes before the USB script and remains outside
the test image's control. If it does not hand over to USB, the original OS may boot.
Software isolation is not a physical write blocker or a guarantee against hardware
failure. Stop if the expected USB identity or isolation checks fail.

## Tests and resource use

The workflow tests 32 guard scenarios, 272 legacy-entry cases, four legacy file-read
checks, and 30 loader policy scenarios. It runs the actual ARM64 mount, blkid and
BusyBox tools under QEMU for command support and syntax checks. These are userspace
and fixture tests, not N1 board emulation. The finished image's partition data and
re-extracted root files are checked before upload.

Resource output appears at stage boundaries. A quiet monitor stops its own build
stage below a 5 GiB reserve. No recurring capacity spam or paid resources are used.
Only explicitly named public artifacts are uploaded. Backups, private keys,
container definitions and device inventories stay outside Git.

## Other workflows

- `build.yml` is the original full source compiler (`probe`, `baseline`, `usb-test`).
  Use it only when changing kernel inputs. Its older artifact is a pinned input to
  the current repack, not the currently reviewed bootable test image.
- `rescue.yml` creates the separate read-only backup rescue system. Unlike the
  Bluetooth image, its Linux DT enables eMMC for read-only backup access. It is not
  a Bluetooth test image or an installer.

Official candidate source, feeds and public build configuration remain pinned in
`configs/` and the build scripts. The candidate source corresponds to the official
August 2026 build timestamp; this does not claim reproduction of the installed
kernel ABI. Never install these newly built modules into an existing kernel merely
because its release string is also 6.6.144.
