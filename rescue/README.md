# N1 offline backup rescue — USB only

This is a separate rescue image, not an iStoreOS upgrade. Never upload it to a firmware
upgrade page or install it to internal storage. Hardware boot and unplug rollback are
still pending. Build success is not evidence of a working recovery entry point.

The verified Bluetooth build supplies the unchanged Linux 6.6.144 kernel and matching
modules. A minimal Debian ARM64 userspace supplies Partclone, NTFS-3G and disk tools.
Its package source is the Debian snapshot dated 2026-11-01. Package versions are recorded.
The Linux DTB enables eMMC for reading. The custom init skips all distribution services,
mounts temporary runtime directories in RAM, and sets the eMMC main device, partitions
and boot areas software read-only before enabling network access. It does not initialize
an overlay, auto-mount data disks, activate swap or repair filesystems.

The old prebuilt chainloader is no longer distributed. A checksum-pinned U-Boot 2024.10
source build uses the upstream Phicomm N1 DT, with MMC, networking, EFI, automatic distro
boot and persistent environment backends disabled. Its replacement main_loop only tries
USB 0..3, checks the exact root PARTUUID and kernel/DTB sizes and SHA256, and hangs if
anything fails or kernel boot returns. It never executes scripts from USB or opens a CLI.
The inherited watchdog is explicitly stopped before loading. The entry scripts verify
loader size/CRC using basic crc32 + itest (not optional crc32 -v), and loop on failure.
Both native-endian and big-endian CRC storage used by U-Boot versions are supported.
Only documented RAM scratch space just past the bounded loader is written by entry scripts.

Software read-only protection is not a physical write blocker. The original vendor boot
code BEFORE any USB entry script executes is outside this image's control: an unreadable
USB may never enter our script, and the existing firmware may then boot its original OS.
This is not a promise to prevent all original-system writes before takeover. No internal
boot environment or firmware is changed to alter that behavior. Actual takeover must be
verified before calling the device isolated. A hardware hang/reset outside the verified
software path also remains a hardware validation concern.

## Before writing or booting

1. Preserve configuration and application/database backups on independent storage first.
   Do not publish these files or device inventories. Stop databases/containers cleanly
   before the final shutdown. A live raw filesystem image is not an offline backup.
2. Review the actual boot chain. Do not enable multiboot, run saveenv/fw_setenv, alter
   eMMC boot files or guess environment offsets. If no non-writing USB entry can be
   established, stop; do not substitute a firmware upgrade for a temporary boot.
3. Identify the disposable USB by model, capacity and serial number. Writing it erases
   its contents. Keep the NTFS backup disk disconnected while writing the USB.
4. Verify SHA256SUMS, write only the identified USB and compare the written image bytes
   before adding local files. Compare PARTUUID/UUID with all attached disks.
5. Create a dedicated local Ed25519 SSH key. Put ONLY the `.pub` file on the USB FAT
   partition as `rescue-authorized_keys`. Never put its private key on USB or GitHub.
   The image will refuse network startup if this file is missing or malformed.
6. First boot: backup disk disconnected, direct isolated Ethernet to the computer.
   Set the computer to 192.168.77.2/24 with no gateway. Rescue is 192.168.77.1.
   SSH uses the dedicated private key; there is no password login or DHCP server.
   Host keys are generated in RAM at each boot; use a dedicated known_hosts file and
   verify the direct-cable endpoint. Do not disable host-key checking globally.

## First boot gate

Log in and run `n1-rescue inventory`. Retain the output privately for review. It must
show a USB squashfs root mounted read-only and every eMMC device unmounted/read-only.
No backup starts automatically. If the gate fails, the image does not start SSH.
After a successful inventory, shut down normally, remove USB and verify the original
iStoreOS and Docker applications still start. Complete this rollback test before
performing the full backup. Failure to connect is not permission to change boot settings.

## Backup after inventory and rollback review

Boot rescue again. Attach the NTFS disk and run `n1-rescue inventory` again. Review the
model, size and UUID. The following are templates, not commands with guessed devices:

```
n1-rescue mount-backup /dev/REVIEWED_PARTITION --expect-uuid REVIEWED_NTFS_UUID
n1-rescue backup --expect-emmc-bytes REVIEWED_EMMC_BYTE_SIZE
```

The mount command accepts only an NTFS partition on a different USB disk. It refuses
dirty/hibernated volumes rather than repairing them or deleting hibernation data.
The backup command checks source capacity, read-only/unmounted state, filesystem clean
state and pending journal/orphan flags, a forced read-only e2fsck pass, destination identity,
free space and a destination write/read test. Any nonzero e2fsck result stops the backup. It creates
a new directory and never overwrites an existing backup or existing user files.

Backup components: small partitions 1/2 as raw images; ext4 partitions 3/4 as Partclone
used-block images; partition table plus all ranges outside the four partitions; separate
boot0/boot1 images. RPMB is not a normal disk image and is not cloned. This is a backup
for reviewed restoration to this device, not an unconditional replacement-chip clone.
No assumptions are made about boot-environment offsets. No source repair is attempted.

Partclone images are initially uncompressed: the destination estimate reserves twice
reported used ext4 bytes, all raw regions, metadata allowance and 5 GiB free margin.
This avoids needing 128 GB for unused filesystem blocks. The live estimate, not `df`
alone, decides whether the destination has enough space. Any failed operation leaves
INCOMPLETE and must not be treated as a usable backup. Existing backups are not deleted.

Raw copies record the hash of the source read stream and compare it with a destination
reread. Success requires Partclone image checks and a full SHA256 reread. COMPLETE.json explicitly
keeps restore rehearsal and hardware recovery as PENDING. Copy the checksums elsewhere
and verify again on the computer. An additional rehearsal into sparse image files or
spare media is required before claiming restoration verified. Rehearsal does not write
to the original eMMC. Restoration commands are intentionally not automated or shipped.

Use `poweroff` for normal shutdown. Unplug the rescue USB and backup disk only after
shutdown. If a backup fails or a source filesystem is unclean, retain logs and stop.
Do not run fsck repair on the original disk, force a clone, or bypass a protection gate.

## Current limits

Cloud rehearsal uses ordinary synthetic image files, real ARM64 tools and an actual
NTFS-3G mount. It runs the production backup function through a clearly isolated fixture
identity adapter, restores the four partitions and all outside ranges, compares the whole
synthetic disk byte-for-byte, checks boot-area hashes and validates file permissions,
links, xattrs and ACL. Pending-journal/orphan and corruption/interruption cases must fail.
The adapter is not installed into the firmware. These tests do not exercise physical N1
device discovery. Thirty shared C-policy scenarios exercise boot failure behavior. They
are not hardware emulation of U-Boot or its USB controller.

QEMU exercises the ARM64 tools, not N1 Ethernet/USB/boot behavior. The first real
boot, SSH connectivity and unplug rollback need local verification. A working USB backup
environment does not by itself establish recovery after damaged vendor boot firmware.
No claim of guaranteed recovery or zero hardware risk is made.
