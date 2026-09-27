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


def classify_ausearch(r):
    """Classify one ausearch result as OK, NO_MATCH or ERROR.

    ausearch exits 1 both for "no matching events" and for real failures, so
    rc alone cannot tell them apart. audit-4.2.1-3.el10 was observed in the
    2026-09-26 six-node runtime precheck to emit the exact '<no matches>'
    sentinel on stderr (rc=1, empty stdout), while existing fixtures emit the
    same sentinel on stdout. Accept only either exact single-channel form.
    Any other stderr/output combination remains ERROR so real query failures
    can never be interpreted as zero AVC events.
    """
    text = (r.stdout or '').strip()
    err = (r.stderr or '').strip()

    if r.returncode == 1:
        if text == '<no matches>' and not err:
            return 'NO_MATCH'
        if err == '<no matches>' and not text:
            return 'NO_MATCH'
        return 'ERROR'

    if r.returncode == 0 and text and not err:
        return 'OK'

    return 'ERROR'


def filter_by_window(raw_text, start, end):
    """Keep only complete audit records whose timestamp falls in [start, end].

    Call this only for an OK query. ausearch groups a record's lines under a
    single '----' separated block that begins with a 'type=... msg=audit(...)'
    line. We split on that separator, inspect the epoch on the first
    msg=audit(...) found in each block, and keep the block only if it is in
    range. Blocks without a parseable timestamp are kept as-is so unexpected
    output surfaces for review instead of being silently dropped.
    """
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


def search_start(start_epoch, now_epoch):
    """Return the ausearch -ts keyword that covers start_epoch, or None.

    ENV pins TZ=UTC, so 'today'/'yesterday' are UTC midnights. A window that
    began before today's midnight needs 'yesterday'; one older than that
    cannot be covered by a keyword that works on this build and must HOLD.
    """
    midnight = now_epoch - now_epoch % 86400
    if start_epoch >= midnight:
        return 'today'
    if start_epoch >= midnight - 86400:
        return 'yesterday'
    return None


def query(cmd, start_epoch, end_epoch):
    """Run ausearch and return (status, raw result, filtered text)."""
    r = call(cmd)
    status = classify_ausearch(r)
    if status == 'OK':
        return status, r, filter_by_window(r.stdout, start_epoch, end_epoch)
    if status == 'NO_MATCH':
        return status, r, '<no matches>'
    return status, r, r.stdout


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
    # The explicit -ts/-te date form fails on this ausearch build; use the
    # narrowest keyword that still covers start_epoch and filter by epoch.
    ts = search_start(start_epoch, int(time.time()))
    if ts is None:
        result = {'status': 'HOLD', 'no_matches': False, 'both_markers_retained': False,
                  'audit_counters_healthy': False, 'avc_query': 'NOT_RUN', 'markers_query': 'NOT_RUN',
                  'note': 'window starts before yesterday 00:00 UTC; ausearch keyword cannot cover it'}
        (a.dir / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
        raise SystemExit(2)
    cmd = ['ausearch', '--input-logs', '-m', 'AVC,USER_AVC,SELINUX_ERR,USER_SELINUX_ERR', '-ts', ts]
    avc_status, r, filtered_avc = query(cmd, start_epoch, end_epoch)
    (a.dir / 'avc.stdout.raw').write_text(r.stdout)
    (a.dir / 'avc.stdout').write_text(filtered_avc)
    (a.dir / 'avc.stderr').write_text(r.stderr)
    (a.dir / 'avc.command.json').write_text(json.dumps(
        {'argv': cmd, 'rc': r.returncode, 'classified': avc_status,
         'filtered_window': [start_epoch, end_epoch],
         'note': 'ausearch -ts/-te MM/DD/YYYY fails on this audit build; queried by keyword and filtered by epoch instead.'},
        indent=2))
    markers_cmd = ['ausearch', '--input-logs', '-m', 'USER', '-ts', ts]
    markers_status, markers, filtered_markers = query(markers_cmd, start_epoch, end_epoch)
    (a.dir / 'markers.stdout.raw').write_text(markers.stdout)
    (a.dir / 'markers.stdout').write_text(filtered_markers)
    (a.dir / 'markers.stderr').write_text(markers.stderr)
    # A failed AVC query is never "no AVC": only NO_MATCH, or OK whose
    # in-window remainder is empty, counts as zero events.
    no_matches = avc_status in ('NO_MATCH', 'OK') and filtered_avc.strip() == '<no matches>'
    retained = markers_status == 'OK' and all(data['marker'] + x in filtered_markers for x in ('_START', '_END'))
    def counters(text):
        return dict(line.split(None, 1) for line in text.splitlines() if len(line.split(None, 1)) == 2)
    before = counters((a.dir / 'audit-status-start.txt').read_text())
    after = counters((a.dir / 'audit-status-end.txt').read_text())
    healthy = all(x.get('enabled') in ('1', '2') and x.get('lost') == '0' for x in (before, after))
    result = {'status': 'NO_AVC_REVIEW_REQUIRED' if no_matches and retained and healthy else 'HOLD',
              'no_matches': no_matches, 'both_markers_retained': retained,
              'audit_counters_healthy': healthy, 'avc_query': avc_status,
              'markers_query': markers_status, 'note': 'Review auditd continuity/rotation and clock events separately.'}
    (a.dir / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    if result['status'] == 'HOLD':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
