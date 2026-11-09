"""Audit userspace packages against the reviewed USB root; cloud regular files only."""
import io,json,hashlib,pathlib,shutil,subprocess,tarfile,re,os,sys
P=pathlib.Path
ROOT=P('audio-audit'); BASE=P('audio-base/n1-hcd-control.img.gz'); PKGS=P('audio-input/packages')
def need(c,m):
 if not c: raise RuntimeError(m)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def fields(text):
 d={}
 for l in text.splitlines():
  if ': ' in l and not l.startswith(' '):k,v=l.split(': ',1);d[k]=v
 return d
def ipk(p):
 raw=p.read_bytes()
 if raw.startswith(b'!<arch>\n'):
  pos=8;parts={}
  while pos<len(raw):
   h=raw[pos:pos+60];need(len(h)==60 and h[58:]==b'`\n','Bad ar');n=h[:16].decode().strip().rstrip('/');size=int(h[48:58]);pos+=60;parts[n]=raw[pos:pos+size];pos+=size+(size%2)
 else:
  with tarfile.open(fileobj=io.BytesIO(raw)) as t:parts={m.name.removeprefix('./'):t.extractfile(m).read() for m in t if m.isfile()}
 need('control.tar.gz' in parts and 'data.tar.gz' in parts,'Unsupported IPK')
 with tarfile.open(fileobj=io.BytesIO(parts['control.tar.gz'])) as t:
  cs={m.name.removeprefix('./'):t.extractfile(m).read() for m in t if m.isfile()}
 return fields(cs['control'].decode()),parts['data.tar.gz'],cs
need(not ROOT.exists(),'Fresh audit directory required');ROOT.mkdir()
need(sha(BASE)=='9d3cffaad40b8dd06dd9499190a1c70442a9449e899b6bd3abce014fc9e179fc','Baseline gzip changed')
import gzip
raw=ROOT/'baseline.img'
with gzip.open(BASE,'rb') as src,raw.open('wb') as out:shutil.copyfileobj(src,out)
need(sha(raw)=='73b609ec81aeb68eb4c1acc2099b1188c0f91abc5fcb88f7d8ca2ad1a955817e','Baseline raw changed')
with raw.open('rb') as f:f.seek(68157440);(ROOT/'root.squashfs').write_bytes(f.read(27262976))
root=ROOT/'root';subprocess.run(['unsquashfs','-d',str(root),str(ROOT/'root.squashfs')],check=True)
installed={};providers={}
for block in (root/'usr/lib/opkg/status').read_text().split('\n\n'):
 d=fields(block)
 if not d.get('Package'):continue
 installed[d['Package']]=d
 for n in [d['Package']]+[x.strip().split(' ')[0] for x in d.get('Provides','').split(',') if x.strip()]:providers[n]=d
packages={};available={}
for p in sorted(PKGS.glob('*.ipk')):
 d,data,cs=ipk(p);n=d['Package'];need(not n.startswith('kmod-') and n!='kernel','Kernel package forbidden')
 need(d['Architecture'] in ('all','aarch64_cortex-a53'),'Wrong architecture')
 packages[n]=(p,d,data,cs)
 for v in [n]+[x.strip().split(' ')[0] for x in d.get('Provides','').split(',') if x.strip()]:available[v]=n
selected=set();baseline_dependencies={}
def resolve(n):
 if n in providers:
  old=providers[n];candidate=available.get(n)
  if candidate:need(packages[candidate][1]['Version']==old['Version'],'Existing dependency version differs: '+n)
  baseline_dependencies[n]=old['Version'];return
 need(n in available,'Missing dependency '+n)
 n=available[n]
 if n in selected:return
 selected.add(n);d=packages[n][1]
 for dep in d.get('Depends','').split(','):
  if not dep.strip():continue
  need('|' not in dep and '(' not in dep,'Review versioned/alternative dependency manually: '+dep)
  resolve(dep.strip())
