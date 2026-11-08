"""Cache only public download files, bounded by size and checked on restore."""
import hashlib
import json
import pathlib
import shutil
import sys


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def transfer(mode, root):
    cache = root / 'download-cache'
    dl = root / 'source/dl'
    limit = 3 * 1024**3
    assert not cache.is_symlink()
    if mode == 'restore':
        manifest = cache / 'manifest.json'
        if not manifest.exists():
            print('No saved source downloads; normal verified download will run.')
            return
        assert not manifest.is_symlink()
        records = json.loads(manifest.read_text())
        assert sum(r['size'] for r in records) <= limit
        # Validate every entry before making any file available to the build.
        for r in records:
            name = r['name']
            assert name not in ('', '.', '..', 'manifest.json')
            assert pathlib.Path(name).name == name and not name.startswith('.')
            p = cache / name
            assert p.is_file() and not p.is_symlink()
            assert p.stat().st_size == r['size'] and digest(p) == r['sha256'], name
        dl.mkdir(parents=True, exist_ok=True)
        for r in records:
            target = dl / r['name']
            assert not target.is_symlink()
            shutil.copyfile(cache / r['name'], target)
        print(f'Restored {len(records)} source downloads after SHA256 checks.')
    elif mode == 'save':
        # This directory contains only files created by this helper on the runner.
        assert not cache.is_symlink()
        if cache.exists():
            shutil.rmtree(cache)
        cache.mkdir()
        records, total = [], 0
        candidates = [p for p in dl.iterdir() if p.is_file() and not p.is_symlink()
                      and not p.name.startswith('.') and not p.name.endswith(('.hash', '.dl', '.tmp'))
                      and p.name != 'manifest.json']
        for p in sorted(candidates, key=lambda p: (p.stat().st_size, p.name)):
            size = p.stat().st_size
            if total + size > limit:
                continue
            shutil.copyfile(p, cache / p.name)
            records.append({'name': p.name, 'size': size, 'sha256': digest(p)})
            total += size
        (cache / 'manifest.json').write_text(json.dumps(records, sort_keys=True) + '\n')
        print(f'Prepared {len(records)} public source downloads ({total} bytes) for cache.')
    else:
        raise ValueError(mode)


if __name__ == '__main__':
    transfer(sys.argv[1], pathlib.Path.cwd())
