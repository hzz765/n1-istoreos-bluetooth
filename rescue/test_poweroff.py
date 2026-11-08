"""Exercise installed shutdown logic without sending any real poweroff signal."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch, mock_open

path = '/sbin/poweroff' if Path('/etc/n1-rescue').exists() else str(Path(__file__).with_name('poweroff.py'))
loader = importlib.machinery.SourceFileLoader('safe_poweroff', path)
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class ShutdownTests(unittest.TestCase):
    def test_shutdown_boundaries(self):
        for case in ('busy_lock', 'busy_mount', 'unmounted', 'mounted', 'unknown'):
            calls = []
            def execute(cmd, **kwargs):
                calls.append(cmd)
                if cmd[0] == 'mountpoint':
                    return subprocess.CompletedProcess(cmd, 32 if case == 'unmounted' else 1 if case == 'unknown' else 0)
                if cmd[0] == 'umount' and case == 'busy_mount':
                    raise subprocess.CalledProcessError(1, cmd)
                return subprocess.CompletedProcess(cmd, 0)
            with patch('builtins.open', mock_open()), patch.object(module.sys, 'argv', ['poweroff']), \
                 patch.object(module.fcntl, 'flock', side_effect=BlockingIOError() if case == 'busy_lock' else None), \
                 patch.object(module.os, 'chdir'), patch.object(module.os, 'sync'), \
                 patch.object(module.Path, 'touch'), patch.object(module.subprocess, 'run', side_effect=execute):
                if case in ('busy_lock', 'busy_mount', 'unknown'):
                    with self.assertRaises((OSError, RuntimeError, subprocess.CalledProcessError)):
                        module.shutdown()
                    self.assertNotIn(['/bin/busybox', 'poweroff'], calls)
                else:
                    module.shutdown()
                    self.assertEqual(calls[-1], ['/bin/busybox', 'poweroff'])
                    if case == 'mounted':
                        self.assertEqual(calls[-2], ['umount', '/run/backup'])


if __name__ == '__main__':
    unittest.main()
