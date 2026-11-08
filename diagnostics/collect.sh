#!/bin/sh
# Optional local diagnostic package. Not installed or run by existing images.
# Run only after a separately approved USB test; writes private logs to tmpfs.
set -eu
umask 077
[ "$#" -eq 0 ] || { echo 'Usage: n1-bt-collect (fixed 120 second capture)' >&2; exit 2; }
for tool in n1-bt-check timeout btmon mktemp sha256sum hexdump; do
    command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; exit 1; }
done
timeout --version 2>/dev/null | grep -q 'GNU coreutils' || { echo 'GNU coreutils-timeout is required; unsupported timeout implementation.' >&2; exit 1; }
n1-bt-check >/dev/null || { echo 'Isolation check failed; no capture started.' >&2; exit 1; }
awk '$2 == "/tmp" && $3 == "tmpfs" {ok=1} END {exit !ok}' /proc/mounts || exit 1
# The capture has a size limit as well as a time limit; leave room for the OS.
free_kb=$(df -Pk /tmp | awk 'END {print $4}')
case "$free_kb" in ''|*[!0-9]*) exit 1;; esac
[ "$free_kb" -ge 65536 ] || { echo 'Less than 64 MiB free in /tmp.' >&2; exit 1; }
out=$(mktemp -d /tmp/n1-bt-diag.XXXXXX)
pid=
sampler=
cleanup() {
    if [ -n "$sampler" ]; then kill "$sampler" 2>/dev/null || :; wait "$sampler" 2>/dev/null || :; fi
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || :; wait "$pid" 2>/dev/null || :; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
snapshot() {
    echo '=== UTC / uptime ==='; date -u; cat /proc/uptime
    echo '=== Kernel / isolation ==='; uname -a; n1-bt-check || return 1
    echo '=== UART counters (software, not physical CTS measurement) ==='
    cat /proc/tty/driver/meson_uart 2>/dev/null || echo 'UART counters unavailable'
    echo '=== Runtime PM ==='
    for base in /sys/bus/serial/devices/serial0-0/power /sys/bus/platform/devices/c11084c0.serial/power; do
        for name in control runtime_status runtime_active_time runtime_suspended_time; do
            printf '%s: ' "$base/$name"; cat "$base/$name" 2>/dev/null || echo unavailable
        done
    done
    echo '=== Read-only debugfs views (no mounting or register writes) ==='
    for path in /sys/kernel/debug/gpio /sys/kernel/debug/clk/clk_summary /sys/kernel/debug/pinctrl/*/pinmux-pins; do
        [ -r "$path" ] || continue
        printf '\n--- %s ---\n' "$path"; head -c 131072 "$path"
    done
    echo '=== HCD digest ==='
    digest=$(sha256sum /lib/firmware/brcm/BCM4345C0.hcd) || return 1
    printf '%s\n' "$digest"
    [ "${digest%% *}" = 8bbf245399e66f68304ba0f9185159dd1d557c48b534f2e7be75e2e0f4cb4a9a ] || { echo 'HCD digest mismatch'; return 1; }
    # The caller's file-size limit also bounds this raw kernel log. Keep errors
    # and incomplete records; never hide dmesg failure behind a pipeline.
    dmesg >"$out/$1-kernel.txt" 2>&1 || { echo 'Kernel log unavailable'; return 1; }
    echo '=== Diagnostic kernel records ==='
    grep N1DIAG "$out/$1-kernel.txt" || :
    grep -q 'N1DIAG setup ' "$out/$1-kernel.txt" || { echo 'Missing setup diagnostic'; return 1; }
    grep -q 'N1DIAG controller-baud requested=.*result=' "$out/$1-kernel.txt" || { echo 'Missing baud result diagnostic'; return 1; }
    echo '=== Recent kernel messages ==='; tail -n 500 "$out/$1-kernel.txt"
    echo '=== Recent system messages ==='; logread 2>/dev/null | tail -n 300 || :
}
# Limit each output file to 2 MiB (POSIX ulimit -f uses 512-byte blocks).
(ulimit -f 4096; snapshot before) >"$out/before.txt" 2>&1 || { printf 'complete=no\nreason=initial-snapshot-failed\n' >"$out/status.txt"; printf 'Initial snapshot failed; capture not started. Private evidence: %s\n' "$out" >&2; exit 1; }
printf 'Private capture: %s\n120 seconds; no power, scan, pairing or service changes are performed.\n' "$out"
timeout --help >"$out/timeout-help.txt" 2>&1 || :
btmon --version >"$out/btmon-version.txt" 2>&1 || :
started=$(awk '{print int($1)}' /proc/uptime)
# Timeout owns btmon directly; no pipeline whose grandchildren could survive.
(ulimit -f 16384; exec timeout -s TERM -k 3 120 btmon -w "$out/hci.btsnoop") >"$out/btmon.txt" 2>&1 &
pid=$!
# Bounded counter timeline; read-only and useful across an HCI timeout.
(ulimit -f 4096
 trap 'exit 0' TERM INT
 n=0
 while [ "$n" -lt 60 ]; do
     cat /proc/uptime
     cat /proc/tty/driver/meson_uart 2>/dev/null || { echo 'UART counters unavailable'; exit 1; }
     n=$((n + 1))
     sleep 2
 done
) >"$out/uart-timeline.txt" 2>&1 &
sampler=$!
status=0
wait "$pid" || status=$?
pid=
ended=$(awk '{print int($1)}' /proc/uptime)
elapsed=$((ended - started))
kill "$sampler" 2>/dev/null || :
sampler_status=0
wait "$sampler" 2>/dev/null || sampler_status=$?
sampler=
printf 'capture_exit=%s\n124 normally means the 120 second limit; other nonzero exits require inspection.\n' "$status" >"$out/status.txt"
complete=yes
[ "$sampler_status" -eq 0 ] || complete=no
[ -s "$out/uart-timeline.txt" ] || complete=no
(ulimit -f 4096; snapshot after) >"$out/after.txt" 2>&1 || { echo 'Final snapshot incomplete' >>"$out/snapshot-warning.txt"; complete=no; }
[ "$status" -eq 124 ] && [ "$elapsed" -ge 115 ] || complete=no
if [ ! -f "$out/hci.btsnoop" ] || [ "$(wc -c <"$out/hci.btsnoop")" -le 16 ]; then
    complete=no
elif [ "$(hexdump -n 8 -v -e '1/1 "%02x"' "$out/hci.btsnoop")" != 6274736e6f6f7000 ]; then
    complete=no
fi
printf 'elapsed_seconds=%s\nsampler_exit=%s\ncomplete=%s\n' "$elapsed" "$sampler_status" "$complete" >>"$out/status.txt"
(cd "$out"; sha256sum ./*.txt ./hci.btsnoop 2>/dev/null >SHA256SUMS) || :
printf 'Capture finished: %s\nKeep private: logs can contain Bluetooth addresses and nearby device traffic. No automatic upload.\n' "$out"

[ "$complete" = yes ] || exit 1
