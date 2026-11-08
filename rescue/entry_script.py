"""Vendor-stage script: exact bounded read, independent size query, CRC, fail closed."""
import zlib


def make_entry(loader):
    size = len(loader)
    assert 0 < size < 4 * 1024**2
    scratch = 0x01000000 + ((size + 4095) // 4096 + 1) * 4096
    crc = zlib.crc32(loader)
    swapped = int.from_bytes(crc.to_bytes(4, 'big'), 'little')
    sentinel = next(i for i in range(3) if i not in (crc, swapped))
    # fatsize validates the entire directory entry; exact read avoids legacy EOF failure.
    # A missing command, stale filesize, truncated/extended file or read failure must stop.
    return f'''echo "N1 rescue: USB-only volatile chainload"
if usb start; then
  for n1_usb in 0 1 2 3; do
    setenv filesize
    if fatsize usb ${{n1_usb}}:1 n1-rescue-loader.bin; then
      if test "${{filesize}}" = "{size:x}"; then
        setenv filesize
        if fatload usb ${{n1_usb}}:1 0x01000000 n1-rescue-loader.bin {size:x}; then
          if test "${{filesize}}" = "{size:x}"; then
            if mw.l {scratch:x} 0 2 && mw.l {scratch:x} {sentinel:x} 1; then
              if crc32 0x01000000 ${{filesize}} {scratch:x}; then
                if itest.l *{scratch:x} == {crc:x} || itest.l *{scratch:x} == {swapped:x}; then
                  go 0x01000000
                fi
              fi
            fi
          fi
        fi
      fi
      echo "N1 rescue: loader validation failed; STOP"
      while test 1 = 1; do sleep 1; done
    fi
  done
fi
echo "N1 rescue: no verified loader (fatsize required); STOP"
while test 1 = 1; do sleep 1; done
'''