for n in ('n1-bluealsa','n1-bluealsa-utils','alsa-utils'):resolve(n)
records=[];scripts={}
for n in sorted(selected):
 p,d,data,cs=packages[n];scripts[n]={k:v.decode(errors='replace') for k,v in cs.items() if k!='control'}
 with tarfile.open(fileobj=io.BytesIO(data)) as t:
  for m in t:
   name=m.name.removeprefix('./');rel=P(name)
   need(not rel.is_absolute() and '..' not in rel.parts,'Unsafe package path')
   if name in ('','.'):continue
   need(rel.parts[0] in ('usr','etc','lib'),'Unexpected top-level path: '+name)
   need(not name.startswith(('lib/modules','lib/firmware','etc/init.d','etc/rc.d','etc/hotplug.d')),'Forbidden path: '+name)
   dst=root/rel
   need(m.isdir() or m.isfile() or m.issym(),'Unexpected package entry')
   # No writes via pre-existing parent symlinks.
   for parent in list(rel.parents)[:-1]:need(not (root/parent).is_symlink(),'Symlink parent: '+name)
   if m.isdir():dst.mkdir(parents=True,exist_ok=True);continue
   if rel.parts[0]=='etc':need(name in ('etc/alsa/conf.d/20-bluealsa.conf','etc/dbus-1/system.d/bluealsa.conf'),'Unreviewed etc file: '+name)
   need(not dst.exists() and not dst.is_symlink(),'Would replace existing file: '+name)
   dst.parent.mkdir(parents=True,exist_ok=True)
   if m.issym():
    need(not P(m.linkname).is_absolute(),'Absolute symlink not accepted')
    resolved=(dst.parent/m.linkname).resolve();need(resolved.is_relative_to(root.resolve()),'Escaping symlink')
    dst.symlink_to(m.linkname);records.append({'path':name,'symlink':m.linkname})
   else:
    dst.write_bytes(t.extractfile(m).read());dst.chmod(m.mode & 0o777);records.append({'path':name,'sha256':sha(dst),'mode':m.mode & 0o777})
shutil.copyfile('/usr/bin/qemu-aarch64-static',root/'tmp/qemu');(root/'tmp/qemu').chmod(0o755)
checks={}
for command in [['/usr/bin/bluealsad','--help'],['/usr/bin/bluealsactl','--help'],['/usr/bin/aplay','--version'],['/usr/bin/arecord','--version']]:
 r=subprocess.run(['chroot',str(root),'/tmp/qemu']+command,capture_output=True,text=True,timeout=30);need(r.returncode==0,'ARM runtime check failed: '+str(command)+r.stderr);checks[command[0]]=r.stdout+r.stderr
# Exercise both ALSA plugin entry points; lack of D-Bus is intentional offline.
for label,command in [('pcm',['/usr/bin/aplay','-D','bluealsa:DEV=00:00:00:00:00:01,PROFILE=a2dp','-t','raw','/tmp/empty.raw']),('ctl',['/usr/bin/amixer','-D','bluealsa','info'])]:
 (root/'tmp/empty.raw').write_bytes(b'')
 r=subprocess.run(['chroot',str(root),'/tmp/qemu']+command,capture_output=True,text=True,timeout=30)
 text=r.stdout+r.stderr
 need(r.returncode!=0 and "Couldn't initialize D-Bus context" in text,'ALSA plugin did not reach expected offline D-Bus failure: '+label+text)
 need(not any(x in text for x in ['Error relocating','symbol not found','cannot open shared object']),'Plugin dependency error')
 checks['plugin_'+label]=text
(root/'tmp/empty.raw').unlink()
(root/'tmp/qemu').unlink()
need('hfp-ag' in checks['/usr/bin/bluealsad'] and 'a2dp-source' in checks['/usr/bin/bluealsad'],'Required profiles absent')
license=P('audio/BlueALSA-LICENSE')
need(sha(license)=='47a67fe6b44d4aff90c837ebce38814bf89fdf5efd6bc6c9c078f960c35f61b8','BlueALSA license changed')
license_rel='usr/share/licenses/n1-bluealsa/LICENSE';dest=root/license_rel
need(not dest.exists(),'License collision');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(license,dest)
records.append({'path':license_rel,'sha256':sha(dest),'mode':0o644})
out=P('audio-validated');out.mkdir()
with tarfile.open(out/'audio-files.tar.gz','w:gz') as t:
 for record in records:t.add(root/record['path'],arcname=record['path'],recursive=False)
report={'status':'PASS_OFFLINE_USERSPACE','baseline_raw_sha256':sha(raw),'packages':{n:{'version':packages[n][1]['Version'],'ipk_sha256':sha(packages[n][0])} for n in sorted(selected)},'baseline_dependencies':baseline_dependencies,'files':records,'arm_checks':checks,'limits':'No device execution or audio success claim; no package maintainer scripts executed. Runtime USB preflight and deployment review still required.'}
(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');(out/'package-scripts.json').write_text(json.dumps(scripts,indent=2)+'\n')
(out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in sorted(out.iterdir()) if p.is_file()))
print('PASS: new userspace files only; existing BlueZ, libc, kernel, firmware and boot untouched; ARM tools execute against original root.')
