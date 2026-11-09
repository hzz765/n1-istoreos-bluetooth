"""Prepare a normal N1 firmware candidate. Never accesses any device."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = '5fe2cddefc93d59156ca66740ed896ae954d8e8e'
FW_COMMIT = '3ae4cba9340fbd746f0ed896c836cd4ec95a4266'
HCD = '51c45e77ddad91a19e96dc8fb75295b2087c279940df2634b23baf71b6dea42c'
BT_PACKAGES = ['kmod-bluetooth', 'bluez-daemon', 'bluez-utils', 'bluez-utils-extra', 'bluez-utils-btmon']

def config(text):
    changes = {f'CONFIG_PACKAGE_{p}': 'y' for p in BT_PACKAGES}
    changes['CONFIG_VERSION_NUMBER'] = '"24.10.8"'
    changes['CONFIG_VERSION_CODE'] = '"n1-bt-candidate"'
    lines = [line for line in text.splitlines() if not any(
        line.startswith(k+'=') or line == f'# {k} is not set' for k in changes)]
    return '\n'.join(lines + [f'{k}={v}' for k,v in changes.items()])+'\n'

def attest_patch(source, repo):
    changed='package/kernel/linux/modules/other.mk'
    added='target/linux/amlogic/patches-6.6/006-n1-uart-bluetooth.patch'
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp)
        p=base/changed;p.parent.mkdir(parents=True)
        p.write_bytes(subprocess.check_output(['git','show',f'{SOURCE}:{changed}'],cwd=source))
        subprocess.run(['patch','-p1','-i',str(repo/'patches/01-n1-bluetooth.patch')],cwd=base,check=True,stdout=subprocess.DEVNULL)
        for name in (changed,added):
            assert (base/name).read_bytes()==(source/name).read_bytes(),name
    new=subprocess.check_output(['git','ls-files','--others','--exclude-standard','target/linux/amlogic'],cwd=source,text=True).splitlines()
    assert new==[added],new


def main(source, repo):
    assert subprocess.check_output(['git','rev-parse','HEAD'], cwd=source,text=True).strip() == SOURCE
    assert not (source/'target/linux/amlogic/patches-6.6/007-n1-usb-test-disable-emmc.patch').exists()
    patch = repo/'patches/01-n1-bluetooth.patch'
    for args in [['--check'], []]:
        subprocess.run(['git','apply',*args,str(patch)],cwd=source,check=True)
    attest_patch(source,repo)
    p=source/'.config'; p.write_text(config(p.read_text()))
    files=source/'files'; assert not files.exists(), 'Candidate requires a clean overlay input'
    fw=files/'lib/firmware/brcm'; fw.mkdir(parents=True)
    notices=files/'usr/share/n1-bluetooth'; notices.mkdir(parents=True)
    inputs = [
        ('broadcom/BCM4345C0.hcd',fw/'BCM4345C0.hcd',HCD),
        ('broadcom/BCM-LEGAL.txt',notices/'BCM-LEGAL.txt','092aaac7a0743f6e98ef224dfd68cac7554aa267581121fadcace8599b7d29dd'),
        ('debian/copyright',notices/'firmware-copyright.txt','611b9f690d41e3cb200d66b1c9c5b23172be40d1f6ca4e59efb2b8232c476ad3')]
    for name,dest,digest in inputs:
        url=f'https://raw.githubusercontent.com/RPi-Distro/bluez-firmware/{FW_COMMIT}/{name}'
        subprocess.run(['curl','--fail','--location','--retry','3','--max-time','120',url,'-o',str(dest)],check=True)
        assert hashlib.sha256(dest.read_bytes()).hexdigest()==digest, name
    record={'source':SOURCE,'hcd_commit':FW_COMMIT,'hcd_sha256':HCD,
            'scope':'Uninstalled normal firmware candidate; NOT an isolated USB image. HFP microphone unresolved.',
            'emmc':'official enabled configuration; installation review pending',
            'audio':'optional userspace packages deferred; no automatic recording or pairing'}
    (notices/'candidate.json').write_text(json.dumps(record,indent=2)+'\n')
    (repo/'public-artifacts/candidate-scope.txt').write_text(
        'CUSTOM N1 iStoreOS 24.10.8 Bluetooth candidate. Not an official release.\n'
        'Normal eMMC-enabled image with original upstream boot/update scripts, including persistent environment operations.\n'
        'DO NOT boot as an isolated USB, flash, or use firmware update until a separate installation/backup review.\n'
        'Building does not access N1. Does not promise original kernel ABI compatibility or data-preserving upgrade.\n'
        'HFP microphone remains unresolved; later userspace installation may not alone resolve it.\n')

if __name__=='__main__': main(Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve())
