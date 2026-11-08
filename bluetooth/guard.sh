# Sourced only by the early preinit hooks and the bounded USB overlay mount script.
n1_stop() {
    echo "N1 BT STOP: $*" >/dev/console
    while :; do sleep 3600; done
}
n1_devices() {
    local path name n disk="$(cat /tmp/n1-bt-disk 2>/dev/null)"
    [ -n "$disk" ] || n1_stop missing-disk
    for path in /sys/class/block/*; do
        name="${path##*/}"
        case "$name" in
            "$disk"|"${disk}1"|"${disk}2"|"${disk}3"|loop*|ram*|zram*) ;;
            *) n1_stop "unexpected-block-device $name" ;;
        esac
    done
    for n in 1 2 3; do
        [ "$(cat /sys/class/block/${disk}$n/partition)" = "$n" ] || n1_stop partition-number
        [ "$(readlink -f /sys/class/block/${disk}$n/..)" = "$(readlink -f /sys/class/block/$disk)" ] || n1_stop partition-parent
    done
}
n1_early() {
    local major part path disk expected
    expected="$(cat /etc/n1-bt-partuuid)"
    case " $(cat /proc/cmdline) " in *" root=PARTUUID=$expected "*) ;; *) n1_stop root-argument;; esac
    major="$(awk '$5 == "/" {v=$3} END {print v}' /proc/self/mountinfo)"
    path="$(readlink -f /sys/dev/block/$major)"
    part="${path##*/}"
    echo "$part" | grep -Eq '^sd[a-z]+2$' || n1_stop root-not-usb-partition2
    [ "$(cat "$path/partition")" = 2 ] || n1_stop root-partition
    case "$path" in */usb*/*) ;; *) n1_stop root-not-usb;; esac
    [ "$(blkid -o value -s PARTUUID /dev/$part)" = "$expected" ] || n1_stop root-partuuid
    disk="$(basename "$(dirname "$path")")"
    printf '%s\n' "$disk" >/tmp/n1-bt-disk
    n1_devices
    [ "$(tr -d '\000' </sys/firmware/devicetree/base/soc/apb@d0000000/mmc@74000/status)" = disabled ] || n1_stop emmc-dtb
    mkdir -p /tmp/n1-bt-boot
    mount -t vfat -o ro "/dev/${disk}1" /tmp/n1-bt-boot || n1_stop boot-mount
    [ "$(cat /tmp/n1-bt-boot/N1-BT-IDENTITY)" = "$expected" ] || n1_stop boot-identity
    [ -f /tmp/n1-bt-boot/rescue-authorized_keys ] || n1_stop missing-public-key
    [ "$(wc -c </tmp/n1-bt-boot/rescue-authorized_keys)" -lt 16384 ] || n1_stop key-size
    awk '{print $1 " " $2}' /tmp/n1-bt-boot/rescue-authorized_keys >/tmp/n1-bt-authorized_keys
    [ "$(wc -l </tmp/n1-bt-authorized_keys)" -eq 1 ] || n1_stop key-count
    grep -Eq '^ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}$' /tmp/n1-bt-authorized_keys || n1_stop key-format
    umount /tmp/n1-bt-boot || n1_stop boot-unmount
    echo 'N1 BT: verified USB root, no internal block storage' >/dev/console
}
n1_overlay() {
    local disk="$(cat /tmp/n1-bt-disk)" source type options
    n1_devices
    source="$(awk '$2 == "/overlay" {print $1}' /proc/mounts)"
    type="$(awk '$2 == "/overlay" {print $3}' /proc/mounts)"
    options="$(awk '$2 == "/overlay" {print $4}' /proc/mounts)"
    [ "$source" = "/dev/${disk}3" ] && [ "$type" = ext4 ] || n1_stop wrong-overlay
    case ",$options," in *,rw,*) ;; *) n1_stop overlay-not-writable;; esac
    awk '$2 == "/" && $3 == "overlay" {ok=1} END {exit !ok}' /proc/mounts || n1_stop missing-overlay-root
    [ "$(cat /overlay/.n1-bt-identity)" = "$(cat /etc/n1-bt-partuuid)" ] || n1_stop overlay-identity
    mkdir -p /etc/dropbear
    cp /tmp/n1-bt-authorized_keys /etc/dropbear/authorized_keys || n1_stop key-install
    chmod 600 /etc/dropbear/authorized_keys
    echo 'N1 BT: USB overlay verified; key-only SSH at 192.168.77.1' >/dev/console
}
