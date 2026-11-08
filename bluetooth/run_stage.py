#!/usr/bin/env python3
"""Report stage boundaries; stop only this stage's process group below 5 GiB."""
import os
import shutil
import signal
import subprocess
import sys
import time

reserve = 5 * 1024**3
label, *command = sys.argv[1:]
def space():
    free = shutil.disk_usage('.').free
    st = os.statvfs('.')
    return free, st.f_favail
free, inodes = space()
print(f'Stage {label}: starting; {free // 1024**2} MiB free, {inodes} inodes free', flush=True)
if free < reserve:
    raise SystemExit('STOP: less than 5 GiB reserve; no command started')
start = time.monotonic()
process = subprocess.Popen(command, start_new_session=True)
while process.poll() is None:
    if space()[0] < reserve:
        print('STOP: 5 GiB disk reserve reached', flush=True)
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise SystemExit(1)
    time.sleep(2)
free, inodes = space()
print(f'Stage {label}: exit {process.returncode}; {int(time.monotonic()-start)} seconds; {free // 1024**2} MiB free, {inodes} inodes free', flush=True)
raise SystemExit(process.returncode)
