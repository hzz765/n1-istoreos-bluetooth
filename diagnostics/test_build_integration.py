#!/usr/bin/env python3
"""Offline tests; never invoke firmware compilation or contact a device."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import build_integration as bi

HERE=Path(__file__).resolve().parent
SOURCES=Path(os.environ.get('N1_DIAG_SOURCE_DIR',HERE.parent/'work/uart-investigation/uart-agent'))

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for rel,sha in json.loads((HERE/'kernel-sources.json').read_text())['sha256'].items():
            src=SOURCES/Path(rel).name
            self.assertEqual(hashlib.sha256(src.read_bytes()).hexdigest(),sha)
            dst=self.root/rel; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(src.read_bytes())
    def patch(self):
        subprocess.run(['git','apply',str(HERE/'kernel-observe.patch')],cwd=self.root,check=True)
    def test_attests_prepared_bytes_without_mutation(self):
        self.patch()
        before={str(p):p.read_bytes() for p in self.root.rglob('*.c')}
        result=bi.attest(self.root,self.root/'record.json')
        self.assertEqual(result['linux_version'],'6.6.144')
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*.c')})
        self.assertNotEqual(result['original_sha256'],result['prepared_sha256'])
    def test_unpatched_and_extra_vendor_mutation_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError): bi.attest(self.root,self.root/'record.json')
        self.patch(); p=self.root/'drivers/bluetooth/hci_bcm.c'; p.write_text(p.read_text()+'\n/* vendor change */\n')
        with self.assertRaises(ValueError): bi.attest(self.root,self.root/'record.json')
        self.assertFalse((self.root/'record.json').exists())
    def test_symlink_rejected(self):
        self.patch(); p=self.root/'drivers/bluetooth/hci_bcm.c'; p.rename(p.with_suffix('.save'));p.symlink_to('hci_bcm.save')
        with self.assertRaises(ValueError): bi.attest(self.root,self.root/'record.json')
    def test_install_is_explicit_and_not_repeatable(self):
        (self.root/'target/linux/amlogic/patches-6.6').mkdir(parents=True)
        bi.install(self.root)
        p=self.root/'target/linux/amlogic/patches-6.6'/bi.PATCH_NAME
        self.assertEqual(p.read_bytes(),(HERE/'kernel-observe.patch').read_bytes())
        self.assertEqual((self.root/'files/usr/sbin/n1-bt-collect').read_bytes(),(HERE/'collect.sh').read_bytes())
        self.assertFalse((self.root/'files/etc/rc.d').exists())
        with self.assertRaises(ValueError):bi.install(self.root)

if __name__=='__main__':unittest.main()
