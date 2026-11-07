#!/usr/bin/env bash
set -euo pipefail
ROOT=$(pwd)
MODE=${1:?probe, baseline or usb-test required}
mkdir -p public-artifacts
exec > >(tee public-artifacts/build.log) 2>&1
snapshot() { date -u; df -h .; df -i .; }
snapshot
if [[ "$MODE" == probe ]]; then exit 0; fi
[[ "$MODE" == baseline || "$MODE" == usb-test ]]
# Watch only this build's process group. Stop before exhausting the disk.
python3 "$ROOT/scripts/space_guard.py" "$ROOT" &
GUARD=$!
trap 'kill "$GUARD" 2>/dev/null || true' EXIT
SOURCE_COMMIT=5fe2cddefc93d59156ca66740ed896ae954d8e8e
git clone --filter=blob:none --no-checkout https://github.com/istoreos/istoreos.git source
cd source
git checkout --detach "$SOURCE_COMMIT"
cp "$ROOT/configs/24.10-config.buildinfo" .config
cp "$ROOT/configs/24.10-feeds.buildinfo" feeds.conf
./scripts/feeds update -a
./scripts/feeds install -a
if [[ "$MODE" == usb-test ]]; then
  git apply --check "$ROOT/patches/01-n1-bluetooth.patch"
  git apply "$ROOT/patches/01-n1-bluetooth.patch"
  git apply --check "$ROOT/patches/02-n1-usb-test-guard.patch"
  git apply "$ROOT/patches/02-n1-usb-test-guard.patch"
  for pkg in kmod-bluetooth bluez-daemon bluez-utils bluez-utils-extra bluez-utils-btmon; do
    ./scripts/config --enable "PACKAGE_$pkg"
  done
  mkdir -p files/lib/firmware/brcm files/etc
  curl --fail --location --retry 3 https://raw.githubusercontent.com/armbian/firmware/5d685ad233b4dfd03a4d025fa0061f6b0f850cb3/BCM4345C0.hcd -o files/lib/firmware/brcm/BCM4345C0.hcd
  echo '8bbf245399e66f68304ba0f9185159dd1d557c48b534f2e7be75e2e0f4cb4a9a  files/lib/firmware/brcm/BCM4345C0.hcd' | sha256sum -c -
  printf 'N1 Bluetooth USB test\n' > files/etc/n1-bt-usb-test
fi
make defconfig
cp .config "$ROOT/public-artifacts/build.config"
git rev-parse HEAD > "$ROOT/public-artifacts/source-commit.txt"
cp feeds.conf "$ROOT/public-artifacts/feeds.lock"
make download -j4
snapshot
make tools/install -j4
make toolchain/install -j4
snapshot
make target/linux/compile -j4 V=s
snapshot
make -j4 V=s
snapshot
python3 "$ROOT/scripts/verify.py" "$MODE" "$ROOT"
