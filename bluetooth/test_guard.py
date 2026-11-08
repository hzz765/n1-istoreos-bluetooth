#!/usr/bin/env python3
"""Offline guard fixtures; no kernel, block-device or filesystem mounting tests.

Copies the production shell scripts into a temporary tree and rewrites their
absolute filesystem paths into that tree. Only the infinite stop sink is replaced
with exit 42. mount/umount/blkid/pivot_root/readlink are explicit fixture commands.
The shell still executes the production guard predicates and ordering unchanged.
"""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
PARTUUID = '1234abcd-02'
OVERLAY_UUID = '11111111-2222-3333-4444-555555555555'
KEY = 'ssh-ed25519 ' + base64.b64encode(
    struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))
).decode() + '\n'

MOCK = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
r=Path(os.environ['N1_FIXTURE_ROOT']); case=os.environ['N1_FIXTURE_CASE']
name=Path(sys.argv[0]).name; args=sys.argv[1:]
with (r/'events.jsonl').open('a') as f: f.write(json.dumps({'command':name,'args':args})+'\n')

def arg(x): return str(r/x.lstrip('/'))
if name=='readlink':
    assert args[0]=='-f'
    print(os.path.realpath(args[1]));sys.exit(0)
if name=='blkid':
    key=args[args.index('-s')+1]; dev=Path(args[-1]).name
    values={'sda1':{'PARTUUID':'1234abcd-01','TYPE':'vfat','UUID':'1234-ABCD'},
            'sda2':{'PARTUUID':'1234abcd-02','TYPE':'squashfs','UUID':'root-filesystem'},
            'sda3':{'PARTUUID':'1234abcd-03','TYPE':'ext4','UUID':'11111111-2222-3333-4444-555555555555'}}
    if case=='root_blkid_error' and dev=='sda2':sys.exit(2)
    if case=='wrong_actual_root_uuid' and dev=='sda2' and key=='PARTUUID':print('87654321-02');sys.exit(0)
    if case=='wrong_overlay_uuid' and dev=='sda3' and key=='UUID':print('wrong-uuid');sys.exit(0)
    if case=='wrong_overlay_type' and dev=='sda3' and key=='TYPE':print('ntfs');sys.exit(0)
    print(values.get(dev,{}).get(key,''));sys.exit(0)
if name=='mount':
    target=args[-1]
    if '--move' in args:
        if case=='move_proc_fail' and args[-2]==arg('/proc'):sys.exit(1)
        if case=='move_dev_fail' and args[-2]==arg('/rom/dev'):sys.exit(1)
        sys.exit(0)
    kind=args[args.index('-t')+1];opts=args[args.index('-o')+1];source=args[-2]
    if kind=='vfat' and case=='boot_mount_fail':sys.exit(1)
    if kind=='ext4' and 'ro' in opts.split(',') and case=='precheck_mount_fail':sys.exit(1)
    if kind=='ext4' and 'rw' in opts.split(',') and case=='rw_mount_fail':sys.exit(1)
    if kind=='overlay' and case=='overlay_merge_fail':sys.exit(1)
    if kind=='ext4' and 'rw' in opts.split(','):
        (r/'overlay-mount.txt').write_text(source+' '+target+' ext4 '+opts+' 0 0\n')
    sys.exit(0)
if name=='umount':
    if case=='boot_unmount_fail' and args[-1]==arg('/tmp/n1-bt-boot'):sys.exit(1)
    if case=='precheck_unmount_fail' and args[-1]==arg('/overlay'):sys.exit(1)
    sys.exit(0)
if name=='pivot_root':
    if case=='pivot_fail':sys.exit(1)
    mounts=(r/'overlay-mount.txt').read_text()
    if case=='late_wrong_overlay':mounts=mounts.replace(arg('/dev/sda3'),arg('/dev/sdb3'))
    if case=='late_readonly_overlay':mounts=mounts.replace('rw,noatime','ro,noatime')
    root='overlayfs:/overlay / overlay rw 0 0\n'
    if case=='late_missing_overlay_root':root=arg('/dev/sda2')+' / squashfs ro 0 0\n'
    (r/'proc/mounts').write_text(mounts+root)
    sys.exit(0)
