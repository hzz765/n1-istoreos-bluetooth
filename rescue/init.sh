#!/bin/busybox sh
# PID 1. Never enter the distribution's normal init, hotplug or mount services.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
umask 077
fail() {
    echo "RESCUE STOPPED: $*" >/dev/console
    # No shell, SSH, automount, reboot or storage repair on failed protection.
    while :; do /bin/busybox sleep 3600; done
}
mount -t proc proc /proc || fail proc
mount -t sysfs sysfs /sys || fail sysfs
mount -t tmpfs -o mode=755,nosuid tmpfs /run || fail run
mount -t tmpfs -o mode=755,nosuid tmpfs /dev || fail dev
/bin/busybox mdev -s || fail mdev
mkdir -p /dev/pts /dev/shm
mount -t devpts -o mode=620 devpts /dev/pts || fail devpts
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /tmp || fail tmp
mount -t tmpfs -o mode=755,nosuid,nodev tmpfs /var || fail var
mount -t tmpfs -o mode=700,nosuid,nodev tmpfs /root || fail root
mkdir -p /var/log /var/tmp /run/lock
# Allow bounded asynchronous USB/MMC discovery; never mount anything while waiting.
protected=0
for attempt in $(seq 1 30); do
    /bin/busybox mdev -s || fail mdev
    if /sbin/n1-rescue protect > /run/protection.json 2>/run/protection-error.txt; then
        protected=1
        break
    fi
    sleep 1
done
[ "$protected" = 1 ] || fail protection
# The boot partition is derived from the verified USB root, never an MMC guess.
/sbin/n1-rescue mount-boot || fail boot
mkdir -m 700 /root/.ssh
/usr/bin/python3 - <<'PY' || fail public-key
import base64,pathlib,struct
p=pathlib.Path('/boot/rescue-authorized_keys')
assert p.is_file() and p.stat().st_size < 16384, 'Missing/oversized private USB login configuration'
lines=p.read_text().splitlines()
assert 1 <= len(lines) <= 5
keys=[]
for line in lines:
    fields=line.split()
    assert len(fields)>=2 and fields[0]=='ssh-ed25519'
    raw=base64.b64decode(fields[1],validate=True)
    assert raw[:19]==struct.pack('>I',11)+b'ssh-ed25519'+struct.pack('>I',32) and len(raw)==51
    keys.append('ssh-ed25519 '+fields[1])
pathlib.Path('/root/.ssh/authorized_keys').write_text('\n'.join(keys)+'\n')
PY
chmod 600 /root/.ssh/authorized_keys
# No udev, hotplug service, automounter or automatic filesystem repair.
echo '' > /proc/sys/kernel/hotplug
# Additional disks are enumerated by the inventory command with `busybox mdev -s`.
modprobe fuse || fail fuse
modprobe uas 2>/dev/null || true
ip link set lo up || fail loopback
ip link set eth0 up || fail ethernet
ip address add 192.168.77.1/24 dev eth0 || fail address
dropbearkey -t ed25519 -f /run/rescue-hostkey > /run/hostkey-public.txt 2>&1 || fail hostkey
dropbear -s -g -j -k -r /run/rescue-hostkey -P /run/dropbear.pid -p 192.168.77.1:22 || fail ssh
echo 'N1 rescue ready: direct isolated Ethernet, 192.168.77.1; public-key SSH only.' >/dev/console
exec /bin/busybox init
