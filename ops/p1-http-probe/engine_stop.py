#!/usr/bin/env python3
"""One exact engine process, one STOP, bounded same-pidfd CONT; never AC PASS.

Requires independent source review and an operator-approved execution card.
CLI timestamps/hash are consistency checks, not authenticated authorization.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import time

MAX_HOLD = 150
RECOVERY_RESERVE = 240
HOST = 'k8s-worker02'
CRI = ['/usr/bin/crictl', '--runtime-endpoint', 'unix:///run/containerd/containerd.sock']


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def stamp(value):
    d = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(d.utcoffset() is not None, 'timezone required')
    return d.timestamp()


def window(start, latest, end, now):
    a, b, c = map(stamp, (start, latest, end))
    need(a <= now <= b, 'outside actual start window')
    need(a <= b and c - b >= MAX_HOLD + RECOVERY_RESERVE, 'insufficient recovery budget')
    return c


def validate_binding(b):
    need(b['host'] == HOST, 'host mismatch')
    need(re.fullmatch(r'[0-9a-f]{64}', b['container_id']) is not None, 'full container ID required')
    need(re.fullmatch(r'[0-9a-f-]{36}', b['pod_uid']) is not None, 'Pod UID required')
    need(b['namespace'] == 'fds-app' and b['container_name'] == 'fds-engine', 'wrong workload')
    need(b['pod_name'].startswith('fds-engine-'), 'wrong Pod name')
    need(type(b['pid']) is int and b['pid'] > 1, 'unsafe host PID')
    need(str(b['start_ticks']).isdigit(), 'start time required')
    need(b['image_ref'].startswith('10.1.93.54/fds/fds-engine@sha256:'), 'digest image required')


def inspect_identity(b):
    need(socket.gethostname() == b['host'] and os.geteuid() == 0, 'host/root mismatch')
    need(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == b['boot_id'], 'boot changed')
    r = subprocess.run(CRI + ['inspect', b['container_id']], capture_output=True, timeout=8)
    need(r.returncode == 0, 'CRI inspect failed; no fallback target')
    x = json.loads(r.stdout); s = x['status']; labels = s['labels']
    need(s['id'] == b['container_id'] and s['state'] == 'CONTAINER_RUNNING', 'container changed/exited')
    need(x['info']['pid'] == b['pid'] and s['imageRef'] == b['image_ref'], 'PID/image changed')
    for key, want in [('namespace', b['namespace']), ('name', b['pod_name']), ('uid', b['pod_uid'])]:
        need(labels['io.kubernetes.pod.' + key] == want, 'Pod identity mismatch')
    need(labels['io.kubernetes.container.name'] == b['container_name'], 'container name mismatch')
    p = Path('/proc') / str(b['pid'])
    stat = (p / 'stat').read_text(); fields = stat[stat.rfind(')') + 2:].split()
    need(fields[19] == str(b['start_ticks']), 'PID start changed')
    need((p / 'cgroup').read_text() == b['cgroup'], 'cgroup changed')
    need(os.readlink(p / 'ns/pid') == b['pid_namespace'], 'PID namespace changed')
    need((p / 'cmdline').read_bytes() == b'/venv/bin/python3\x00run.py\x00', 'command changed')
    status = dict(line.split(':', 1) for line in (p / 'status').read_text().splitlines())
    need(status['Uid'].split() == ['10001'] * 4 and status['NSpid'].split() == [str(b['pid']), '1'], 'UID/namespace PID changed')
    return fields[0]


def exited(fd):
    return bool(select.select([fd], [], [], 0)[0])


def pin(b, allow_stopped=False):
    """Revalidate after opening: every later signal uses only this descriptor."""
    inspect_identity(b)
    fd = os.pidfd_open(b['pid'])
    try:
        state = inspect_identity(b)
        need(not exited(fd), 'pinned process exited')
        need(state not in ('T', 't') or allow_stopped, 'already stopped; do not inject')
        need(state not in ('Z', 'X'), 'process exited')
        return fd
    except BaseException:
        os.close(fd)
        raise


def resume(fd, log):
    if exited(fd):
        log('ORIGINAL_EXITED_NO_CONT')
        return
    try:
        signal.pidfd_send_signal(fd, signal.SIGCONT)
        log('CONT_ORIGINAL_ONLY')
    except ProcessLookupError:
        log('ORIGINAL_EXITED_DURING_CONT')


def hold(fd, abort, log, seconds=MAX_HOLD):
    """No subprocess, new PID lookup, or second STOP after injection."""
    need(0 < seconds <= MAX_HOLD, 'invalid hold bound')
    deadline = time.monotonic() + seconds
    # Recovery covers uncertain signal completion and failures writing STOP_SENT.
    log('STOP_INTENT')
    try:
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        log('STOP_SENT')
        while True:
            if exited(fd):
                log('ORIGINAL_EXIT_OBSERVED_NOT_PROBE_PASS')
                return 'ORIGINAL_EXIT'
            if abort.exists():
                log('ABORT_REQUESTED')
                return 'ABORT'
            if time.monotonic() >= deadline:
                log('HOLD_DEADLINE')
                return 'DEADLINE'
            time.sleep(0.2)
    finally:
        resume(fd, log)


def interrupted(signum, frame):
    raise InterruptedError('signal ' + str(signum))


def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument('mode', choices=['apply', 'resume-original'])
    a.add_argument('binding')
    a.add_argument('--source-sha256', required=True)
    a.add_argument('--start'); a.add_argument('--latest-start'); a.add_argument('--end')
    a.add_argument('--output')
    args = a.parse_args()
    need(socket.gethostname() == HOST and os.geteuid() == 0, 'worker02 root required')
    need(hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == args.source_sha256, 'source mismatch')
    b = json.loads(Path(args.binding).read_text()); validate_binding(b)
    fd = None
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    if args.mode == 'resume-original':
        # Console recovery after controller loss: rebind only the original identity.
        fd = pin(b, allow_stopped=True)
        try:
            resume(fd, lambda event: print(json.dumps({'event': event, 'utc': dt.datetime.now(dt.timezone.utc).isoformat()}), flush=True))
        finally:
            os.close(fd)
        return 0
    need(all((args.start, args.latest_start, args.end, args.output)), 'window/output required')
    window(args.start, args.latest_start, args.end, time.time())
    out = Path(args.output)
    need(out.parent == Path('/var/tmp') and re.fullmatch(r'p1-http-probe-[0-9]{8}-[0-9]{4}', out.name), 'fixed new output path required')
    os.umask(0o077)
    out.mkdir(mode=0o700, exist_ok=False)  # Persistent one-shot claim; never remove/reuse.
    with (out / 'input.json').open('x') as f:
        json.dump({'binding': b, 'arguments': vars(args)}, f, indent=2); f.flush(); os.fsync(f.fileno())
    with (out / 'events.jsonl').open('x', buffering=1) as f:
        def log(event):
            f.write(json.dumps({'event': event, 'utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'monotonic': time.monotonic()}) + '\n')
            f.flush(); os.fsync(f.fileno())
        result = 'ERROR'
        try:
            fd = pin(b)
            window(args.start, args.latest_start, args.end, time.time())
            need(not (out / 'ABORT').exists(), 'preexisting abort')
            result = hold(fd, out / 'ABORT', log)
        except BaseException as e:
            # hold finally attempts same-FD resume before an error is logged.
            log('ERROR_' + type(e).__name__)
            raise
        finally:
            if fd is not None:
                os.close(fd)
            log('FINAL_' + result)
    # Exit of original is only a collection result; external kubelet/Ready evidence required.
    return 0 if result == 'ORIGINAL_EXIT' else 2


if __name__ == '__main__':
    raise SystemExit(main())
