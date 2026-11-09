"""Only named public build outputs; never upload local work or private evidence."""
import pathlib,re
root=pathlib.Path('comparison-output')
expected={'n1-hcd-control.img.gz','comparison.json','tree-diff.json','arm-checks.json','SHA256SUMS','BCM-LEGAL.txt','firmware-copyright.txt','evidence.txt'}
assert root.is_dir() and not root.is_symlink()
assert {p.name for p in root.iterdir()}==expected
patterns=[rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',rb'github_pat_[A-Za-z0-9_]{20,}',rb'gh[pousr]_[A-Za-z0-9]{20,}',rb'https?://[^\s/:]+:[^\s/@]+@',rb'/Users/',rb'192\.168\.5\.4']
for p in root.iterdir():
 assert p.is_file() and not p.is_symlink()
 if p.suffix!='.gz':
  data=p.read_bytes()
  assert not any(re.search(x,data) for x in patterns), 'Potential private information; upload stopped'
print('Explicit public output set and text credential checks passed.')
