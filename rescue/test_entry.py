#!/usr/bin/env python3
"""Run generated vendor entry through a shell command harness, not board emulation.
The legacy fs_read logic is compiled unchanged (loff_t renamed to avoid host conflicts),
with only the FAT I/O boundary mocked.
Bash parses the shared if/for syntax; this does not prove a vendor Hush configuration.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request
from entry_script import make_entry

REV = 'ff7d3afbb488794da734cfe0bf0060d8573de56d'
URL = f'https://raw.githubusercontent.com/khadas/u-boot/{REV}/fs/fs.c'
# Pin the complete reference file; do not silently test a different branch revision.
SOURCE_SHA = 'cb59606f01eba172311012948ee2bd6cb87d6f1da246cecc4790252c4a30d371'


def legacy_probe(tmp):
    cached = os.environ.get('N1_LEGACY_FS_SOURCE')
    source = Path(cached).read_bytes() if cached else urllib.request.urlopen(URL, timeout=60).read()
    assert hashlib.sha256(source).hexdigest() == SOURCE_SHA
    text = source.decode()
    begin = text.index('int fs_read(')
    function = text[begin:text.index('\nint fs_write(', begin)].replace('loff_t', 'n1_loff_t')
    prelude = r'''
#include <stdio.h>
#include <stdlib.h>
typedef unsigned long ulong;
typedef long long n1_loff_t;
static n1_loff_t actual_size;
static int fake_read(const char *n, void *b, n1_loff_t off, n1_loff_t len, n1_loff_t *actual) {
 *actual = (!len || len > actual_size) ? actual_size : len; return 0;
}
struct fstype_info { int (*read)(const char *,void *,n1_loff_t,n1_loff_t,n1_loff_t *); };
static struct fstype_info info={fake_read};
static int fs_type;
static struct fstype_info *fs_get_info(int t) {return &info;}
static void *map_sysmem(ulong a,n1_loff_t n) {return (void *)a;}
static void unmap_sysmem(void *p) {}
static void fs_close(void) {}
'''
    main = '''\nint main(int argc,char **argv) { n1_loff_t n;
actual_size=strtoll(argv[1],0,10);
return fs_read("fixture",0,0,strtoll(argv[2],0,10),&n) != 0;
}\n'''
    c = tmp/'legacy.c'; c.write_text(prelude+function+main)
    binary = tmp/'legacy'; subprocess.run(['cc',str(c),'-o',str(binary)],check=True)
    for size in (489800,540408):
        assert subprocess.run([str(binary),str(size),str(size+1)],stdout=subprocess.DEVNULL).returncode == 1
        assert subprocess.run([str(binary),str(size),str(size)],stdout=subprocess.DEVNULL).returncode == 0
    return binary


# RAM CRC behavior models both native and network byte order and the legacy LP64
# itest.l dereference. No block devices, sockets, private files or external command I/O.
MOCKS = r'''
set -f
filesize=deadbeef
usb() { test "$CASE" != usb_fail; }
setenv() { unset "$1"; }
fatsize() {
  test "$CASE" != no_fatsize || return 127
  test "$CASE" != size_error || return 1
  test "$CASE" != missing || return 1
  if test "$CASE" = second_disk && test "$2" != 1:1; then return 1; fi
  if test "$CASE" = stale_size; then return 0; fi
  filesize="$FILESIZE"
}
fatload() {
  test "$CASE" != load_error || return 1
  if test "$MODE" = legacy; then "$PROBE" "$ACTUAL" "$((16#$5))" || return 1; fi
  if test "$CASE" = stale_load; then return 0; fi
  filesize="$FILESIZE"
  test "$CASE" != short_read || filesize=1
}
mw.l() {
  test "$CASE" != memory_error || return 1
  test "$3" = 2 || test "$3" = 1 || exit 90
  "$PYTHON" "$RAM_HELPER" mw "$2" "$3"
}
crc32() {
  test "$CASE" != crc_error || return 1
  test "$CASE" != crc_no_write || return 0
  "$PYTHON" "$RAM_HELPER" crc "$EXPECTED_CRC"
}
itest.l() { "$PYTHON" "$RAM_HELPER" test "$3"; }
go() { echo GO; test "$CASE" = go_returns && return 0; exit 0; }
sleep() { echo STOP; exit 42; }
'''


RAM_HELPER = r'''import os, sys
from pathlib import Path
p=Path(os.environ['RAM_FILE']); ram=bytearray(p.read_bytes())
op=sys.argv[1]
if op=='mw':
    count=int(sys.argv[3]); ram[:count*4]=int(sys.argv[2],16).to_bytes(4,'little')*count
elif op=='crc':
    ram[:4]=int(sys.argv[2],16).to_bytes(4,'little')
elif op=='test':
    expected=int(sys.argv[2],16)
    # Both legitimate 32-bit and buggy legacy LP64 dereferences must agree.
    sys.exit(0 if int.from_bytes(ram[:4],'little') == expected and int.from_bytes(ram[:8],'little') == expected else 1)
else: raise AssertionError(op)
p.write_bytes(ram)
'''


def run():
    import zlib, sys
    count = 0
    with tempfile.TemporaryDirectory(prefix='n1-entry-test-') as directory:
        tmp = Path(directory); probe = legacy_probe(tmp)
        ram_helper = tmp/'ram.py'
        ram_helper.write_text(RAM_HELPER)

        # Original failed build sizes plus a small non-round fixture.
        fixtures = [bytes(range(256))*(n//256)+bytes(range(n%256)) for n in (489800,540408,257)]
        fixtures.append(bytes.fromhex('9d0ad96d'))  # CRC32 == 0: sentinel must not poison high word.
        for loader in fixtures:
            size = len(loader)
            crc = zlib.crc32(loader)
            cases = ['valid','truncated','oversized','corrupt','missing','no_fatsize',
                     'size_error','stale_size','load_error','stale_load','short_read',
                     'memory_error','crc_error','crc_no_write','usb_fail','go_returns','second_disk']
            for mode in ('legacy','modern'):
                for endian in ('native','swapped'):
                    for case in cases:
                        actual = size + (1 if case=='oversized' else -1 if case=='truncated' else 0)
                        actual_loader = (bytes([loader[0] ^ 1])+loader[1:]) if case=='corrupt' else loader
                        actual_crc = zlib.crc32(actual_loader)
                        expected_crc = actual_crc if endian=='native' else int.from_bytes(actual_crc.to_bytes(4,'big'),'little')
                        env = dict(os.environ, CASE=case,MODE=mode,PROBE=str(probe),
                                   ACTUAL=str(actual),FILESIZE=f'{actual:x}',EXPECTED_CRC=f'{expected_crc:x}',
                                   PYTHON=sys.executable,RAM_HELPER=str(ram_helper),RAM_FILE=str(tmp/'ram.bin'))
                        (tmp/'ram.bin').write_bytes(b'\xff'*8)
                        result = subprocess.run(['bash'],input=MOCKS+make_entry(loader),text=True,
                                                capture_output=True,env=env,timeout=5)
                        good = case in ('valid','second_disk')
                        assert result.returncode == (0 if good else 42), (case,mode,result)
                        assert result.stdout.splitlines().count('GO') == (1 if good or case=='go_returns' else 0), (case,result)
                        count += 1
    print(f'PASS: {count} generated-entry shell cases; 4 legacy fs_read checks (logic unchanged).')
    print('Limits: mocked command I/O, Bash rather than vendor Hush; no hardware or exact installed vendor identity proven.')
    print('Reference:', URL, SOURCE_SHA)


if __name__ == '__main__':
    run()
