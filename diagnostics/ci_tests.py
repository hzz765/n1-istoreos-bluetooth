#!/usr/bin/env python3
"""Fetch only hash-pinned public source files and run offline diagnostic tests."""
from pathlib import Path
import hashlib,json,os,subprocess,urllib.request
root=Path.cwd(); manifest=json.loads((root/'diagnostics/kernel-sources.json').read_text())
source=root/'diagnostic-test-sources'; source.mkdir(exist_ok=True)
for rel,sha in manifest['sha256'].items():
    data=urllib.request.urlopen(manifest['base']+rel,timeout=60).read()
    assert hashlib.sha256(data).hexdigest()==sha,rel
    (source/Path(rel).name).write_bytes(data)
env=dict(os.environ,N1_DIAG_SOURCE_DIR=str(source))
for name in ['test_kernel.py','test_build_integration.py','test_collect.py','test_image_checks.py']:
    subprocess.run(['python3',str(root/'diagnostics'/name)],env=env,check=True)
