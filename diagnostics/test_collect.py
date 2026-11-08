"""Exercise a private copy against fake Linux files/commands, never a device.
The production script has no alternate-root or isolation-bypass interface.
"""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

SOURCE = Path(__file__).with_name('collect.sh')

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root/'bin'; self.bin.mkdir()
        self.log = self.root/'calls'
        self.env = dict(os.environ, PATH=str(self.bin)+':/usr/bin:/bin',
                        CALLS=str(self.log), FIXTURE=str(self.root))
        for name, content in {
            'proc/mounts':'tmpfs /tmp tmpfs rw 0 0\n',
            'proc/uptime':'100.0 90.0\n',
            'proc/tty/driver/meson_uart':'0: tx:4 rx:8\n',
            'lib/firmware/brcm/BCM4345C0.hcd':'fixture',
        }.items():
            p=self.root/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(content)
        text=SOURCE.read_text()
        # Rewrite only absolute fixture paths in a disposable test copy.
        text=text.replace('/proc/',str(self.root)+'/proc/').replace('/sys/',str(self.root)+'/sys/').replace('/lib/firmware/',str(self.root)+'/lib/firmware/')
        text=text.replace('mktemp -d /tmp/n1-bt-diag.XXXXXX','mktemp -d '+str(self.root)+'/n1-bt-diag.XXXXXX')
        self.script=self.root/'collect.sh'; self.script.write_text(text)
        self.stub('n1-bt-check', 'echo check >>"$CALLS"; exit "${CHECK_EXIT:-0}"')
        self.stub('df', 'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\nmock 999999 0 %s 0%% /tmp\\n" "${FREE_KB:-65536}"')
        self.stub('btmon', '''[ "${1:-}" != --version ] || { echo fixture; exit 0; }
echo btmon >>"$CALLS"
printf 'btsnoop\\000\\000\\000\\000\\001\\000\\000\\003\\351fixture-event' > "$2"
exit 0''')
        self.stub('timeout', '''[ "${1:-}" != --version ] || { echo "timeout (GNU coreutils) fixture"; exit 0; }
[ "${1:-}" != --help ] || { echo help; exit 0; }
echo timeout >>"$CALLS"
shift 5
/bin/sleep 0.1
"$@"
printf "220 100\\n" > "$FIXTURE/proc/uptime"
exit "${CAPTURE_EXIT:-124}"''')
        self.stub('sha256sum', 'echo 8bbf245399e66f68304ba0f9185159dd1d557c48b534f2e7be75e2e0f4cb4a9a "$@"')
        self.stub('dmesg', 'printf "N1DIAG setup serdev=1\\nN1DIAG controller-baud requested=2000000 result=-110\\n"')
        self.stub('logread', 'echo system-fixture')
        for name in ['bluetoothctl','btmgmt','rfkill','mount','reboot','fw_setenv','hciattach']:
            self.stub(name,'echo FORBIDDEN >>"$CALLS"; exit 99')

    def stub(self,name,body):
        p=self.bin/name; p.write_text('#!/bin/sh\n'+body+'\n'); p.chmod(0o755)

    def run_capture(self,**variables):
        result=subprocess.run(['/bin/sh',str(self.script)],env=dict(self.env,**variables),capture_output=True,text=True,timeout=60)
        self.assertNotIn('FORBIDDEN',self.log.read_text() if self.log.exists() else '')
        return result

    def outputs(self):
        return list(self.root.glob('n1-bt-diag.*'))

    def test_isolation_fail_closed(self):
        r=self.run_capture(CHECK_EXIT='1')
        self.assertNotEqual(r.returncode,0); self.assertEqual(self.outputs(),[])
        self.assertNotIn('btmon',self.log.read_text())

    def test_space_fail_closed(self):
        for space in ['65535','bad']:
            r=self.run_capture(FREE_KB=space)
            self.assertNotEqual(r.returncode,0); self.assertEqual(self.outputs(),[])
        self.assertNotIn('btmon',self.log.read_text())

    def test_non_tmpfs_fail_closed(self):
        (self.root/'proc/mounts').write_text('/dev/foo /tmp ext4 rw 0 0\n')
        self.assertNotEqual(self.run_capture().returncode,0)
        self.assertEqual(self.outputs(),[])

    def test_second_isolation_failure_prevents_capture(self):
        self.stub('n1-bt-check','''echo check >>"$CALLS"
[ ! -e "$FIXTURE/checked" ] || exit 1
: > "$FIXTURE/checked"''')
        self.assertNotEqual(self.run_capture().returncode,0)
        self.assertNotIn('btmon',self.log.read_text())

    def test_normal_capture(self):
        self.assertEqual(self.run_capture(CAPTURE_EXIT='124').returncode,0)
        out=self.outputs()[0]
        self.assertEqual(out.stat().st_mode & 0o777,0o700)
        for f in ['before.txt','after.txt','btmon.txt','hci.btsnoop','status.txt','uart-timeline.txt','SHA256SUMS']:
            self.assertTrue((out/f).exists(),f)
            self.assertEqual((out/f).stat().st_mode & 0o777,0o600)
        self.assertIn('capture_exit=124',(out/'status.txt').read_text())

    def test_timeout_is_recorded(self):
        self.assertEqual(self.run_capture(CAPTURE_EXIT='124').returncode,0)
        self.assertIn('capture_exit=124',(self.outputs()[0]/'status.txt').read_text())

    def test_capture_error_propagates(self):
        self.assertNotEqual(self.run_capture(CAPTURE_EXIT='7').returncode,0)
        out=self.outputs()[0]
        self.assertIn('capture_exit=7',(out/'status.txt').read_text())
        self.assertTrue((out/'after.txt').exists())

    def test_early_zero_exit_is_incomplete(self):
        self.assertNotEqual(self.run_capture(CAPTURE_EXIT='0').returncode,0)
        self.assertIn('complete=no',(self.outputs()[0]/'status.txt').read_text())

    def test_missing_capture_is_incomplete(self):
        self.stub('btmon','exit 0')
        self.assertNotEqual(self.run_capture().returncode,0)
        self.assertIn('complete=no',(self.outputs()[0]/'status.txt').read_text())

    def test_unsupported_timeout_fails_closed(self):
        self.stub('timeout','echo BusyBox; exit 0')
        self.assertNotEqual(self.run_capture().returncode,0)
        self.assertEqual(self.outputs(),[])

    def test_after_isolation_failure_is_incomplete(self):
        self.stub('n1-bt-check','''n=0
[ ! -f "$FIXTURE/count" ] || n=$(cat "$FIXTURE/count")
n=$((n + 1)); echo "$n" > "$FIXTURE/count"
[ "$n" -lt 3 ]''')
        self.assertNotEqual(self.run_capture().returncode,0)
        self.assertIn('complete=no',(self.outputs()[0]/'status.txt').read_text())

    def test_missing_uart_counters_is_incomplete(self):
        (self.root/'proc/tty/driver/meson_uart').unlink()
        self.assertNotEqual(self.run_capture().returncode,0)
        self.assertIn('complete=no',(self.outputs()[0]/'status.txt').read_text())

    def test_kernel_log_read_failure_stops_capture(self):
        self.stub('dmesg','echo permission-denied >&2; exit 1')
        self.assertNotEqual(self.run_capture().returncode,0)
        out=self.outputs()[0]
        self.assertIn('permission-denied',(out/'before-kernel.txt').read_text())
        self.assertIn('complete=no',(out/'status.txt').read_text())
        self.assertNotIn('btmon',self.log.read_text())

    def test_missing_diagnostic_marker_stops_capture(self):
        self.stub('dmesg','echo "N1DIAG setup serdev=1"')
        self.assertNotEqual(self.run_capture().returncode,0)
        out=self.outputs()[0]
        self.assertIn('N1DIAG setup',(out/'before-kernel.txt').read_text())
        self.assertIn('complete=no',(out/'status.txt').read_text())
        self.assertNotIn('btmon',self.log.read_text())

    def test_hcd_mismatch_stops_capture(self):
        self.stub('sha256sum','echo BADHASH file')
        self.assertNotEqual(self.run_capture().returncode,0)
        self.assertIn('HCD digest mismatch',(self.outputs()[0]/'before.txt').read_text())
        self.assertNotIn('btmon',self.log.read_text())

    def test_signal_reaps_timeout(self):
        self.stub('timeout','''[ "${1:-}" != --version ] || { echo "timeout (GNU coreutils) fixture"; exit 0; }
[ "${1:-}" != --help ] || exit 0
echo $$ > "$FIXTURE/timeout.pid"
trap 'echo stopped > "$FIXTURE/stopped"; exit 143' TERM
while :; do sleep 0.1; done''')
        p=subprocess.Popen(['/bin/sh',str(self.script)],env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(500):
            if (self.root/'timeout.pid').exists(): break
            time.sleep(.02)
        self.assertTrue((self.root/'timeout.pid').exists())
        pid=int((self.root/'timeout.pid').read_text())
        p.send_signal(signal.SIGTERM); p.wait(timeout=5)
        self.assertEqual(p.returncode,143)
        self.assertTrue((self.root/'stopped').exists())
        with self.assertRaises(ProcessLookupError): os.kill(pid,0)

if __name__=='__main__': unittest.main()
