"""Safety checks use temporary regular files; never access physical disks."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('rescue_guard', Path(__file__).with_name('guard.py'))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class SafetyTests(unittest.TestCase):
    def table(self):
        return {'label': 'dos', 'unit': 'sectors', 'sectorsize': 512,
                'partitions': [{'node': f'/dev/mmcblk9p{i}', 'start': start, 'size': size}
                               for i, start, size in [(1, 2048, 100), (2, 2200, 100), (3, 2400, 100), (4, 2600, 100)]]}

    def test_partition_ranges_cover_every_nonfilesystem_byte(self):
        table = self.table()
        total = 3000 * 512
        with patch.object(guard, 'run', return_value=json.dumps({'partitiontable': table})):
            _, parts, gaps = guard.layout('mmcblk9', total)
        regions = sorted(gaps + [(p['start'] * 512, p['size'] * 512) for p in parts])
        cursor = 0
        for start, size in regions:
            self.assertEqual(start, cursor)
            cursor += size
        self.assertEqual(cursor, total)

    def test_reject_overlap_wrong_device_and_extended_layout(self):
        for change in ('overlap', 'node', 'gpt'):
            table = self.table()
            if change == 'overlap':
                table['partitions'][1]['start'] = 2100
            elif change == 'node':
                table['partitions'][0]['node'] = '/dev/sda1'
            else:
                table['label'] = 'gpt'
            with patch.object(guard, 'run', return_value=json.dumps({'partitiontable': table})):
                with self.assertRaises(RuntimeError):
                    guard.layout('mmcblk9', 3000 * 512)

    def test_dirty_ext4_is_not_repaired_or_cloned(self):
        with patch.object(guard, 'run', side_effect=['ext4', 'Filesystem state: not clean\n']), patch.object(guard.subprocess, 'run') as execute:
            with self.assertRaises(RuntimeError):
                guard.ext4_used('/dev/not-a-real-device')
            execute.assert_not_called()

    def test_raw_copy_exact_range_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            src, dst = Path(directory) / 'source', Path(directory) / 'dest'
            src.write_bytes(bytes(range(256)) * 8)
            guard.copy_range(str(src), dst, 100, 700)
            self.assertEqual(dst.read_bytes(), src.read_bytes()[100:800])
            with self.assertRaises(FileExistsError):
                guard.copy_range(str(src), dst, 0, 1)
            with self.assertRaises(RuntimeError):
                guard.copy_range(str(src), Path(directory) / 'truncated', 2000, 100)
            self.assertEqual(len(src.read_bytes()), 2048)

    def test_initialization_protects_before_mount_or_network(self):
        text = Path(__file__).with_name('init.sh').read_text()
        self.assertLess(text.index('/sbin/n1-rescue protect'), text.index('/sbin/n1-rescue mount-boot'))
        self.assertLess(text.index('/sbin/n1-rescue protect'), text.index('ip link set'))
        self.assertNotIn('mount -a', text)
        self.assertIn('dropbear -s -g -j -k', text)


if __name__ == '__main__':
    unittest.main()
