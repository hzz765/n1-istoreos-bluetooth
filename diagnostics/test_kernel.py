#!/usr/bin/env python3
"""Offline patch/application checks, not a kernel compilation test."""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
from kernel_apply import verify

HERE = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get('N1_DIAG_SOURCE_DIR', HERE.parent / 'work/uart-investigation/uart-agent'))
MANIFEST = json.loads((HERE / 'kernel-sources.json').read_text())
PATCH = HERE / 'kernel-observe.patch'

class KernelPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for rel, digest in MANIFEST['sha256'].items():
            data = (SOURCE / Path(rel).name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def apply(self, *opts, check=True):
        return subprocess.run(['git', 'apply', *opts, str(PATCH)],
                              cwd=self.root, capture_output=True, check=check)

    def test_apply_reverse_exact_and_not_idempotent(self):
        self.apply('--check')
        self.apply()
        self.assertNotEqual(self.apply('--check', check=False).returncode, 0)
        self.apply('--reverse')
        for rel, digest in MANIFEST['sha256'].items():
            self.assertEqual(hashlib.sha256((self.root/rel).read_bytes()).hexdigest(), digest)

    def test_logs_do_not_replace_hardware_actions_or_error_path(self):
        self.apply()
        bcm = (self.root/'drivers/bluetooth/hci_bcm.c').read_text()
        meson = (self.root/'drivers/tty/serial/meson_uart.c').read_text()
        self.assertIn('if (!err)\n\t\t\thost_set_baudrate(hu, speed);', bcm)
        self.assertEqual(bcm.count('actual = serdev_device_set_baudrate(hu->serdev, speed);'), 1)
        original = (SOURCE/'meson_uart.c').read_text()
        # No modifications to data path: no per-byte logging/counter changes.
        self.assertEqual(original[original.index('static void meson_uart_set_mctrl'):original.index('static void meson_uart_set_termios')],
                         meson[meson.index('static void meson_uart_set_mctrl'):meson.index('static void meson_uart_set_termios')])
        termios = meson[meson.index('static void meson_uart_set_termios'):meson.index('static int meson_uart_verify_port')]
        self.assertLess(termios.index('spin_unlock_irqrestore'), termios.index('dev_info'))
        self.assertNotIn('AML_UART_RFIFO', termios)
        self.assertEqual(original.count('writel('), meson.count('writel('))
        self.assertIn('diag_irq_result = bcm_request_irq(bcm);', bcm)
        self.assertIn('if (!diag_irq_result) {\n\t\terr = bcm_setup_sleep(hu);', bcm)

    def test_hash_guard_rejects_modified_or_symlink_sources(self):
        verify(self.root)
        p = self.root/'drivers/bluetooth/hci_bcm.c'
        original = p.read_bytes()
        p.write_bytes(original+b'\n')
        with self.assertRaises(ValueError):
            verify(self.root)
        p.unlink()
        p.symlink_to(SOURCE.resolve()/'hci_bcm.c')
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_pm_error_return_semantics(self):
        self.apply()
        bcm = (self.root/'drivers/bluetooth/hci_bcm.c').read_text()
        fragment = bcm[bcm.index('\tdiag_irq_result = bcm_request_irq(bcm);'):bcm.index('\n\treturn err;', bcm.index('\tdiag_irq_result = bcm_request_irq(bcm);'))]
        fragment = re.sub(r'\t+bt_dev_info\(.*?;\n', '', fragment, flags=re.S)
        self.assertEqual(fragment.strip(), 'diag_irq_result = bcm_request_irq(bcm);\n\tif (!diag_irq_result) {\n\t\terr = bcm_setup_sleep(hu);\n\t}')

    def test_patch_has_only_expected_files(self):
        targets = {line[6:] for line in PATCH.read_text().splitlines() if line.startswith('+++ b/')}
        self.assertEqual(targets, set(MANIFEST['sha256']))

if __name__ == '__main__':
    unittest.main()
