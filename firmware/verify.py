"""Inspect a normal candidate offline; no target devices or mounts are used."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zlib
from prepare import HCD, SOURCE, BT_PACKAGES, attest_patch


def run(*args):
    return subprocess.check_output([str(x) for x in args],text=True).strip()

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        while b:=f.read(1024*1024): h.update(b)
    return h.hexdigest()

def script_body(data):
    assert len(data)>=72 and data[:4]==bytes.fromhex('27051956')
    head=bytearray(data[:64]); crc=int.from_bytes(head[4:8],'big');head[4:8]=bytes(4)
    assert zlib.crc32(head)==crc and data[30:32]==bytes([6,0])
    body=data[64:];assert len(body)==int.from_bytes(data[12:16],'big')
    assert zlib.crc32(body)==int.from_bytes(data[24:28],'big')
    assert body[4:8]==bytes(4) and int.from_bytes(body[:4],'big')==len(body)-8
    return body[8:]

def partitions(mbr):
    assert len(mbr)==512 and mbr[510:]==b'\x55\xaa'
    parts=[]
    for i in range(4):
        e=mbr[446+i*16:462+i*16]; start=int.from_bytes(e[8:12],'little'); size=int.from_bytes(e[12:],'little')
        if size:
            assert start and e[4];parts.append((start,size))
    assert len(parts)==3 and [s*512 for _,s in parts]==[64<<20,256<<20,2048<<20]
    assert all(a+n<=b for (a,n),(b,_) in zip(parts,parts[1:]))
    return parts

def main(root):
    source=root/'source';out=root/'public-artifacts';work=root/'candidate-inspect'
    work.mkdir(); boot=work/'boot';boot.mkdir()
    assert run('git','-C',source,'rev-parse','HEAD')==SOURCE
    # Only the reviewed kernel module change may alter tracked official files.
    assert run('git','-C',source,'diff','--name-only').splitlines()==['package/kernel/linux/modules/other.mk']
    attest_patch(source,root)
    klist=list((source/'build_dir').glob('target-*/linux-amlogic_meson/linux-6.6.144'));assert len(klist)==1
    kernel=klist[0];config=(kernel/'.config').read_text()
    for c in ['BT=m','BT_HCIUART=m','BT_HCIUART_BCM=y','BT_HCIUART_SERDEV=y','BT_BCM=m','SERIAL_DEV_BUS=y','SERIAL_DEV_CTRL_TTYPORT=y']:
        assert f'CONFIG_{c}\n' in config,c
    shutil.copyfile(kernel/'.config',out/'kernel.config')
    bconfig=(source/'.config').read_text()
    for p in BT_PACKAGES+['luci','dockerd','containerd']:
        assert f'CONFIG_PACKAGE_{p}=y\n' in bconfig,p
    images=list((source/'bin/targets/amlogic/meson').glob('*phicomm_n1*sysupgrade.img.gz'));assert len(images)==1
    original=images[0];raw=work/'image.raw';dec=zlib.decompressobj(16+zlib.MAX_WBITS)
    with original.open('rb') as src,raw.open('wb') as dst:
        while chunk:=src.read(1024*1024):
            dst.write(dec.decompress(chunk))
            if dec.eof:break
        assert dec.eof,'gzip truncated'
    with raw.open('rb') as f:
        parts=partitions(f.read(512))
        for n,(start,size) in enumerate(parts[:2],1):
            f.seek(start*512)
            with (work/f'p{n}').open('wb') as dst:
                remaining=size*512
                while remaining:
                    b=f.read(min(remaining,1024*1024));assert b,'partition truncated'
                    dst.write(b);remaining-=len(b)
        f.seek(parts[2][0]*512);assert f.read(8)==b'RESET000'
    subprocess.run(['mcopy','-i',str(work/'p1'),'::*',str(boot)],check=True)
    mapping={'aml_autoscript':'aml_autoscript.cmd','s905_autoscript':'s905_autoscript.cmd','emmc_autoscript':'emmc_autoscript.cmd','boot.scr':'amlogic.bootscript'}
    for name,src in mapping.items():
        assert script_body((boot/name).read_bytes())==(source/'target/linux/amlogic/image'/src).read_bytes(),name
    dtb=boot/'amlogic.dtb';uart='/soc/bus@c1100000/serial@84c0';bt=uart+'/bluetooth'
    def prop(node,key,typ='s'):return run('fdtget','-t',typ,dtb,node,key)
    assert prop(uart,'status')=='okay'
    assert 'uart-has-rtscts' in run('fdtget','-p',dtb,uart).splitlines()
    assert prop(bt,'compatible')=='brcm,bcm43438-bt'
    assert prop(bt,'max-speed','u')=='2000000' and prop(bt,'clock-names')=='lpo'
    # Resolve actual phandles against the decompiled tree's named pin groups.
    nodes=[]
    def walk(node):
        nodes.append(node)
        for child in run('fdtget','-l',dtb,node).splitlines():walk(node.rstrip('/')+'/'+child)
    walk('/')
    def phandle_of(predicate):
        matches=[n for n in nodes if predicate(n)]
        assert len(matches)==1,matches
        return prop(matches[0],'phandle','u')
    pins=prop(uart,'pinctrl-0','u').split()
    assert pins==[phandle_of(lambda n:n.endswith('/uart_a')),phandle_of(lambda n:n.endswith('/uart_a_cts_rts'))],pins
    assert prop(bt,'clocks','u')==phandle_of(lambda n:n=='/wifi32k')
    gpio=prop(bt,'shutdown-gpios','u').split();assert len(gpio)==3 and gpio[1:]==['96','0'],gpio
    assert gpio[0]==phandle_of(lambda n:n.endswith('/bus@c8834000/pinctrl@4b0/bank@4b0')),gpio
    emmc=prop('/aliases','mmc1')
    node=emmc
    while node:
        props=run('fdtget','-p',dtb,node).splitlines()
        if 'status' in props:assert prop(node,'status') in ('okay','ok'),node
        if node=='/':break
        node=node.rsplit('/',1)[0] or '/'
    fs=work/'root'
    subprocess.run(['sudo','unsquashfs','-d',str(fs),str(work/'p2')],check=True,stdout=subprocess.DEVNULL)
    assert sha(fs/'lib/firmware/brcm/BCM4345C0.hcd')==HCD
    assert not (fs/'lib/firmware/BCM4345C0.hcd').exists()
    for p in ['usr/bin/bluetoothctl','usr/bin/bluetoothd','usr/bin/btmgmt','usr/bin/btmon','usr/bin/dockerd','usr/sbin/uhttpd','www/cgi-bin/luci']:
        assert (fs/p).is_file(),p
    for p in ['etc/n1-bt-usb-test','etc/n1-bt-diagnostic','usr/sbin/n1-bt-check','lib/n1-bt-guard.sh','etc/dropbear/authorized_keys','root/.ssh/authorized_keys']:
        assert not (fs/p).exists(),p
    for name in ['BCM-LEGAL.txt','firmware-copyright.txt','candidate.json']:
        assert (fs/'usr/share/n1-bluetooth'/name).read_bytes()==(source/'files/usr/share/n1-bluetooth'/name).read_bytes()
    assert (fs/'lib/upgrade/platform.sh').read_bytes()==(source/'target/linux/amlogic/base-files/lib/upgrade/platform.sh').read_bytes()
    assert "DISTRIB_RELEASE='24.10.8'" in (fs/'etc/openwrt_release').read_text()
    mods=list((fs/'lib/modules').rglob('*.ko'));assert mods
    names={p.stem.replace('-','_') for p in mods}
    selected={n:next(p for p in mods if p.stem==n) for n in ['bluetooth','hci_uart','btbcm']}
    assert {p.parent.name for p in selected.values()}=={'6.6.144'}
    vermagic={run('modinfo','-F','vermagic',p) for p in selected.values()};assert len(vermagic)==1
    for n,p in selected.items():
        deps={d.replace('-','_') for d in run('modinfo','-F','depends',p).split(',') if d}
        assert deps<=names,(n,deps-names)
    metadata=work/'metadata.json'
    subprocess.run([str(source/'staging_dir/host/bin/fwtool'),'-i',str(metadata),str(original)],check=True)
    meta=json.loads(metadata.read_text());assert 'phicomm,n1' in meta['supported_devices'],meta
    # All packages originate from this same public source build; never ingest local device state.
    pkgout=out/'packages';pkgout.mkdir()
    for p in sorted((source/'bin').rglob('*.ipk')):
        dest=pkgout/p.name
        if dest.exists():assert sha(dest)==sha(p)
        else:shutil.copyfile(p,dest)
    assert list(pkgout.glob('kmod-bluetooth_*.ipk'))
    (out/'package-manifest.json').write_text(json.dumps({p.name:sha(p) for p in sorted(pkgout.iterdir())},indent=2)+'\n')
    shutil.copyfile(original,out/'n1-istoreos-24.10.8-bluetooth-CANDIDATE.img.gz')
    report={'status':'offline checks passed; installation NOT approved','source':SOURCE,'kernel_abi':(kernel/'.vermagic').read_text().strip(),'module_vermagic':list(vermagic),'hcd_sha256':HCD,'metadata':meta,'partitions_sectors':parts,'emmc':emmc,'boot_hashes':{p.name:sha(p) for p in boot.iterdir() if p.is_file()},'modules':{str(p.relative_to(fs)):sha(p) for p in selected.values()},'microphone':'unresolved; no claim that userspace alone fixes it'}
    (out/'candidate-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    (out/'verification.txt').write_text('PASS: normal N1 candidate offline image checks. NOT installation approval.\n')
    files=[p for p in out.rglob('*') if p.is_file() and p.name not in ('SHA256SUMS','build.log')]
    (out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(out))+'\n' for p in sorted(files)))
    print(report['status'])

if __name__=='__main__': main(Path(sys.argv[1]).resolve())
