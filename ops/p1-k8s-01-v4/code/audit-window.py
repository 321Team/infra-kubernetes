#!/usr/bin/env python3
"""Persist a UTC audit interval across reboots. Run as root on each node."""
import argparse
import datetime as dt
import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

ENV = dict(os.environ, LC_ALL='C', TZ='UTC')


def call(args):
    return subprocess.run(args, text=True, capture_output=True, env=ENV, timeout=60)


def required(args):
    r = call(args)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout or str(args))
    return r.stdout


def time_args(start, end):
    a = dt.datetime.fromtimestamp(start, dt.timezone.utc)
    b = dt.datetime.fromtimestamp(end, dt.timezone.utc)
    return ['-ts', a.strftime('%m/%d/%Y'), a.strftime('%H:%M:%S'),
            '-te', b.strftime('%m/%d/%Y'), b.strftime('%H:%M:%S')]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['start', 'end', 'query'])
    p.add_argument('--dir', type=Path, required=True)
    a = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('root required')
    os.umask(0o077)
    required(['systemctl', 'is-active', 'auditd'])
    if required(['getenforce']).strip() != 'Enforcing':
        raise SystemExit('SELinux must be Enforcing')
    status = required(['auditctl', '-s'])
    a.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    statefile = a.dir / 'window.json'
    if a.action == 'start':
        if statefile.exists():
            raise SystemExit('existing window: choose a new directory')
        mark = 'FDS_G2_' + uuid.uuid4().hex
        data = {'node': socket.gethostname(), 'start_epoch': int(time.time()),
                'timezone': 'UTC', 'locale': 'C', 'marker': mark,
                'start_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        required(['auditctl', '-m', mark + '_START'])
        statefile.write_text(json.dumps(data, indent=2) + '\n')
        (a.dir / 'audit-status-start.txt').write_text(status)
        print('START_RECORDED; keep this directory through reboot')
        return
    data = json.loads(statefile.read_text())
    if data['node'] != socket.gethostname():
        raise SystemExit('wrong node')
    if a.action == 'end':
        if 'end_epoch' in data:
            raise SystemExit('already closed; query the same interval instead')
        required(['auditctl', '-m', data['marker'] + '_END'])
        data['end_epoch'] = int(time.time())
        if data['end_epoch'] < data['start_epoch']:
            raise SystemExit('clock moved backwards: interval cannot be accepted')
        data['end_boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        statefile.write_text(json.dumps(data, indent=2) + '\n')
        (a.dir / 'audit-status-end.txt').write_text(status)
    elif 'end_epoch' not in data:
        raise SystemExit('close interval with end first')
    interval = time_args(data['start_epoch'], data['end_epoch'])
    # Two separate date/time argv values for EACH endpoint; never a combined string.
    cmd = ['ausearch', '--input-logs', '-m', 'AVC,USER_AVC,SELINUX_ERR,USER_SELINUX_ERR'] + interval
    r = call(cmd)
    (a.dir / 'avc.stdout').write_text(r.stdout)
    (a.dir / 'avc.stderr').write_text(r.stderr)
    (a.dir / 'avc.command.json').write_text(json.dumps({'argv': cmd, 'rc': r.returncode}, indent=2))
    markers = call(['ausearch', '--input-logs', '-m', 'USER'] + interval)
    (a.dir / 'markers.stdout').write_text(markers.stdout)
    (a.dir / 'markers.stderr').write_text(markers.stderr)
    no_matches = r.returncode == 1 and (r.stdout + r.stderr).strip() == '<no matches>'
    retained = markers.returncode == 0 and all(data['marker'] + x in markers.stdout for x in ('_START', '_END'))
    def counters(text):
        return dict(line.split(None, 1) for line in text.splitlines() if len(line.split(None, 1)) == 2)
    before = counters((a.dir / 'audit-status-start.txt').read_text())
    after = counters((a.dir / 'audit-status-end.txt').read_text())
    healthy = all(x.get('enabled') in ('1', '2') and x.get('lost') == '0' for x in (before, after))
    result = {'status': 'NO_AVC_REVIEW_REQUIRED' if no_matches and retained and healthy else 'HOLD',
              'no_matches': no_matches, 'both_markers_retained': retained,
              'audit_counters_healthy': healthy, 'note': 'Review auditd continuity/rotation and clock events separately.'}
    (a.dir / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    if result['status'] == 'HOLD':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
