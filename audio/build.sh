#!/usr/bin/env bash
set -euo pipefail
ROOT="$PWD"
python3 "$ROOT/scripts/space_guard.py" "$ROOT" &
guard=$!
trap 'kill "$guard" 2>/dev/null || true' EXIT
echo "Audio stage: $1"
df -h .
case "$1" in
prepare)
 git clone --filter=blob:none --no-checkout https://github.com/istoreos/istoreos.git source
 cd source
 git checkout --detach 5fe2cddefc93d59156ca66740ed896ae954d8e8e
 cp "$ROOT/configs/24.10-feeds.buildinfo" feeds.conf
 ./scripts/feeds update -a
 ./scripts/feeds install -a
 cp -r "$ROOT/audio/package" package/n1-bluealsa
 # Bluetooth virtual PCM uses userspace ALSA plugins; no local sound-card kmod.
 python3 - <<'PY'
from pathlib import Path
p=Path('feeds/packages/libs/alsa-lib/Makefile');s=p.read_text()
assert s.count('+kmod-sound-core ')==1
p.write_text(s.replace('+kmod-sound-core ',''))
PY
 cat > .config <<'EOF'
CONFIG_TARGET_amlogic=y
CONFIG_TARGET_amlogic_meson=y
CONFIG_TARGET_MULTI_PROFILE=y
CONFIG_TARGET_DEVICE_amlogic_meson_DEVICE_phicomm_n1=y
CONFIG_DEVEL=y
CONFIG_CCACHE=y
CONFIG_PACKAGE_n1-bluealsa=m
CONFIG_PACKAGE_n1-bluealsa-utils=m
CONFIG_PACKAGE_alsa-utils=m
# CONFIG_KERNEL_DEBUG_INFO_BTF is not set
# CONFIG_KERNEL_DEBUG_INFO_BTF_MODULES is not set
# CONFIG_NEED_BPF_TOOLCHAIN is not set
# CONFIG_USE_LLVM_BUILD is not set
EOF
 make defconfig
 ! grep -q '^CONFIG_USE_LLVM_BUILD=y' .config
 mkdir -p "$ROOT/audio-output"
 cp .config "$ROOT/audio-output/build.config"
 cp feeds.conf "$ROOT/audio-output/feeds.lock"
 git rev-parse HEAD > "$ROOT/audio-output/source-commit.txt"
 ;;
tools|toolchain)
 cd source
 make -j"$(nproc)" "$1/install" V=s
 ;;
packages)
 cd source
 make -j"$(nproc)" package/n1-bluealsa/compile V=s
 make -j"$(nproc)" package/feeds/packages/alsa-utils/compile V=s
 ;;
collect)
 python3 - <<'PY'
from pathlib import Path
import shutil,hashlib,json
out=Path('audio-output');pkgs=out/'packages';pkgs.mkdir()
files=list(Path('source/bin').rglob('*.ipk'));assert files
for p in files:
 assert not p.name.startswith(('kmod-','kernel_')),'Unexpected kernel package'
 q=pkgs/p.name;assert not q.exists();shutil.copyfile(p,q)
(out/'scope.txt').write_text('Userspace packages only, not a firmware or an automatic installer. Source and feeds pinned to diagnostic baseline. ALSA virtual PCM dependency excludes local sound-card kmod. Native HFP CVSD and A2DP SBC; mSBC optional codec is not enabled. No N1 access or private data. Offline ABI and install review required before deployment.\n')
(out/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(out))+'\n' for p in sorted(out.rglob('*')) if p.is_file()))
PY
 ;;
*) exit 2;;
esac
