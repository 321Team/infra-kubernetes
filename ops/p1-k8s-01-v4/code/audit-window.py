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


import re

# ausearch's explicit '-ts MM/DD/YYYY HH:MM:SS' / '-te ...' argument form fails
# with "Error parsing start date" on this environment (audit-4.2.1, el10),
# reproducibly, independent of LC_ALL/LC_TIME and of zero-padding. This is a
# known upstream issue (e.g. Red Hat KB 7074389) and not specific to a single
# node's locale, so every node in this cluster is expected to hit it the same
# way. '-ts today' and '-ts recent' DO work on this build, so instead of the
# explicit date/time pair we pull the whole day's events with '-ts today' and
# filter to [start, end] ourselves using the epoch timestamp embedded in each
# record's msg=audit(epoch.msec:id) header. This still gives an exact,
# reviewable [start_epoch, end_epoch) boundary; it does not widen the window
# ausearch itself is asked to search, since 'today' already has to include it.

AUDIT_MSG_RE = re.compile(r'msg=audit\((\d+)\.\d+:\d+\)')


def filter_by_window(raw_text, start, end):
    """Keep only complete audit records whose timestamp falls in [start, end].

    ausearch groups a record's lines under a single '----' separated block
    that begins with a 'type=... msg=audit(epoch.msec:id):' line. We split on
    that separator, inspect the epoch on the first msg=audit(...) found in
    each block, and keep the block only if it is in range. Blocks without a
    parseable timestamp are kept as-is (e.g. '<no matches>') so the caller can
    still recognize known ausearch output rather than silently dropping it.
    """
    if raw_text.strip() in ('', '<no matches>'):
        return '<no matches>'
    blocks = raw_text.split('----\n')
    kept = []
    for block in blocks:
        m = AUDIT_MSG_RE.search(block)
        if m is None:
            kept.append(block)
            continue
        epoch = int(m.group(1))
        if start <= epoch <= end:
            kept.append(block)
    result = '----\n'.join(b for b in kept if b.strip())
    return result if result else '<no matches>'


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
    start_epoch, end_epoch = data['start_epoch'], data['end_epoch']
    # '-ts today' is the widest scope that reliably works on this ausearch
    # build; we narrow to [start_epoch, end_epoch] ourselves afterward.
    cmd = ['ausearch', '--input-logs', '-m', 'AVC,USER_AVC,SELINUX_ERR,USER_SELINUX_ERR', '-ts', 'today']
    r = call(cmd)
    # ausearch exits 1 (not 0) with stdout '<no matches>' or empty stdout when
    # there are genuinely no matching events; that is a valid, expected
    # outcome here, not a failure. Only treat other/unexpected nonzero exits
    # as a real query failure (stderr will be non-empty in that case).
    avc_query_ok = r.returncode in (0, 1)
    raw_avc = r.stdout
    filtered_avc = filter_by_window(raw_avc, start_epoch, end_epoch) if avc_query_ok else raw_avc
    (a.dir / 'avc.stdout.raw').write_text(raw_avc)
    (a.dir / 'avc.stdout').write_text(filtered_avc)
    (a.dir / 'avc.stderr').write_text(r.stderr)
    (a.dir / 'avc.command.json').write_text(json.dumps(
        {'argv': cmd, 'rc': r.returncode, 'filtered_window': [start_epoch, end_epoch],
         'note': 'ausearch -ts/-te MM/DD/YYYY fails on this audit build; queried -ts today and filtered by epoch instead.'},
        indent=2))
    markers_cmd = ['ausearch', '--input-logs', '-m', 'USER', '-ts', 'today']
    markers = call(markers_cmd)
    markers_query_ok = markers.returncode in (0, 1)
    raw_markers = markers.stdout
    filtered_markers = filter_by_window(raw_markers, start_epoch, end_epoch) if markers_query_ok else raw_markers
    (a.dir / 'markers.stdout.raw').write_text(raw_markers)
    (a.dir / 'markers.stdout').write_text(filtered_markers)
    (a.dir / 'markers.stderr').write_text(markers.stderr)
    no_matches = avc_query_ok and filtered_avc.strip() == '<no matches>'
    retained = markers_query_ok and all(data['marker'] + x in filtered_markers for x in ('_START', '_END'))
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
