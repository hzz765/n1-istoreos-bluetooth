#!/bin/sh
# Read-only on-device checks. This does not change Bluetooth or storage state.
. /lib/n1-bt-guard.sh
n1_stop() { echo "FAIL: $*" >&2; exit 1; }
n1_devices
disk="$(cat /tmp/n1-bt-disk)"
expected="$(cat /etc/n1-bt-partuuid)"
[ "$(blkid -o value -s PARTUUID /dev/${disk}2)" = "$expected" ] || n1_stop root-partuuid
major="$(awk '$5 == "/rom" {v=$3} END {print v}' /proc/self/mountinfo)"
[ "$(readlink -f /sys/dev/block/$major)" = "$(readlink -f /sys/class/block/${disk}2)" ] || n1_stop root-device
awk '$2 == "/" && $3 == "overlay" {ok=1} END {exit !ok}' /proc/mounts || n1_stop root-overlay
awk -v p="/dev/${disk}3" '$1 == p && $2 == "/overlay" && $3 == "ext4" && $4 ~ /(^|,)rw(,|$)/ {ok=1} END {exit !ok}' /proc/mounts || n1_stop overlay-device
[ "$(blkid -o value -s UUID /dev/${disk}3)" = "$(cat /etc/n1-bt-overlay-uuid)" ] || n1_stop overlay-uuid
[ "$(cat /overlay/.n1-bt-identity)" = "$expected" ] || n1_stop overlay-identity
[ "$(tr -d '\000' </sys/firmware/devicetree/base/soc/apb@d0000000/mmc@74000/status)" = disabled ] || n1_stop emmc-status
[ "$(wc -l </proc/swaps)" -eq 1 ] || n1_stop swap-present
printf 'PASS: USB root %s2, USB overlay %s3; eMMC absent; no swap\n' "$disk" "$disk"
cat /etc/n1-bt-usb-test
cat /proc/cmdline
cat /proc/partitions
cat /proc/mounts
