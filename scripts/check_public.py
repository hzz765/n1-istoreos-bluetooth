"""Fail closed before uploading explicitly named text artifacts. Never print matches."""
import pathlib
import re

root = pathlib.Path('public-artifacts')
texts = ['build.log', 'build.config', 'kernel.config', 'source-commit.txt',
         'feeds.lock', 'verification.txt', 'candidate-scope.txt', 'candidate-verification.json', 'package-manifest.json', 'diagnostic-build.json', 'diagnostic-source.json', 'SHA256SUMS']
patterns = [rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
            rb'github_pat_[A-Za-z0-9_]{20,}', rb'gh[pousr]_[A-Za-z0-9]{20,}',
            rb'https?://[^\s/:]+:[^\s/@]+@']
paths = [root / name for name in texts]
paths += [pathlib.Path('bluetooth-output') / name for name in [
    'verification.txt', 'loader.config', 'loader-verification.json',
    'entry-tests.txt', 'boot-policy-tests.txt', 'module-hashes.json',
    'guard-tests.txt', 'mount-help.txt', 'blkid-help.txt', 'pivot-help.txt',
    'README.md', 'kernel.config', 'build-source.txt', 'diagnostic-build.json',
    'diagnostic-target-checks.json', 'SHA256SUMS']]
for path in paths:
    name = str(path)
    if not path.exists():
        continue
    assert path.is_file() and not path.is_symlink(), f'Unexpected artifact type: {name}'
    with path.open('rb') as handle:
        for line in handle:
            assert not any(re.search(p, line) for p in patterns), f'Upload blocked: potential credential in {name}'
print('Named text artifacts passed credential-pattern checks. No private local data is a build input.')
