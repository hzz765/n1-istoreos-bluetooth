import unittest
import struct
import os
import subprocess
import shutil
import tempfile
from unittest import mock
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from prepare import config, BT_PACKAGES
from verify import partitions, script_body, own_inspection_tree, require_absent, sha
from config_check import check, differences

REPO = Path(__file__).resolve().parent.parent

class CandidateTests(unittest.TestCase):
    def test_inspection_ownership_is_bounded_and_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp).resolve()
            fs = work/'root'
            fs.mkdir()
            with mock.patch('verify.subprocess.run') as execute:
                own_inspection_tree(fs, work)
                execute.assert_called_once_with(
                    ['sudo', 'chown', '-hR', '-P', f'{os.getuid()}:{os.getgid()}', '--', str(fs)], check=True)
                execute.reset_mock()
                with self.assertRaises(AssertionError):
                    own_inspection_tree(work, work)
                fs.rmdir()
                fs.symlink_to(work, target_is_directory=True)
                with self.assertRaises(AssertionError):
                    own_inspection_tree(fs, work)
                execute.assert_not_called()

    def test_forbidden_paths_reject_dangling_and_ancestor_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            fs = Path(tmp)
            (fs/'root').mkdir(mode=0o700)
            require_absent(fs, 'root/.ssh/authorized_keys')
            ssh = fs/'root/.ssh'
            ssh.mkdir(mode=0o700)
            key = ssh/'authorized_keys'
            key.write_text('not a real key')
            with self.assertRaises(AssertionError): require_absent(fs, 'root/.ssh/authorized_keys')
            key.unlink()
            key.symlink_to('missing-target')
            self.assertFalse(key.exists())  # The old exists() check missed this.
            with self.assertRaises(AssertionError): require_absent(fs, 'root/.ssh/authorized_keys')
            key.unlink(); ssh.rmdir()
            ssh.symlink_to('missing-directory', target_is_directory=True)
            with self.assertRaises(AssertionError): require_absent(fs, 'root/.ssh/authorized_keys')

    def test_root_owned_mode_700_extraction_is_readable_after_ownership_fix(self):
        # Exercise the actual failure on the unprivileged Ubuntu Actions runner.
        # Local macOS runs may lack noninteractive sudo; never request a password.
        sudo = shutil.which('sudo')
        try:
            available = sudo and subprocess.run([sudo, '-n', 'true'], stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL).returncode == 0
        except OSError:
            available = False
        if not available or os.getuid() == 0:
            if sys.platform.startswith('linux') and os.environ.get('GITHUB_ACTIONS') == 'true':
                self.fail('Permission regression requires unprivileged runner with noninteractive sudo')
            self.skipTest('Requires an unprivileged user and noninteractive sudo; mandatory on GitHub Actions')
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp).resolve()
            fs = work/'root'; fs.mkdir()
            home = fs/'root'; home.mkdir(mode=0o700)
            outside = work/'outside'; outside.write_text('must remain untouched')
            (fs/'external-link').symlink_to(outside)
            image = work/'image.img'; image.write_bytes(b'input image must not change')
            digest = sha(image)
            old = outside.stat()
            subprocess.run([sudo, '-n', 'chown', '0:0', str(home)], check=True)
            try:
                with self.assertRaises(PermissionError): require_absent(fs, 'root/.ssh/authorized_keys')
                own_inspection_tree(fs, work)
                require_absent(fs, 'root/.ssh/authorized_keys')
                self.assertEqual(home.stat().st_uid, os.getuid())
                self.assertEqual(home.stat().st_mode & 0o777, 0o700)
                self.assertEqual(sha(image), digest)
                self.assertEqual((outside.stat().st_uid, outside.stat().st_gid, outside.stat().st_ctime_ns),
                                 (old.st_uid, old.st_gid, old.st_ctime_ns))
            finally:
                subprocess.run([sudo, '-n', 'chown', '-hR', '-P', f'{os.getuid()}:{os.getgid()}', '--', str(fs)], check=True)

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
