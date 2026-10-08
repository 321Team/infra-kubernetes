#!/usr/bin/env python3
"""Capture gateway policy inputs only; never infer packet DENY from configuration."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time


def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()


def plan():
    rows = [
        ('firewalld-version', ['firewall-cmd', '--version']),
        ('firewalld-state', ['firewall-cmd', '--state']),
        ('active-zones', ['firewall-cmd', '--get-active-zones']),
        ('active-policies', ['firewall-cmd', '--get-active-policies']),
        ('links', ['ip', '-j', 'address', 'show']),
        ('routes', ['ip', '-j', 'route', 'show', 'table', 'all']),
        ('routing-rules', ['ip', '-j', 'rule', 'show']),
        ('ip-forward', ['sysctl', 'net.ipv4.ip_forward']),
    ]
    for phase in ('before', 'after'):
        for mode in ('runtime', 'permanent'):
            prefix = ['firewall-cmd'] + (['--permanent'] if mode == 'permanent' else [])
            rows.append((f'{phase}-{mode}-policy', prefix + ['--info-policy=if06b-p2m']))
            # Zone forwarding/masquerade and other policies can affect effective rules.
            rows.append((f'{phase}-{mode}-zones', prefix + ['--list-all-zones']))
            rows.append((f'{phase}-{mode}-policies', prefix + ['--list-all-policies']))
        rows.append((f'{phase}-nft', ['nft', '-a', '-nn', 'list', 'ruleset']))
    return rows


def capture(out, key, argv, deadline):
    row = {'id': key, 'argv': argv, 'started_kst': now()}
    remaining = deadline - time.monotonic()
    stdout = stderr = b''
    rc = 124
    if remaining > 0:
        try:
            with subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  start_new_session=True) as p:
                try:
                    stdout, stderr = p.communicate(timeout=min(15, remaining))
                    rc = p.returncode
                except subprocess.TimeoutExpired:
                    # Kill the read-only command group and retain all partial bytes.
                    os.killpg(p.pid, signal.SIGKILL)
                    stdout, stderr = p.communicate()
                    rc = 124
        except OSError as e:
            rc, stderr = 127, str(e).encode()
    else:
        stderr = b'Whole-collection time budget exhausted; command not started.\n'
    for suffix, data in [('stdout', stdout), ('stderr', stderr), ('rc', f'{rc}\n'.encode())]:
        (out / f'{key}.{suffix}').write_bytes(data)
    row.update(rc=rc, finished_kst=now())
    return row


def finish(out, rows):
    # Only configuration byte stability is reported. nft counters are live;
    # no all-rules atomicity or historical packet attribution is claimed.
    pairs = [f'{mode}-{kind}' for mode in ('runtime', 'permanent')
             for kind in ('policy', 'zones', 'policies')]
    stable = all((out / f'before-{p}.stdout').read_bytes() ==
                 (out / f'after-{p}.stdout').read_bytes() for p in pairs)
    rc = 0 if all(r['rc'] == 0 for r in rows) and stable else 2
    report = {'schema': 'if06b-policy-readonly/v1', 'finished_kst': now(),
              'hostname': socket.gethostname(), 'uid': os.geteuid(),
              'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'collection_rc': rc, 'configuration_bytes_stable': stable,
              'policy_verdict': 'PENDING_REVIEW', 'packet_test': 'NOT_RUN',
              'historical_8443_enforcer': 'NOT_ESTABLISHED', 'queries': rows}
    (out / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    entries = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
               for p in sorted(out.iterdir()) if p.is_file()]
    (out / 'SHA256SUMS').write_text(''.join(entries))
    return rc


def main():
    if socket.gethostname().split('.')[0] != 'mgmt-gw01' or os.geteuid() != 0:
        raise SystemExit('STOP: mgmt-gw01 with existing root privilege required')
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    os.environ['LC_ALL'] = 'C'
    os.umask(0o077)
    out = Path(tempfile.mkdtemp(prefix='if06b-policy-', dir='/root'))
    print(f'OUT={out}', flush=True)
    deadline = time.monotonic() + 180
    rows = []
    try:
        for key, argv in plan():
            rows.append(capture(out, key, argv, deadline))
        rc = finish(out, rows)
    except (OSError, KeyboardInterrupt) as e:
        # Retain the private directory; no successful seal claim after interruption.
        print(f'INCOMPLETE_UNSEALED: {type(e).__name__}; retain {out}', flush=True)
        return 12
    print(f'COLLECTION_RC={rc} OUT={out}', flush=True)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
