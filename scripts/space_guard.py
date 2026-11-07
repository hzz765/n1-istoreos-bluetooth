import os
import shutil
import signal
import sys
import time

root = sys.argv[1]
warned = False
while True:
    free = shutil.disk_usage(root).free
    if free < 10 * 1024 ** 3 and not warned:
        print("Disk guard: under 10 GiB free; approaching the 5 GiB stop reserve.", flush=True)
        warned = True
    if free < 5 * 1024 ** 3:
        print("Disk reserve reached; terminating this build process group.", flush=True)
        os.killpg(os.getpgrp(), signal.SIGTERM)
        break
    time.sleep(30)
