#!/usr/bin/env python3
"""Failure-path tests; no hardware, network, root, or disk device access."""
import gzip
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import pack


def digest(data): return hashlib.sha256(data).hexdigest()

def mbr():
    b=bytearray(512); b[440:444]=bytes.fromhex(pack.SIGNATURE)[::-1]; b[510:]=b'\x55\xaa'
    for i,(start,size) in enumerate([(2048,131072),(133120,53248),(186368,1048576)]):
        struct.pack_into('<B3sB3sII',b,446+i*16,0,b'\xfe\xff\xff',14 if i==0 else 131,b'\xfe\xff\xff',start,size)
    return b

class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.path=Path(self.tmp.name)
        # macOS Python lacks Linux xattr APIs; only these synthetic fixtures use an empty substitute.
        if not hasattr(os,'listxattr'):
            x=patch.object(os,'listxattr',return_value=[],create=True);x.start();self.addCleanup(x.stop)
    def file(self,name,data):
        p=self.path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p
    def fixture(self):
        root=self.path/'root';root.mkdir()
        old=b'old controller payload';new=b'new controller payload';oldsha=digest(old);newsha=digest(new)
        collector=b'#!/bin/sh\nexpected='+oldsha.encode()+b'\necho collect-only\n'
        self.file('root/'+pack.HCD,old);self.file('root/'+pack.COLLECTOR,collector).chmod(0o755)
        self.file('root/lib/modules/6.6.144/hci_uart.ko',b'coherent-module')
        self.file('root/lib/n1-bt-guard.sh',b'guard-must-not-change')
        (root/'etc').mkdir();(root/'tmp').mkdir()
        self.file('root/bin/busybox',b'busybox');(root/'bin/sh').symlink_to('busybox')
        hp=self.file('control.hcd',new)
        constants=patch.multiple(pack, OLD_HCD_SHA=oldsha,NEW_HCD_SHA=newsha,COLLECTOR_SHA=digest(collector))
        constants.start();self.addCleanup(constants.stop)
        return root,hp,collector.replace(oldsha.encode(),newsha.encode())
    def test_layout_is_pinned_and_eMMC_id_rejected(self):
        self.assertEqual(pack.partitions(mbr())[1],(68157440,27262976))
        bad=mbr();bad[440:444]=bytes.fromhex('8fd1484a')[::-1]
        with self.assertRaisesRegex(ValueError,'signature'):pack.partitions(bad)
        bad=mbr();bad[494]=1
        with self.assertRaisesRegex(ValueError,'fourth'):pack.partitions(bad)
        bad=mbr();struct.pack_into('<I',bad,458,131073)
        with self.assertRaisesRegex(ValueError,'layout'):pack.partitions(bad)
    def test_wrong_baseline_rejected_before_payload(self):
        a=self.file('bad.gz',b'invalid');h=self.file('hcd',b'invalid')
        with self.assertRaisesRegex(ValueError,'compressed image'):pack.validate_inputs(a,h)
    def test_wrong_hcd_rejected(self):
        a=self.file('base.gz',b'base');h=self.file('hcd',b'bad')
        with patch.object(pack,'BASE_GZ_SHA',digest(b'base')):
            with self.assertRaisesRegex(ValueError,'HCD checksum'):pack.validate_inputs(a,h)
    def test_hcd_command_structure_is_checked(self):
        a=self.file('base.gz',b'base')
        good=b''.join(struct.pack('<HB',0xfc4c,196 if i<43 else 195)+bytes(196 if i<43 else 195) for i in range(322))+struct.pack('<HB',0xfc4e,4)+bytes(4)
        self.assertEqual(len(good),63806)
        h=self.file('hcd',good)
        with patch.multiple(pack,BASE_GZ_SHA=digest(b'base'),NEW_HCD_SHA=digest(good)):
            pack.validate_inputs(a,h)
        bad=bytearray(good);bad[-5]=255;h.write_bytes(bad)
        with patch.multiple(pack,BASE_GZ_SHA=digest(b'base'),NEW_HCD_SHA=digest(bad)):
            with self.assertRaisesRegex(ValueError,'Truncated HCD'):pack.validate_inputs(a,h)
    @unittest.skipUnless(hasattr(os,'setxattr'),'Real Linux xattr API required')
    def test_real_xattr_is_preserved_and_drift_detected(self):
        root,hcd,_=self.fixture();os.setxattr(root/pack.HCD,'user.n1-test',b'keep-me')
        before,after=pack.change_root(root,hcd)
        self.assertEqual(after[pack.HCD]['xattrs'],{'user.n1-test':b'keep-me'.hex()})
        after[pack.HCD]['xattrs']={}
        with self.assertRaisesRegex(ValueError,'Metadata'):pack.audit_changes(before,after,(root/pack.MARKER).read_bytes())
    def test_regular_file_only(self):
        f=self.file('ordinary',b'x');l=self.path/'link';l.symlink_to(f)
        with self.assertRaisesRegex(ValueError,'regular'):pack.regular(l)
        with self.assertRaisesRegex(ValueError,'regular'):pack.regular(self.path)
    def test_decompression_length_and_digest_checks(self):
        a=self.file('base.gz',gzip.compress(b'123456'))
        with patch.multiple(pack,BASE_LENGTH=6,BASE_RAW_SHA=digest(b'123456')):
            out=self.path/'raw';pack.inflate_verified(a,out);self.assertEqual(out.read_bytes(),b'123456')
        with patch.object(pack,'BASE_LENGTH',5):
            with self.assertRaisesRegex(ValueError,'Oversized'):pack.inflate_verified(a,self.path/'over')
        with patch.multiple(pack,BASE_LENGTH=6,BASE_RAW_SHA='0'*64):
            with self.assertRaisesRegex(ValueError,'checksum/length'):pack.inflate_verified(a,self.path/'hash')
    def test_only_allowed_changes_and_exact_collector_substitution(self):
        root,hcd,expected=self.fixture();before,after=pack.change_root(root,hcd)
        self.assertEqual((root/pack.COLLECTOR).read_bytes(),expected)
        self.assertEqual(set(after)-set(before),{pack.MARKER})
        self.assertEqual({n for n in before if before[n]!=after[n]},{pack.HCD,pack.COLLECTOR})
        self.assertEqual(after['bin/sh'],before['bin/sh'])
    def test_unexpected_guard_module_and_file_changes_fail(self):
        root,hcd,_=self.fixture();before,after=pack.change_root(root,hcd);marker=(root/pack.MARKER).read_bytes()
        for name in ['lib/n1-bt-guard.sh','lib/modules/6.6.144/hci_uart.ko']:
            bad={k:dict(v) for k,v in after.items()};bad[name]['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'Unexpected root changes'):pack.audit_changes(before,bad,marker)
        bad=dict(after);bad['etc/unauthorized']={'mode':0}
        with self.assertRaisesRegex(ValueError,'addition/removal'):pack.audit_changes(before,bad,marker)
    def test_metadata_drift_fails(self):
        root,hcd,_=self.fixture();before,after=pack.change_root(root,hcd)
        after[pack.HCD]['mode']^=0o100
        with self.assertRaisesRegex(ValueError,'Metadata'):pack.audit_changes(before,after,(root/pack.MARKER).read_bytes())
    def test_firmware_alias_rejected_before_mutation(self):
        root,hcd,_=self.fixture();(root/'lib/firmware/brcm/BCM4345C0.phicomm,n1.hcd').symlink_to('BCM4345C0.hcd')
        old=(root/pack.HCD).read_bytes()
        with self.assertRaisesRegex(ValueError,'alias'):pack.change_root(root,hcd)
        self.assertEqual((root/pack.HCD).read_bytes(),old)
    def test_parent_symlink_cannot_escape(self):
        outside=self.path/'outside';outside.mkdir();(outside/'file').write_bytes(b'secret')
        root=self.path/'root';root.mkdir();(root/'lib').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'symlink'):pack.check_path(root,'lib/file')
        self.assertEqual(pack.tree(root)['lib']['target'],str(outside))
    def test_hardlink_mutation_rejected(self):
        root,hcd,_=self.fixture();os.link(root/pack.COLLECTOR,root/'collector-alias')
        with self.assertRaisesRegex(ValueError,'hard links'):pack.change_root(root,hcd)
    def test_overflow_does_not_modify_image(self):
        disk=self.file('disk',b'ABCD0123456789WXYZ');fs=self.file('fs',b'12345678901')
        with self.assertRaisesRegex(ValueError,'exceeds'):pack.install_root_partition(disk,fs,4,10)
        self.assertEqual(disk.read_bytes(),b'ABCD0123456789WXYZ')
    def test_fixed_partition_zero_padding_preserves_neighbors(self):
        disk=self.file('disk',b'ABCD0123456789WXYZ');fs=self.file('fs',b'new')
        pack.install_root_partition(disk,fs,4,10)
        self.assertEqual(disk.read_bytes(),b'ABCDnew'+bytes(7)+b'WXYZ')
    def test_truncated_region_read_fails(self):
        disk=self.file('disk',b'123')
        with self.assertRaisesRegex(ValueError,'Short'):pack.copy_region(disk,self.path/'copy',0,4)

if __name__=='__main__':unittest.main(verbosity=2)
