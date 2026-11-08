#!/bin/sh
# Only the preformatted, identity-checked third partition of the boot USB is writable.
case "$1" in ''|start) ;; *) exit 0;; esac
. /lib/n1-bt-guard.sh
n1_devices
disk="$(cat /tmp/n1-bt-disk)"
expected="$(cat /etc/n1-bt-overlay-uuid)"
[ "$(blkid -o value -s UUID /dev/${disk}3)" = "$expected" ] || n1_stop overlay-uuid
[ "$(blkid -o value -s TYPE /dev/${disk}3)" = ext4 ] || n1_stop overlay-type
mount -t ext4 -o ro,noload /dev/${disk}3 /overlay || n1_stop overlay-precheck-mount
[ "$(cat /overlay/.n1-bt-identity)" = "$(cat /etc/n1-bt-partuuid)" ] || n1_stop overlay-identity
umount /overlay || n1_stop overlay-precheck-unmount
mount -t ext4 -o rw,noatime /dev/${disk}3 /overlay || n1_stop overlay-mount
mkdir -p /overlay/upper /overlay/work
mount -t overlay -o noatime,lowerdir=/,upperdir=/overlay/upper,workdir=/overlay/work overlayfs:/overlay /mnt || n1_stop overlay-merge
mount --move /proc /mnt/proc || n1_stop move-proc
pivot_root /mnt /mnt/rom || n1_stop pivot
for dir in dev tmp sys overlay; do
    mount --move /rom/$dir /$dir || n1_stop "move-$dir"
done
exit 0
