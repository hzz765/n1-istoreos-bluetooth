#!/usr/bin/python3
"""Unmount the backup cleanly before asking PID 1 to power off."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys


def shutdown():
    if len(sys.argv) != 1:
        raise RuntimeError('No force/options accepted; use plain poweroff')
    with open('/run/n1-backup.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.chdir('/')
        os.sync()
        mounted = subprocess.run(['mountpoint', '-q', '/run/backup'])
        if mounted.returncode == 0:
            subprocess.run(['umount', '/run/backup'], check=True)
        elif mounted.returncode != 32:
            raise RuntimeError('Cannot determine backup mount state')
        os.sync()
        marker = Path('/run/n1-shutting-down')
        marker.touch(exist_ok=False)
        try:
            subprocess.run(['/bin/busybox', 'poweroff'], check=True)
        except BaseException:
            marker.unlink()
            raise


if __name__ == '__main__':
    try:
        shutdown()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit('STOP: shutdown refused: ' + str(error))
