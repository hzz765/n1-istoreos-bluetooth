import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from checkpoint import INPUTS, SOURCE, digest, main, regular

class CheckpointTests(unittest.TestCase):
    def test_exact_public_files_and_replay_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            names = INPUTS + ['source/.config',
                'source/bin/targets/amlogic/meson/test-phicomm_n1-sysupgrade.img.gz',
                'source/bin/packages/arch/base/example.ipk',
                'source/bin/packages/arch/base/hardlinked.ipk',
                'source/build_dir/target-test/linux-amlogic_meson/linux-6.6.144/.config',
                'source/build_dir/target-test/linux-amlogic_meson/linux-6.6.144/.vermagic',
                'source/staging_dir/host/bin/fwtool']
            names += ['public-artifacts/' + n for n in ['build.config', 'source-commit.txt', 'feeds.lock', 'candidate-scope.txt']]
            for name in names:
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(name)
            (root/'source/bin/packages/arch/base/hardlinked.ipk').unlink()
            os.link(root/'source/bin/packages/arch/base/example.ipk', root/'source/bin/packages/arch/base/hardlinked.ipk')
            private = root / 'work/private-usb/private-key'; private.parent.mkdir(parents=True); private.write_text('never upload')
            with patch('checkpoint.subprocess.check_output', side_effect=[SOURCE+'\n', 'test-repo-commit\n']), patch('checkpoint.attest_patch'):
                main(root)
            out = root / 'candidate-checkpoint'
            record = json.loads((out/'checkpoint.json').read_text())
            expected = {n for n in names if n.startswith(('source/', 'public-artifacts/'))}
            self.assertEqual(set(record['files']), expected)
            archive = out / 'UNVERIFIED-compile-checkpoint.tar'
            with tarfile.open(archive) as tar:
                self.assertEqual(set(tar.getnames()), expected | {'candidate-checkpoint/checkpoint.json', 'candidate-checkpoint/DO-NOT-INSTALL.txt'})
                self.assertTrue(all(m.isfile() for m in tar.getmembers()))
                for name, value in record['files'].items():
                    self.assertEqual(digest(root/name), value)
            self.assertTrue((out/'SHA256SUMS').read_text().startswith(digest(archive)))

    def test_reject_symlink_and_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); directory = root/'source'; directory.mkdir()
            outside = root/'outside'; outside.write_text('not a build output')
            link = directory/'output'; link.symlink_to(outside)
            with self.assertRaises(AssertionError): regular(directory, link)
            with self.assertRaises(AssertionError): regular(directory, outside)

if __name__ == '__main__': unittest.main()
