import unittest
import struct
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from prepare import config, BT_PACKAGES
from verify import partitions, script_body

class CandidateTests(unittest.TestCase):
    def test_preserve_official_config(self):
        original=(Path(__file__).resolve().parent.parent/'configs/24.10-config.buildinfo').read_text()
        generated=config(original)
        changed={f'CONFIG_PACKAGE_{p}' for p in BT_PACKAGES}|{'CONFIG_VERSION_CODE','CONFIG_VERSION_NUMBER'}
        for line in original.splitlines():
            key=line.split('=')[0]
            if key not in changed: self.assertIn(line,generated)
        self.assertIn('CONFIG_PACKAGE_dockerd=y',generated)
        self.assertNotIn('CONFIG_PACKAGE_n1-bluealsa=',generated)
    def mbr(self):
        b=bytearray(512);b[510:]=b'\x55\xaa'
        for i,(start,size) in enumerate([(2048,131072),(133120,524288),(657408,4194304)]):
            b[450+16*i]=0x83
            struct.pack_into('<II',b,454+16*i,start,size)
        return b
    def test_partition_bounds(self):
        self.assertEqual(len(partitions(self.mbr())),3)
        for offset,value in [(510,0),(450,0)]:
            b=self.mbr();b[offset]=value
            with self.assertRaises(AssertionError):partitions(b)
        b=self.mbr();struct.pack_into('<I',b,470,2048)
        with self.assertRaises(AssertionError):partitions(b)
        b=self.mbr();struct.pack_into('<I',b,474,524289)
        with self.assertRaises(AssertionError):partitions(b)
    def test_scripts_reject_malformed(self):
        with self.assertRaises(AssertionError):script_body(bytes(72))
    def test_no_usb_overlay(self):
        source=(Path(__file__).resolve().parent/'prepare.py').read_text()
        self.assertNotIn('git apply "$ROOT/patches/02',source)
        self.assertNotIn('diagnostics/build_integration',source)

if __name__=='__main__':unittest.main()
