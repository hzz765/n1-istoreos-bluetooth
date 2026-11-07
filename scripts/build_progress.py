"""Every five minutes, report compiler activity without logging process arguments."""
import pathlib
import subprocess
import sys
import time

source = pathlib.Path(sys.argv[1])
while True:
    time.sleep(300)
    print('Build activity snapshot (not a success or liveness assertion):', flush=True)
    processes = subprocess.run(
        ['ps', '-eo', 'comm,pcpu,etimes', '--sort=-pcpu'],
        check=True, capture_output=True, text=True)
    print('\n'.join(processes.stdout.splitlines()[:9]), flush=True)
    for log in (source / 'build_dir/host').glob('llvm-project-*/build/.ninja_log'):
        with log.open('rb') as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 8192))
            lines = handle.read().decode('utf-8', errors='replace').splitlines()
        if lines:
            print('LLVM latest completed build edge: ' + lines[-1], flush=True)
        print(f'LLVM progress file bytes={size}, age={int(time.time()-log.stat().st_mtime)}s', flush=True)
