#!/usr/bin/env bash
set -euo pipefail
ROOT=$(pwd)
MODE=${1:?probe, baseline or usb-test required}
STAGE=${2:-all}
mkdir -p public-artifacts
exec > >(tee -a public-artifacts/build.log) 2>&1
snapshot() { echo "Stage: ${1:-start}"; date -u; df -h .; df -i .; }
if [[ "$MODE" == probe ]]; then snapshot probe; exit 0; fi
[[ "$MODE" == baseline || "$MODE" == usb-test ]]
case "$STAGE" in all|prepare|download|tools|toolchain|kernel|firmware|verify) ;; *) exit 2;; esac
# Independent of compiler slots: quiet unless the remaining disk is low.
python3 "$ROOT/scripts/space_guard.py" "$ROOT" &
GUARD=$!
python3 "$ROOT/scripts/build_progress.py" "$ROOT/source" &
PROGRESS=$!
trap 'kill "$GUARD" "$PROGRESS" 2>/dev/null || true' EXIT
export NINJA_STATUS='[%f/%t, %es elapsed] '
stage() {
  local name=$1
  shift
  echo "::group::$name"
  local started=$SECONDS
  "$@"
  echo "Stage finished: $name; elapsed=$((SECONDS - started)) seconds"
  snapshot "$name"
  echo '::endgroup::'
}
prepare() {
  printf 'Runner CPUs: '; nproc
  grep -E '^(MemTotal|SwapTotal):' /proc/meminfo
  echo '48ab181277cad611970415212578b97e9e8a2f3314c58330bff9107a8ec22d04  configs/24.10-config.buildinfo' | sha256sum -c -
  echo '6581f340ac1bbca6bacc66acc32ac79ee668cd4c4ca759671659a9e682e858d7  configs/24.10-feeds.buildinfo' | sha256sum -c -
  SOURCE_COMMIT=5fe2cddefc93d59156ca66740ed896ae954d8e8e
  git clone --filter=blob:none --no-checkout https://github.com/istoreos/istoreos.git source
  cd source
  git checkout --detach "$SOURCE_COMMIT"
  cp "$ROOT/configs/24.10-config.buildinfo" .config
  cp "$ROOT/configs/24.10-feeds.buildinfo" feeds.conf
  ./scripts/feeds update -a
  ./scripts/feeds install -a
  if [[ "$MODE" == usb-test ]]; then
    USB_SIGNATURE=$(python3 -c 'import secrets; print(secrets.token_hex(4))')
    [[ "$USB_SIGNATURE" != 00000000 && "$USB_SIGNATURE" != 1234abcd ]]
    printf '%s\n' "$USB_SIGNATURE" > "$ROOT/usb-signature.txt"
    git apply --check "$ROOT/patches/01-n1-bluetooth.patch"
    git apply "$ROOT/patches/01-n1-bluetooth.patch"
    git apply --check "$ROOT/patches/02-n1-usb-test-guard.patch"
    git apply "$ROOT/patches/02-n1-usb-test-guard.patch"
    for pkg in kmod-bluetooth bluez-daemon bluez-utils bluez-utils-extra bluez-utils-btmon; do
      sed -i "/^CONFIG_PACKAGE_${pkg}=/d; /^# CONFIG_PACKAGE_${pkg} is not set$/d" .config
      printf 'CONFIG_PACKAGE_%s=y\n' "$pkg" >> .config
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
  printf '%s\n' "$MODE" > "$ROOT/prepared-mode.txt"
}
if [[ "$STAGE" == prepare || "$STAGE" == all ]]; then
  stage prepare prepare
  [[ "$STAGE" != prepare ]] || exit 0
fi
[[ "$(cat "$ROOT/prepared-mode.txt")" == "$MODE" ]]
cd "$ROOT/source"
MAKE_ARGS=()
if [[ "$MODE" == usb-test ]]; then
  USB_SIGNATURE=$(cat "$ROOT/usb-signature.txt")
  [[ "$USB_SIGNATURE" =~ ^[0-9a-f]{8}$ ]]
  MAKE_ARGS+=("IMG_PART_SIGNATURE=$USB_SIGNATURE")
fi
run_build_stage() {
  case "$1" in
    download) stage download make download -j4 V=s "${MAKE_ARGS[@]}" ;;
    tools) stage tools make tools/install -j4 V=s "${MAKE_ARGS[@]}" ;;
    toolchain) stage toolchain make toolchain/install -j4 V=s "${MAKE_ARGS[@]}" ;;
    kernel) stage kernel make target/linux/compile -j4 V=s "${MAKE_ARGS[@]}" ;;
    firmware)
      [[ "$MODE" == usb-test ]]
      stage firmware make -j4 V=s "${MAKE_ARGS[@]}" ;;
    verify) stage verify python3 "$ROOT/scripts/verify.py" "$MODE" "$ROOT" ;;
  esac
}
if [[ "$STAGE" == all ]]; then
  for part in download tools toolchain kernel; do run_build_stage "$part"; done
  if [[ "$MODE" == usb-test ]]; then run_build_stage firmware; fi
  run_build_stage verify
else
  run_build_stage "$STAGE"
fi