raise SystemExit('Unexpected mock command: '+name)
'''


def rewritten(source, root):
    # Do not permit a later production change to bypass the fake command PATH.
    assert not re.search(r'/(?:sbin|bin|usr/sbin)/(?:mount|umount|blkid|pivot_root)\b', source)
    source = re.sub(r'n1_stop\(\) \{.*?\n\}',
                    'n1_stop() { printf "%s\\n" "$*" >"$N1_FIXTURE_ROOT/stop.txt"; exit 42; }',
                    source, count=1, flags=re.S)
    # One pass avoids recursively replacing the /tmp prefix in the fixture path.
    return re.sub(r'(?<![A-Za-z0-9_@./-])/(?:sys|proc|dev|etc|tmp|overlay|mnt|rom|lib)(?=/|[\s"\',;)]|$)',
                  lambda m: str(root)+m.group(), source)


class GuardTests(unittest.TestCase):
    def run_case(self, case, expected_stop=None, allow_rw=False):
        with tempfile.TemporaryDirectory(prefix='n1-guard-fixture-') as directory:
            root = Path(directory)
            def put(name, data):
                path=root/name.lstrip('/');path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(data);return path
            def link(name, target):
                path=root/name.lstrip('/');path.parent.mkdir(parents=True,exist_ok=True)
                path.symlink_to(root/target.lstrip('/'),target_is_directory=True)
            for name in ('dev','etc','tmp','overlay','mnt','rom','bin','lib','proc/self','sys/class/block','sys/dev/block'):
                (root/name).mkdir(parents=True,exist_ok=True)
            disk='/sys/devices/platform/usb1/1-1/sda'
            (root/disk.lstrip('/')).mkdir(parents=True)
            link('/sys/class/block/sda',disk)
            for n in (1,2,3):
                put(f'{disk}/sda{n}/partition',f'{n}\n')
                link(f'/sys/class/block/sda{n}',f'{disk}/sda{n}')
                put(f'/dev/sda{n}','')
            link('/sys/dev/block/8:2',f'{disk}/sda2')
            put('/proc/self/mountinfo','1 0 8:2 / / ro - squashfs /dev/sda2 ro\n')
            put('/proc/cmdline',f'console=tty1 root=PARTUUID={PARTUUID} ro rootwait\n')
            put('/proc/mounts',f'{root}/dev/sda2 / squashfs ro 0 0\n')
            put('/etc/n1-bt-partuuid',PARTUUID+'\n')
            put('/etc/n1-bt-overlay-uuid',OVERLAY_UUID+'\n')
            put('/sys/firmware/devicetree/base/soc/apb@d0000000/mmc@74000/status','disabled\0')
            put('/tmp/n1-bt-boot/N1-BT-IDENTITY',PARTUUID+'\n')
            put('/tmp/n1-bt-boot/rescue-authorized_keys',KEY)
            put('/overlay/.n1-bt-identity',PARTUUID+'\n')
            put('/dev/console','')
            if case=='internal_root':
                (root/'sys/dev/block/8:2').unlink()
                put('/sys/devices/platform/mmc/mmcblk1/mmcblk1p2/partition','2\n')
                link('/sys/dev/block/8:2','/sys/devices/platform/mmc/mmcblk1/mmcblk1p2')
            if case=='non_usb_bus':
                (root/'sys/dev/block/8:2').unlink()
                put('/sys/devices/platform/ata/sda/sda2/partition','2\n')
                link('/sys/dev/block/8:2','/sys/devices/platform/ata/sda/sda2')
            if case in ('extra_mmc','second_usb'):
                name='mmcblk1' if case=='extra_mmc' else 'sdb'
                (root/f'sys/devices/{name}').mkdir(parents=True)
                link('/sys/class/block/'+name,'/sys/devices/'+name)
            if case=='wrong_root_argument':put('/proc/cmdline','root=PARTUUID=ffffffff-02 ro\n')
            if case=='wrong_root_partition':put(f'{disk}/sda2/partition','1\n')
            if case in ('wrong_overlay_parent','wrong_boot_parent'):
                n=3 if case=='wrong_overlay_parent' else 1
                (root/f'sys/class/block/sda{n}').unlink()
                put(f'/sys/devices/platform/usb2/sdz/sda{n}/partition',f'{n}\n')
                link(f'/sys/class/block/sda{n}',f'/sys/devices/platform/usb2/sdz/sda{n}')
            if case=='wrong_dt_status':put('/sys/firmware/devicetree/base/soc/apb@d0000000/mmc@74000/status','okay\0')
            if case=='missing_key':(root/'tmp/n1-bt-boot/rescue-authorized_keys').unlink()
            if case=='wrong_key':put('/tmp/n1-bt-boot/rescue-authorized_keys','ssh-rsa AAAA\n')
            if case=='multiple_keys':put('/tmp/n1-bt-boot/rescue-authorized_keys',KEY+KEY)
            if case=='oversized_key':put('/tmp/n1-bt-boot/rescue-authorized_keys','x'*16384)
            if case=='wrong_boot_identity':put('/tmp/n1-bt-boot/N1-BT-IDENTITY','ffffffff-02\n')
            if case=='wrong_overlay_identity':put('/overlay/.n1-bt-identity','ffffffff-02\n')
            guard=rewritten((HERE/'guard.sh').read_text(),root)
            overlay=rewritten((HERE/'mount_root.sh').read_text(),root)
            put('/lib/n1-bt-guard.sh',guard)
            mount_script=put('/lib/mount_root-fixture.sh',overlay)
            driver=put('/bin/fixture-command',MOCK);driver.chmod(0o700)
            for name in ('mount','umount','blkid','pivot_root','readlink'):
                (root/'bin'/name).symlink_to(driver)
            env=dict(os.environ,PATH=str(root/'bin')+os.pathsep+os.environ['PATH'],
                     N1_FIXTURE_ROOT=str(root),N1_FIXTURE_CASE=case)
            shell=os.environ.get('N1_GUARD_TEST_SHELL','bash')
            # mount_root runs as a child because the production script intentionally exits.
            command=f'set -e\n. "{root}/lib/n1-bt-guard.sh"\nn1_early\n{shell} "{mount_script}" start\nn1_overlay\n'
            result=subprocess.run([shell],input=command,text=True,capture_output=True,env=env,timeout=12)
            events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()] if (root/'events.jsonl').exists() else []
            rw=[e for e in events if e['command']=='mount' and '-o' in e['args'] and 'rw' in e['args'][e['args'].index('-o')+1].split(',')]
            self.assertEqual(result.returncode,42 if expected_stop else 0,(case,result.stderr,result.stdout,(root/'stop.txt').read_text() if (root/'stop.txt').exists() else '', events))
            if expected_stop:
                self.assertEqual((root/'stop.txt').read_text().strip(),expected_stop,case)
                self.assertFalse((root/'etc/dropbear/authorized_keys').exists(),case)
            else:
                self.assertEqual((root/'etc/dropbear/authorized_keys').read_text(),KEY)
                self.assertEqual(len(rw),1)
                ext4=[e for e in events if e['command']=='mount' and '-t' in e['args'] and e['args'][e['args'].index('-t')+1]=='ext4']
                self.assertEqual([e['args'][e['args'].index('-o')+1] for e in ext4],['ro,noload','rw,noatime'])
            if not allow_rw:self.assertFalse(rw,(case,'unsafe RW mount attempted',rw))
            for e in rw:self.assertEqual(e['args'][-2],str(root/'dev/sda3'))

    def test_valid_usb_root_and_overlay(self):self.run_case('valid',allow_rw=True)


CASES = {
 'internal_root':('root-not-usb-partition2',False),
 'non_usb_bus':('root-not-usb',False),
 'extra_mmc':('unexpected-block-device mmcblk1',False),
 'second_usb':('unexpected-block-device sdb',False),
 'wrong_root_argument':('root-argument',False),
 'wrong_actual_root_uuid':('root-partuuid',False),
 'root_blkid_error':('root-partuuid',False),
 'wrong_root_partition':('root-partition',False),
 'wrong_overlay_parent':('partition-parent',False),
 'wrong_boot_parent':('partition-parent',False),
 'wrong_dt_status':('emmc-dtb',False),
 'missing_key':('missing-public-key',False),
 'wrong_key':('key-format',False),
 'multiple_keys':('key-count',False),
 'oversized_key':('key-size',False),
 'wrong_boot_identity':('boot-identity',False),
 'boot_mount_fail':('boot-mount',False),
 'boot_unmount_fail':('boot-unmount',False),
 'wrong_overlay_uuid':('overlay-uuid',False),
 'wrong_overlay_type':('overlay-type',False),
 'wrong_overlay_identity':('overlay-identity',False),
 'precheck_mount_fail':('overlay-precheck-mount',False),
 'precheck_unmount_fail':('overlay-precheck-unmount',False),
 'rw_mount_fail':('overlay-mount',True),
 'overlay_merge_fail':('overlay-merge',True),
 'move_proc_fail':('move-proc',True),
 'pivot_fail':('pivot',True),
 'move_dev_fail':('move-dev',True),
 'late_wrong_overlay':('wrong-overlay',True),
 'late_readonly_overlay':('overlay-not-writable',True),
 'late_missing_overlay_root':('missing-overlay-root',True),
}
for case,(stop,rw) in CASES.items():
    def test(self,case=case,stop=stop,rw=rw):self.run_case(case,stop,rw)
    setattr(GuardTests,'test_'+case,test)

if __name__=='__main__':
    print('Scope: temporary-file shell harness; all mount/device operations mocked; not kernel or hardware validation.',flush=True)
    unittest.main()
