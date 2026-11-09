import unittest
import struct
import os
import subprocess
import tempfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from prepare import config, BT_PACKAGES
from verify import partitions, script_body
from config_check import check, differences

REPO = Path(__file__).resolve().parent.parent

class CandidateTests(unittest.TestCase):
    def test_feed_preparation_restores_official_config_after_feed_defconfig(self):
        # Execute the actual feeds/config-copy block. The fake feed update models
        # run 42572893961: it normalized away package choices before feed install.
        build = (REPO/'scripts/build.sh').read_text()
        block = build.split('  git checkout --detach "$SOURCE_COMMIT"\n', 1)[1].split('  if is_usb;', 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source/'scripts').mkdir()
            feeds = source/'scripts/feeds'
            feeds.write_text('#!/bin/sh\nset -eu\ncase "$1" in\n'
                             'update) printf "# CONFIG_PACKAGE_luci is not set\\n# CONFIG_PACKAGE_dockerd is not set\\n" > .config;;\n'
                             'install) touch feeds-installed;;\nesac\n')
            feeds.chmod(0o755)
            subprocess.run(['bash', '-eu', '-c', block], cwd=source,
                           env={**os.environ, 'ROOT': str(REPO)}, check=True)
            self.assertTrue((source/'feeds-installed').exists())
            self.assertEqual((source/'.config').read_bytes(), (REPO/'configs/24.10-config.buildinfo').read_bytes())

    def test_resolved_configuration_rejects_actual_failure_pattern(self):
        original = (REPO/'configs/24.10-config.buildinfo').read_text()
        valid = config(original)
        self.assertGreater(check(original, valid), 500)
        # These exact disabled lines appear in the failed run's build.config.
        missing = ['luci', 'dockerd', 'containerd', 'bash', 'avahi-utils']
        failed = valid
        for name in missing:
            failed = failed.replace(f'CONFIG_PACKAGE_{name}=y', f'# CONFIG_PACKAGE_{name} is not set')
        diff = differences(original, failed)
        self.assertEqual(set(diff), {f'CONFIG_PACKAGE_{name}' for name in missing})
        self.assertTrue(all(v == {'expected': 'y', 'actual': 'n'} for v in diff.values()))
        with self.assertRaisesRegex(ValueError, 'lost 5 required'):
            check(original, failed)

    def test_resolved_configuration_reports_module_and_absent_separately(self):
        original = 'CONFIG_PACKAGE_luci=y\nCONFIG_PACKAGE_dockerd=y\n'
        resolved = config(original).replace('CONFIG_PACKAGE_luci=y', 'CONFIG_PACKAGE_luci=m').replace('CONFIG_PACKAGE_dockerd=y\n', '')
        diff = differences(original, resolved)
        self.assertEqual(diff['CONFIG_PACKAGE_luci']['actual'], 'm')
        self.assertEqual(diff['CONFIG_PACKAGE_dockerd']['actual'], 'absent')
        with self.assertRaises(ValueError):
            check(original, resolved)

    def test_module_selection_may_be_promoted_but_not_removed(self):
        original = 'CONFIG_PACKAGE_optional=m\n'
        selected = config(original)
        check(original, selected)
        check(original, selected.replace('CONFIG_PACKAGE_optional=m', 'CONFIG_PACKAGE_optional=y'))
        with self.assertRaisesRegex(ValueError, 'CONFIG_PACKAGE_optional: expected=m, actual=n'):
            check(original, selected.replace('CONFIG_PACKAGE_optional=m', '# CONFIG_PACKAGE_optional is not set'))

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
