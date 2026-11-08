#!/usr/bin/env python3
"""Regular-file fixtures for diagnostic packaging fail-closed gates."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('diag_image_checks',HERE/'image_checks.py')
checks=importlib.util.module_from_spec(spec); spec.loader.exec_module(checks)

class ImageChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def put(self,name,text):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);return p
    def test_remove_only_nbd_and_preserve_other_modules(self):
        self.put('etc/modules.d/30-nbd','nbd\n')
        self.put('etc/modules.d/mixed','# keep comment\nnbd max_part=8\nbluetooth\n')
        self.put('etc/modules.d/other','nbd_extra\n')
        with self.assertRaises(AssertionError): checks.verify_no_nbd_autoload(self.root)
        checks.remove_nbd_autoload(self.root)
        checks.verify_no_nbd_autoload(self.root)
        self.assertFalse((self.root/'etc/modules.d/30-nbd').exists())
        self.assertEqual((self.root/'etc/modules.d/mixed').read_text(),'# keep comment\nbluetooth\n')
        self.assertEqual((self.root/'etc/modules.d/other').read_text(),'nbd_extra\n')
    def test_normal_openwrt_boot_links_and_escape_rejection(self):
        self.put('etc/modules.d/30-gpio','gpio_button_hotplug\n')
        self.put('etc/modules.d/30-nbd','nbd\n')
        early=self.root/'etc/modules-boot.d';early.mkdir()
        (early/'30-gpio').symlink_to('../modules.d/30-gpio')
        (early/'30-nbd').symlink_to('../modules.d/30-nbd')
        checks.remove_nbd_autoload(self.root)
        checks.verify_no_nbd_autoload(self.root)
        self.assertTrue((early/'30-gpio').is_symlink())
        self.assertFalse((early/'30-nbd').is_symlink())
        (early/'escape').symlink_to('/etc/passwd')
        with self.assertRaises(AssertionError):checks.verify_no_nbd_autoload(self.root)
        with self.assertRaises(AssertionError):checks.remove_nbd_autoload(self.root)
    def test_reject_changed_target_binary(self):
        p=self.put('usr/bin/btmon','bytes')
        r=self.put('report.json',json.dumps({'status':'PASS_ARM_USERSPACE','sha256':{'usr/bin/btmon':checks.sha(p)}}))
        checks.verify_target_report(self.root,r)
        p.write_text('changed')
        with self.assertRaises(AssertionError): checks.verify_target_report(self.root,r)
    def test_reject_target_report_escape(self):
        r=self.put('report.json',json.dumps({'status':'PASS_ARM_USERSPACE','sha256':{'../outside':'no'}}))
        with self.assertRaises(AssertionError): checks.verify_target_report(self.root,r)
    def test_reject_mixed_kernel_modules_and_changed_dtb(self):
        boot=self.root/'boot';boot.mkdir()
        kernel=self.put('boot/kernel.img','N1DIAG termios fixture')
        dtb=self.put('boot/amlogic.dtb','fixed DTB')
        hcd=self.put('lib/firmware/brcm/BCM4345C0.hcd','fixed HCD')
        collector=self.put('usr/sbin/n1-bt-collect','collector')
        mod=self.put('lib/modules/6.6.144/hci_uart.ko','N1DIAG controller-baud fixture')
        for package in ['coreutils-timeout','bluez-utils-btmon']:
            self.put('usr/lib/opkg/info/'+package+'.control','Package: '+package)
        self.put('etc/n1-bt-diagnostic','diagnostic')
        m={'kernel_sha256':checks.sha(kernel),'dtb_sha256':checks.sha(dtb),
           'collector_sha256':checks.sha(collector),'modules':{str(mod.relative_to(self.root)):checks.sha(mod)}}
        with patch.object(checks,'DTB_SHA',checks.sha(dtb)),patch.object(checks,'HCD_SHA',checks.sha(hcd)):
            checks.inspect(self.root,boot,m)
            mod.write_text('wrong module')
            with self.assertRaises(AssertionError):checks.inspect(self.root,boot,m)
            mod.write_text('N1DIAG controller-baud fixture')
            dtb.write_text('changed DTB')
            with self.assertRaises(AssertionError):checks.inspect(self.root,boot,m)
    def test_manifest_bound_to_source_and_current_checkout(self):
        base=self.root/'base';base.mkdir()
        for p in ['n1-usb-test.img.gz','kernel.config']: (base/p).write_text(p)
        self.put('diagnostics/kernel-observe.patch','patch')
        self.put('diagnostics/collect.sh','collector')
        m={'schema':1,'mode':'usb-diagnostic','source_commit':checks.SOURCE_COMMIT,'workflow_commit':'a'*40,
           'image_sha256':checks.sha(base/'n1-usb-test.img.gz'),'kernel_config_sha256':checks.sha(base/'kernel.config'),
           'patch_sha256':checks.sha(self.root/'diagnostics/kernel-observe.patch'),'collector_sha256':checks.sha(self.root/'diagnostics/collect.sh')}
        (base/'diagnostic-build.json').write_text(json.dumps(m))
        with patch.object(checks.subprocess,'check_output',return_value='a'*40+'\n'):
            checks.manifest(base,self.root)
            (base/'n1-usb-test.img.gz').write_text('stale')
            with self.assertRaises(AssertionError): checks.manifest(base,self.root)
if __name__=='__main__':unittest.main()
