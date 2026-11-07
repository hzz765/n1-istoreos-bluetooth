import os
import shutil
import signal
import sys
import time

root = sys.argv[1]
while True:
    free = shutil.disk_usage(root).free
    print(f"Disk guard: {free // (1024 ** 3)} GiB free", flush=True)
    if free < 5 * 1024 ** 3:
        print("Disk reserve reached; terminating this build process group.", flush=True)
        os.killpg(os.getpgrp(), signal.SIGTERM)
        break
    time.sleep(30)
