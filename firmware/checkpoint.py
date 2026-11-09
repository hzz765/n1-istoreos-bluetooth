"""Retain only public compile outputs for offline re-verification, never installation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
from prepare import SOURCE, attest_patch

INPUTS = ['configs/24.10-config.buildinfo', 'configs/24.10-feeds.buildinfo',
          'patches/01-n1-bluetooth.patch', 'firmware/prepare.py', 'scripts/build.sh']


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while b := f.read(1024 * 1024):
            h.update(b)
    return h.hexdigest()


def regular(root, path):
    assert path.is_file() and not path.is_symlink(), path
    assert path.resolve().is_relative_to(root.resolve()), path
    return path


def main(root):
    source = root / 'source'
    assert subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip() == SOURCE
    attest_patch(source, root)
    out = root / 'candidate-checkpoint'
    out.mkdir(exist_ok=False)
    kernels = list((source / 'build_dir').glob('target-*/linux-amlogic_meson/linux-6.6.144'))
    assert len(kernels) == 1
    images = list((source / 'bin/targets/amlogic/meson').glob('*phicomm_n1*sysupgrade.img.gz'))
    assert len(images) == 1
    files = images + list((source / 'bin').rglob('*.ipk'))
    files += [source / '.config', kernels[0] / '.config', kernels[0] / '.vermagic',
              source / 'staging_dir/host/bin/fwtool']
    files += [root / 'public-artifacts' / name for name in
              ['build.config', 'source-commit.txt', 'feeds.lock', 'candidate-scope.txt']]
    files = sorted(set(regular(root, p) for p in files))
    record = {'status': 'UNVERIFIED - DO NOT INSTALL OR BOOT', 'source': SOURCE,
              'repository_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'run_id': os.environ.get('GITHUB_RUN_ID'),
              'build_inputs': {name: digest(root / name) for name in INPUTS},
              'files': {str(p.relative_to(root)): digest(p) for p in files}}
    meta = out / 'checkpoint.json'
    meta.write_text(json.dumps(record, indent=2) + '\n')
    notice = out / 'DO-NOT-INSTALL.txt'
    notice.write_text('UNVERIFIED COMPILE CHECKPOINT. Not an approved firmware.\n'
                      'For offline inspection/re-verification only; never boot or flash directly.\n'
                      'Contains only public cloud compile outputs; no device backups or private keys.\n')
    archive = out / 'UNVERIFIED-compile-checkpoint.tar'
    with tarfile.open(archive, 'w', dereference=True) as tar:
        for p in files + [meta, notice]:
            tar.add(p, arcname=str(p.relative_to(root)), recursive=False)
    (out / 'SHA256SUMS').write_text(digest(archive) + '  ' + archive.name + '\n')
    print('Retained public compile checkpoint for inspection only:', len(files), 'files')


if __name__ == '__main__':
    main(Path.cwd())
