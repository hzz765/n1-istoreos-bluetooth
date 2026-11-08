#!/usr/bin/env bash
# Runs only on the disposable Actions runner. No user disk is an input.
set -euo pipefail
ROOT="$PWD/rescue-work/root"
SNAPSHOT=https://snapshot.debian.org/archive/debian/20261101T000000Z/
mkdir -p "$ROOT"
# Ubuntu 22.04's Debian keyring predates bookworm. Import only the official
# release key after checking its full published fingerprint; never skip signatures.
export GNUPGHOME="$PWD/rescue-work/gnupg"
mkdir -m 700 "$GNUPGHOME"
curl --fail --location --retry 3 --max-time 120 https://ftp-master.debian.org/keys/release-12.asc -o rescue-work/release-12.asc
FINGERPRINT=$(gpg --batch --show-keys --with-colons rescue-work/release-12.asc | awk -F: '$1=="fpr" { print $10; exit }')
[[ "$FINGERPRINT" == 4D64FEC119C2029067D6E791F8D2585B8783D481 ]]
gpg --batch --dearmor -o rescue-work/bookworm.gpg rescue-work/release-12.asc
debootstrap --keyring="$PWD/rescue-work/bookworm.gpg" --arch=arm64 --foreign --variant=minbase --include=busybox-static,partclone,ntfs-3g,dropbear-bin,kmod,util-linux,fdisk,e2fsprogs,python3,iproute2,gzip,zstd bookworm "$ROOT" "$SNAPSHOT"
cp /usr/bin/qemu-aarch64-static "$ROOT/usr/bin/"
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d"
chmod 755 "$ROOT/usr/sbin/policy-rc.d"
chroot "$ROOT" /debootstrap/debootstrap --second-stage
chroot "$ROOT" dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > rescue-work/packages.txt
cp -a rescue-work/original-root/lib/modules "$ROOT/lib/"
cp -a rescue-work/original-root/lib/firmware "$ROOT/lib/"
chroot "$ROOT" depmod -a 6.6.144
install -m 755 rescue/init.sh "$ROOT/sbin/n1-rescue-init"
install -m 755 rescue/guard.py "$ROOT/sbin/n1-rescue"
mkdir -p "$ROOT/boot" "$ROOT/run" "$ROOT/root" "$ROOT/var" "$ROOT/tmp" "$ROOT/proc" "$ROOT/sys" "$ROOT/dev"
printf 'N1 offline read-only backup rescue v1\n' > "$ROOT/etc/n1-rescue"
printf '::shutdown:/bin/umount /run/backup\n::shutdown:/bin/sync\n' > "$ROOT/etc/inittab"
# Root has no password credential; Dropbear permits ONLY key authentication (-s).
# Keys are supplied privately on the USB boot partition; none are built in.
sed -i 's/^root:[^:]*:/root::/' "$ROOT/etc/shadow"
sed -i 's|^root:\([^:]*\):0:0:[^:]*:[^:]*:.*|root:\1:0:0:Rescue:/root:/bin/sh|' "$ROOT/etc/passwd"
rm -f "$ROOT/etc/machine-id" "$ROOT/var/lib/dbus/machine-id"
find "$ROOT/etc" -type f \( -name 'ssh_host_*' -o -name 'dropbear_*_host_key' \) -delete
rm -rf "$ROOT/var/cache/apt/archives" "$ROOT/var/lib/apt/lists" "$ROOT/debootstrap"
# Kernel lacks devtmpfs: provide the early console; init populates /dev with mdev.
test -e "$ROOT/dev/console" || mknod -m 600 "$ROOT/dev/console" c 5 1
test -e "$ROOT/dev/null" || mknod -m 666 "$ROOT/dev/null" c 1 3
chroot "$ROOT" /bin/busybox --list | grep -qx mdev
chroot "$ROOT" /bin/busybox --list | grep -qx init
chroot "$ROOT" /bin/sh -ec 'partclone.ext4 --version; partclone.chkimg --version; ntfs-3g --version'
chroot "$ROOT" /usr/bin/python3 -m py_compile /sbin/n1-rescue
printf '#!/bin/busybox sh\nexec /bin/busybox poweroff "$@"\n' > "$ROOT/sbin/poweroff"
chmod 755 "$ROOT/sbin/poweroff"
rm -f "$ROOT/usr/bin/qemu-aarch64-static"
echo 'Rescue tools executed successfully under ARM64 emulation.'
