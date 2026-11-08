"""Repack public, pinned OpenWrt bytes; never open a host block device."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('rescue').resolve()))
from package import checksum, extract_region, run, script_text, BASE_SHA
from entry_script import make_entry
import gzip, json, secrets, shutil, struct, subprocess, uuid, zlib
W=Path('rescue-work'); B=Path('base-artifact'); O=Path('bluetooth-output')
def call(*args): subprocess.run([str(x) for x in args],check=True)
def write(path,text,mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text); path.chmod(mode)
def prepare():
    assert checksum(B/'n1-usb-test.img.gz')==BASE_SHA
    assert checksum(B/'kernel.config')=='3185c23daa3b69ce80e16b5d78bf7c6af8befe838d410b3b8185359d12b800cc'
    W.mkdir(exist_ok=False)
    sig=secrets.token_hex(4)
    assert sig not in ['00000000','ffffffff','1234abcd']
    write(W/'signature.txt',sig+'\n'); write(W/'boot-profile.json',json.dumps({'mode':'bluetooth'}))
    d=zlib.decompressobj(31)
    with (B/'n1-usb-test.img.gz').open('rb') as src,(W/'base.img').open('wb') as dst:
        while chunk:=src.read(1048576): dst.write(d.decompress(chunk))
        assert d.eof
    mbr=(W/'base.img').open('rb').read(512); assert mbr[510:]==b'\x55\xaa'
    for i,name in enumerate(['boot.fat','root.squashfs']):
        start,size=struct.unpack_from('<II',mbr,454+i*16); extract_region(W/'base.img',W/name,start*512,size*512)
    boot=W/'boot'; boot.mkdir()
    for name in ['kernel.img','amlogic.dtb']: call('mcopy','-i',W/'boot.fat','::/'+name,boot/name)
    call('unsquashfs','-d',W/'root',W/'root.squashfs')
    root=W/'root'
    hashes={str(p.relative_to(root)):checksum(p) for p in (root/'lib/modules').rglob('*') if p.is_file() and not p.is_symlink()}
    write(W/'module-hashes.json',json.dumps(hashes,sort_keys=True))
    write(W/'kernel-sha.txt',checksum(boot/'kernel.img'))
    write(W/'dtb-sha.txt',checksum(boot/'amlogic.dtb'))
    hcd=list((root/'lib/firmware').rglob('BCM4345C0.hcd'))
    assert len(hcd)==1 and checksum(hcd[0])=='8bbf245399e66f68304ba0f9185159dd1d557c48b534f2e7be75e2e0f4cb4a9a'
    dtb=boot/'amlogic.dtb'; node=run('fdtget','-t','s',dtb,'/aliases','mmc1')
    assert run('fdtget','-t','s',dtb,node,'status')=='disabled'
    assert run('fdtget','-t','s',dtb,'/soc/bus@c1100000/serial@84c0','status')=='okay'
    overlay_uuid=str(uuid.uuid4()); write(W/'overlay-uuid',overlay_uuid)
    write(root/'etc/n1-bt-partuuid',sig+'-02\n'); write(root/'etc/n1-bt-overlay-uuid',overlay_uuid+'\n')
    write(boot/'N1-BT-IDENTITY',sig+'-02\n')
    write(boot/'README.txt','USB Bluetooth test only. Add local Ed25519 PUBLIC key as rescue-authorized_keys. Never use firmware update or install to internal disk.\n')
    shutil.copyfile('bluetooth/guard.sh',root/'lib/n1-bt-guard.sh')
    for p in ['usr/sbin/mount_root','sbin/mount_root']:
        (root/p).unlink(); shutil.copyfile('bluetooth/mount_root.sh',root/p); (root/p).chmod(0o755)
    write(root/'lib/preinit/00_n1_guard','. /lib/n1-bt-guard.sh\npi_preinit_no_failsafe=y\nboot_hook_add preinit_essential n1_early\nboot_hook_add preinit_mount_root n1_overlay\n')
    for name in ['79_move_config','10_indicate_failsafe','10_indicate_preinit','30_failsafe_wait','40_run_failsafe_hook','99_10_failsafe_dropbear','99_10_failsafe_login','99_11_failsafe_dropbear_quick']:
        (root/'lib/preinit'/name).unlink(missing_ok=True)
    write(root/'lib/preinit/80_mount_root', 'n1_mount_root() { /usr/sbin/mount_root start; boot_run_hook preinit_mount_root; }\nboot_hook_add preinit_main n1_mount_root\n')
    # No first-boot disk discovery, package upgrades, or implicit external mounts.
    shutil.rmtree(root/'etc/uci-defaults'); (root/'etc/uci-defaults').mkdir()
    for p in ['etc/hotplug.d/block/09-blockmount','etc/hotplug.d/block/10-mount','usr/sbin/install-to-emmc.sh','sbin/sysupgrade','usr/libexec/blockmount.sh','usr/sbin/fw_setenv','usr/bin/fw_setenv','sbin/firstboot','sbin/jffs2reset']:
        (root/p).unlink(missing_ok=True)
    allowed={'sysfixtime','urngd','boot','system','sysctl','log','dropbear','network','dbus','bluetoothd','done','led','urandom_seed','umount'}
    for p in (root/'etc/rc.d').iterdir():
        if p.name[3:] not in allowed: p.unlink()
    write(root/'etc/config/network',"config interface 'loopback'\n option device 'lo'\n option proto 'static'\n option ipaddr '127.0.0.1'\n option netmask '255.0.0.0'\nconfig interface 'lan'\n option device 'eth0'\n option proto 'static'\n option ipaddr '192.168.77.1'\n option netmask '255.255.255.0'\n option delegate '0'\n")
    write(root/'etc/config/dropbear',"config dropbear\n option PasswordAuth 'off'\n option RootPasswordAuth 'off'\n option RootLogin 'on'\n option Interface 'lan'\n option Port '22'\n")
    write(root/'etc/config/fstab',"config global\n"+''.join(" option %s '0'\n"%x for x in ['auto_swap','auto_mount','anon_swap','anon_mount','port_mount']))
    write(root/'etc/rc.local', '#!/bin/sh\nexit 0\n', 0o755)
    write(root/'etc/crontabs/root', '')
    write(root/'etc/n1-bt-usb-test', 'Isolated Bluetooth USB; base run 42432191825; '+sig+'-02\n')
    shutil.copyfile('bluetooth/check.sh',root/'usr/sbin/n1-bt-check'); (root/'usr/sbin/n1-bt-check').chmod(0o755)
    write(root/'etc/config/wireless','')
    write(root/'etc/config/dhcp','')
    # No root password, private material, or build-time SSH identity is distributed.
    for p in (root/'etc/dropbear').glob('*'): p.unlink()
    overlay=W/'overlay-root'; overlay.mkdir(); (overlay/'upper').mkdir(); (overlay/'work').mkdir()
    write(overlay/'.n1-bt-identity',sig+'-02\n')
    call('truncate','-s','512M',W/'overlay.ext4')
    call('mkfs.ext4','-q','-F','-U',overlay_uuid,'-L','N1-BT-OVERLAY','-d',overlay,W/'overlay.ext4')
    print('Prepared isolated Bluetooth root and fresh USB-only overlay.')
def finish():
    root=W/'root'; boot=W/'boot'; sig=(W/'signature.txt').read_text().strip()
    assert checksum(boot/'kernel.img')==(W/'kernel-sha.txt').read_text()
    assert checksum(boot/'amlogic.dtb')==(W/'dtb-sha.txt').read_text()
    for name,h in json.loads((W/'module-hashes.json').read_text()).items(): assert checksum(root/name)==h,name
    loader=boot/'n1-rescue-loader.bin'
    assert checksum(loader)==json.loads((W/'loader-verification.json').read_text())['loader_sha256']
    entry=make_entry(loader.read_bytes()); write(W/'entry.cmd',entry)
    for name in ['boot.scr','aml_autoscript','s905_autoscript','emmc_autoscript']:
        call('mkimage','-A','arm','-O','linux','-T','script','-C','none','-a','0','-e','0','-d',W/'entry.cmd',boot/name)
        assert script_text(boot/name)==entry
    assert not (boot/'u-boot.emmc').exists()
    assert not (boot/'rescue-authorized_keys').exists()
    assert not any((root/'etc/dropbear').iterdir())
    assert not list(root.rglob('id_ed25519')) and not list(root.rglob('id_rsa'))
    assert not (root/'root/.ssh/authorized_keys').exists()
    call('mksquashfs',root,W/'test.squashfs','-noappend','-comp','xz','-b','256k','-all-root')
    call('mkfs.fat','-F','16','-i',sig,'-n','N1-BT-TEST','-C',W/'test.fat','65536')
    for p in sorted(boot.iterdir()): call('mcopy','-i',W/'test.fat',p,'::/')
    sizes=[131072,((W/'test.squashfs').stat().st_size+1048575)//1048576*2048,1048576]
    starts=[2048,2048+sizes[0],2048+sizes[0]+sizes[1]]
    mbr=bytearray(512);mbr[440:444]=bytes.fromhex(sig)[::-1];mbr[510:]=b'\x55\xaa'
    for i,(start,size) in enumerate(zip(starts,sizes)): struct.pack_into('<B3sB3sII',mbr,446+i*16,0,b'\xfe\xff\xff',14 if i==0 else 131,b'\xfe\xff\xff',start,size)
    disk=W/'n1-bluetooth.img'
    with disk.open('wb') as dst:
        dst.truncate((starts[-1]+sizes[-1])*512); dst.write(mbr)
        for start,name in zip(starts,['test.fat','test.squashfs','overlay.ext4']):
            dst.seek(start*512)
            with (W/name).open('rb') as src: shutil.copyfileobj(src,dst)
    for i,name in enumerate(['test.fat','test.squashfs','overlay.ext4']):
        target=W/('readback-'+name); extract_region(disk,target,starts[i]*512,(W/name).stat().st_size);assert checksum(target)==checksum(W/name)
    call('unsquashfs','-d',W/'verified-root',W/'readback-test.squashfs')
    verified=W/'verified-root'
    for p in ['lib/n1-bt-guard.sh','usr/sbin/mount_root','sbin/mount_root','lib/preinit/00_n1_guard','etc/config/network','etc/config/dropbear','etc/config/fstab']:
        assert checksum(verified/p)==checksum(root/p)
    for p,h in json.loads((W/'module-hashes.json').read_text()).items(): assert checksum(verified/p)==h
    assert run('blkid','-o','value','-s','UUID',W/'readback-overlay.ext4')==(W/'overlay-uuid').read_text()
    assert run('debugfs','-R','cat /.n1-bt-identity',W/'readback-overlay.ext4')==sig+'-02'
    O.mkdir(exist_ok=False)
    with disk.open('rb') as src,(O/'n1-bluetooth.img.gz').open('wb') as dst:
        with gzip.GzipFile(filename='',mode='wb',fileobj=dst,mtime=0) as gz: shutil.copyfileobj(src,gz)
    for name in ['loader.config','loader-verification.json','entry-tests.txt','boot-policy-tests.txt','module-hashes.json','guard-tests.txt','mount-help.txt','blkid-help.txt','pivot-help.txt']:shutil.copyfile(W/name,O/name)
    write(O/'verification.txt','STATIC CHECKS PASSED; HARDWARE BLUETOOTH/ROLLBACK NOT YET VERIFIED\nBase run 42432191825; base SHA256 '+BASE_SHA+'\nKernel/modules byte-identical; source loader has no MMC/NET/persistent environment.\nUSB-only preformatted overlay, no internal MMC controller; fail-closed preinit.\nRoot PARTUUID '+sig+'-02\nOverlay UUID '+(W/'overlay-uuid').read_text()+'\nUSB only. Never upload to firmware update. Public key must be installed locally.\n')
    shutil.copyfile('bluetooth/README.md', O/'README.md')
    shutil.copyfile(B/'kernel.config', O/'kernel.config')
    write(O/'build-source.txt', run('git', 'rev-parse', 'HEAD')+'\n')
    write(O/'SHA256SUMS',''.join(checksum(p)+'  '+p.name+'\n' for p in sorted(O.iterdir())))
if __name__=='__main__': {'prepare':prepare,'finish':finish}[sys.argv[1]]()
