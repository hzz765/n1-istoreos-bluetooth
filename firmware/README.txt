N1 normal Bluetooth firmware candidate — build only

Based on pinned official iStoreOS 24.10.8 source and public configuration.
Adds Bluetooth UART/serdev/Broadcom support and the tested RPi BCM4345C0 HCD.
Keeps official eMMC, web interface, Docker, storage, boot and upgrade behavior.
Does NOT include USB isolation, diagnostic kernel instrumentation, BlueALSA,
automatic pairing, audio recording, private keys or personal configuration.

HFP microphone compatibility remains unresolved. This does not block compilation,
but a future fix may still need kernel/firmware changes, not only user packages.
Userspace packages and kernel packages must be reviewed for the actual build;
never force-install stock OpenWrt kernel modules based on uname alone.

This is NOT an isolated USB image and has NOT been approved for installation.
Official boot scripts include persistent environment operations. Official upgrade
behavior can reset overlay without the correct backup path and changes the disk
signature. Existing application backups are not a complete disk recovery image.
No flash, update, bootloader, DTB replacement or N1 access occurs in this workflow.
A separate installation and rollback review is required before using the image.
