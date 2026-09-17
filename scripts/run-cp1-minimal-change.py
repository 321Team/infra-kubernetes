#!/usr/bin/env python3
"""CP1-only bounded Runtime change. Default plan is read-only; no cluster join.

Run from a verified checkout/package. Apply requires a reviewed bundle digest,
an unchanged original CP1 receipt, a fresh full inspection and explicit window.
"""
import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('minimal_core', HERE / 'cp1_minimal_change.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
BASE = Path('/var/lib/fds/cp1-minimal')
FILES = ('cp1_minimal_change.py', 'run-cp1-minimal-change.py', 'node-firewall.py',
         'run-node-firewall.py', 'analyze-cp1-cni-trial.py')
SINGULAR = {'zones': 'zone', 'policies': 'policy', 'ipsets': 'ipset'}


def source_files(directory=HERE):
    files = {}
    for name in FILES:
        path = directory / name
        m.need(not path.is_symlink() and path.is_file(), 'source path invalid')
        files[name] = m.digest(path.read_bytes())
    return files


def load_checked(path, expected):
    m.need(path and re.fullmatch('[0-9a-f]{64}', expected or ''), 'input path and SHA256 required')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        m.need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'input not regular')
        raw = stream.read(8 * 1024 * 1024 + 1)
    m.need(len(raw) <= 8 * 1024 * 1024 and m.digest(raw) == expected, 'input SHA256/size differs')
    return m.analyzer().parse(raw)


def private(path, create=False):
    for part in reversed([path] + list(path.parents)):
        if create and not part.exists() and not part.is_symlink():
            part.mkdir(mode=0o700)
        st = part.lstat()
        m.need(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022,
               'untrusted root directory: ' + str(part))
    return path


def atomic(path, raw):
    temp = path.with_name(path.name + '.tmp.' + secrets.token_hex(4))
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


class Journal:
    def __init__(self, path):
        self.path = path
    def save(self, record):
        atomic(self.path / 'CHANGE.json', m.encoded(record))
    def load(self):
        file = self.path / 'CHANGE.json'; st = file.lstat()
        m.need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o077, 'untrusted journal')
        return m.analyzer().parse(file.read_bytes())


class IO:
    def __init__(self, job_id, directory=None):
        self.job_id, self.directory = job_id, directory
        self.ctl = m.legacy(); self.n = self.ctl.node
        self.backend = self.n.BaseBackend(m.HOST)
        self.n.COMMAND_STATE.update(count=0, timeouts=0, budget_exhausted=False, deadline=time.monotonic() + 360)
        self.unit = 'fds-cp1-minimal-' + job_id

    def now(self): return m.now()

    def begin_recovery(self):
        self.n.COMMAND_STATE.update(count=0, timeouts=0, budget_exhausted=False, deadline=time.monotonic() + 150)

    def command(self, argv, timeout=30):
        result = self.n.command(argv, timeout=timeout)
        if self.directory:
            with (self.directory / 'commands.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(result, sort_keys=True) + '\n'); stream.flush(); os.fsync(stream.fileno())
        m.need(result['rc'] == 0 and not result.get('stdout_truncated') and not result.get('stderr_truncated') and
               not result['stderr'].strip(), 'command failed/uncertain: ' + json.dumps(result))
        return result['stdout'].strip()

    def snapshot(self):
        state = self.backend.cp1_state()
        # Warnings must not silently become state evidence.
        for result in list(state['queries'].values()) + list(state['snapshot_queries'].values()):
            m.need(not result['stderr'].strip(), 'snapshot warning requires review')
        objects, services = {}, {}
        for plane in ('runtime', 'permanent'):
            prefix = ['firewall-cmd'] + (['--permanent'] if plane == 'permanent' else [])
            services[plane] = {name: self.command(prefix + ['--info-service=' + name]) for name in ('ssh', 'dhcpv6-client')}
            objects[plane] = {'policies': {name: m.parse_info(raw, name) for name, raw in
                               state['stable']['firewall']['active_policies']['configurations'][plane].items()}}
            for kind, get_names, info in (('zones', '--get-zones', '--info-zone='),
                                           ('ipsets', '--get-ipsets', '--info-ipset=')):
                names = self.command(prefix + [get_names]).split()
                m.need(len(names) == len(set(names)) and len(names) <= 64 and
                       all(re.fullmatch('[A-Za-z0-9_-]+', name) for name in names), 'inventory names invalid/too many')
                objects[plane][kind] = {name: m.parse_info(self.command(prefix + [info + name]), name)
                                        for name in sorted(names)}
        result = self.backend.kubectl(['get', 'blockaffinities.crd.projectcalico.org', '-o', 'json'])
        m.need(result['rc'] == 0 and not result['stderr'].strip() and not result.get('stdout_truncated'), 'IPAM query incomplete')
        owned = [v['spec'] for v in json.loads(result['stdout'])['items'] if v.get('spec', {}).get('node') == m.HOST]
        m.need(len(owned) == 1 and owned[0].get('cidr') == m.BLOCK and owned[0].get('state') == 'confirmed' and
               str(owned[0].get('deleted', 'false')).lower() == 'false', 'CP1 block affinity changed')
        # Confirm the IPAM block separately; do not pin allocation counts/new Pod addresses.
        result = self.backend.kubectl(['get', 'ipamblocks.crd.projectcalico.org', '-o', 'json'])
        m.need(result['rc'] == 0 and not result['stderr'].strip() and not result.get('stdout_truncated'), 'IPAM block query incomplete')
        blocks = [v['spec'] for v in json.loads(result['stdout'])['items'] if v.get('spec', {}).get('affinity') == 'host:' + m.HOST]
        m.need(len(blocks) == 1 and blocks[0]['cidr'] == m.BLOCK, 'CP1 IPAM block changed')
        conf = Path('/etc/firewalld/firewalld.conf')
        st = conf.lstat(); m.need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022, 'untrusted firewalld.conf')
        matches = re.findall(r'^\s*FirewallBackend\s*=\s*(\w+)\s*$', conf.read_text(), re.M)
        m.need(matches == ['nftables'], 'explicit nftables backend required')
        return {'firewall': state['stable']['firewall'], 'protection': state['stable']['protection'],
                'objects': objects, 'services': services, 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'version': self.command(['firewall-cmd', '--version']), 'backend': matches[0],
                'ipam': {'cidr': m.BLOCK, 'affinity': 'host:' + m.HOST, 'state': 'confirmed'}}

    def timer_active(self):
        result = self.n.command(['systemctl', 'is-active', self.unit + '.timer'])
        return result['rc'] == 0 and result['stdout'].strip() == 'active' and not result['stderr'].strip()

    def arm(self, withdraw):
        seconds = int((m.instant(withdraw) - self.now()).total_seconds())
        m.need(seconds > 360 and self.directory is not None, 'deadline too close for bounded apply')
        argv = ['systemd-run', '--unit=' + self.unit, '--on-active=' + str(seconds) + 's',
                '--timer-property=AccuracySec=1s', '--property=Type=oneshot', '--property=User=root',
                '--property=TimeoutStartSec=180s', '--quiet', '/usr/bin/python3', '-I', '-B',
                str(self.directory / 'scripts/run-cp1-minimal-change.py'), 'recover', '--job', self.job_id]
        self.command(argv)
        m.need(self.timer_active(), 'independent recovery timer not armed')

    def stage(self, kind, name, xml):
        directory = private(self.directory / 'xml' / kind, True)
        file = directory / (name + '.xml'); atomic(file, xml.encode())
        self.command(['firewall-cmd', '--permanent', '--new-' + SINGULAR[kind] + '-from-file=' + str(file), '--name=' + name])

    def remove_permanent(self, kind, name):
        self.command(['firewall-cmd', '--permanent', '--delete-' + SINGULAR[kind] + '=' + name])

    def bind(self, present):
        self.command(['firewall-cmd', '--zone=' + m.ZONE, ('--add-' if present else '--remove-') + 'interface=cali+'])

    def reload_restore(self, base):
        self.command(['firewall-cmd', '--check-config'])
        self.command(['firewall-cmd', '--reload'])
        active = self.command(['firewall-cmd', '--get-zone-of-interface=ens160'])
        m.need(active in ('public', 'no zone'), 'ens160 changed to another zone')
        if active == 'no zone':
            self.command(['firewall-cmd', '--zone=public', '--add-interface=ens160'])
        current = self.n.parse_zone(self.command(['firewall-cmd', '--zone=public', '--list-all']))
        required = base['firewall']['planes']['runtime']['rules']
        m.need(set(current['rules']) <= set(required), 'foreign public Runtime rule after reload')
        for rule in required:
            if rule not in current['rules']:
                self.command(['firewall-cmd', '--zone=public', '--add-rich-rule=' + rule])

    def disarm(self):
        # Do not stop the recovery service from inside itself.
        result = self.n.command(['systemctl', 'show', self.unit + '.timer', '--property=LoadState', '--value'])
        if result['stdout'].strip() == 'not-found' and not self.timer_active():
            return
        self.command(['systemctl', 'stop', self.unit + '.timer'])
        m.need(not self.timer_active(), 'recovery timer still active')


def live_status(io, record):
    view = io.snapshot()
    active = m.validate_view(view, record['before'])
    status = record['status']
    armed = io.timer_active()
    if status == 'ACTIVE_REVIEW_REQUIRED':
        m.need(active and armed and view == record['after'] and io.now() < m.instant(record['withdraw_at']),
               'live change differs/expired/recovery absent')
    return {'status': status, 'job_id': record['job_id'], 'source_bundle_sha256': record['source_bundle_sha256'],
            'change_receipt_sha256': m.fingerprint(record), 'withdraw_at': record['withdraw_at'],
            'current': view, 'recovery_armed': armed, 'observed_at': io.now().isoformat()}


def inspect_document(io, original, bundle):
    base = io.snapshot()
    document = {'schema': 'fds.cp1-minimal-inspection.v1', 'created_at': m.now().isoformat(),
                'source_bundle_sha256': bundle, 'before': base, 'before_sha256': m.fingerprint(base),
                'original_receipt_sha256': m.fingerprint(original), 'operational_approval': False}
    try:
        m.validate_baseline(base, original)
    except Exception as error:
        # Keep the completed read-only snapshot, but give rejected evidence a
        # distinct schema that apply cannot consume as a successful inspection.
        document.update(schema='fds.cp1-minimal-inspect-diagnostic.v1', status='STOP_REVIEW_REQUIRED',
                        error=str(error), mutation_attempted=False)
        print(m.encoded(document).decode())
        raise
    print(m.encoded(document).decode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', default='plan', choices=('plan', 'inspect', 'apply', 'status', 'recover', 'issue-receipt'))
    for name in ('original', 'authorization', 'inspection'):
        parser.add_argument('--' + name); parser.add_argument('--' + name + '-sha256')
    parser.add_argument('--job'); parser.add_argument('--trial'); parser.add_argument('--trial-sha256')
    args = parser.parse_args(); os.umask(0o077)
    files = source_files(); bundle = m.fingerprint(files)
    if args.mode == 'plan':
        print(json.dumps({'mode': 'PLAN_ONLY', 'hostname': m.HOST, 'block': m.BLOCK,
                          'source_bundle_sha256': bundle, 'files': files, 'operational_approval': False}, indent=2)); return
    m.need(os.geteuid() == 0, 'invoke as normal account with sudo; root login is not required')
    private(BASE, True)
    lock_fd = os.open(BASE / 'LOCK', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        m.need(os.fstat(lock_fd).st_uid == 0 and stat.S_ISREG(os.fstat(lock_fd).st_mode) and
               not os.fstat(lock_fd).st_mode & 0o077, 'untrusted lock')
        deadline = time.monotonic() + (30 if args.mode == 'recover' else 0)
        while True:
            try: fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                m.need(time.monotonic() < deadline, 'another CP1 operation holds the lock'); time.sleep(0.2)
        if args.mode in ('inspect', 'apply'):
            original = load_checked(args.original, args.original_sha256)
            if args.mode == 'inspect':
                inspect_document(IO('inspect'), original, bundle); return
            inspection = load_checked(args.inspection, args.inspection_sha256)
            auth = load_checked(args.authorization, args.authorization_sha256)
            m.need(inspection['schema'] == 'fds.cp1-minimal-inspection.v1' and inspection['source_bundle_sha256'] == bundle and
                   inspection['original_receipt_sha256'] == m.fingerprint(original) and
                   inspection['before_sha256'] == m.fingerprint(inspection['before']) and
                   auth['before_sha256'] == inspection['before_sha256'], 'inspection binding differs')
            m.need(0 <= (m.now() - m.instant(inspection['created_at'])).total_seconds() <= 900, 'fresh inspection required')
            m.check_authorization(auth, inspection['before'], bundle)
            for path in BASE.glob('????????????????/CHANGE.json'):
                record = Journal(private(path.parent)).load()
                m.need(record.get('cleanup_complete') is True and record.get('recovery_armed') is False, 'prior job unresolved')
            job = secrets.token_hex(8); directory = private(BASE / job, True)
            staged = private(directory / 'scripts', True)
            for name in FILES:
                raw = (HERE / name).read_bytes(); m.need(m.digest(raw) == files[name], 'source changed during freeze')
                atomic(staged / name, raw)
            atomic(directory / 'SOURCE.json', m.encoded(files)); atomic(directory / 'ORIGINAL.json', m.encoded(original))
            atomic(directory / 'AUTHORIZATION.json', m.encoded(auth))
            print('JOB_ID=' + job, flush=True)
            io = IO(job, directory); record = m.apply(io, Journal(directory), original, auth, bundle)
            atomic(directory / 'expected-firewall.json', m.analyzer().encoded(record['after']['firewall']))
            print(m.encoded(record).decode()); return
        m.need(re.fullmatch('[0-9a-f]{16}', args.job or ''), 'exact job id required')
        directory = private(BASE / args.job)
        saved_files = m.analyzer().parse((directory / 'SOURCE.json').read_bytes())
        m.need(saved_files == files == source_files(directory / 'scripts'), 'same frozen source required')
        journal = Journal(directory); record = journal.load()
        m.need(record['job_id'] == args.job and record['source_bundle_sha256'] == bundle, 'job/source differs')
        io = IO(args.job, directory)
        if args.mode == 'recover':
            io.n.COMMAND_STATE['deadline'] = time.monotonic() + 150
            print(m.encoded(m.recover(io, journal, record)).decode()); return
        status = live_status(io, record)
        if args.mode == 'status': print(m.encoded(status).decode()); return
        original = m.analyzer().parse((directory / 'ORIGINAL.json').read_bytes())
        request, wrapper, hashes = m.analyzer().archive_inputs(args.trial, args.trial_sha256)
        m.live_gate(status, record, minimum_seconds=300)
        handoff = m.receipt(record, original, request, wrapper, bundle)
        atomic(directory / 'HANDOFF.json', m.encoded(handoff))
        atomic(directory / 'TRIAL_INPUTS.json', m.encoded({'archive_sha256': args.trial_sha256, 'hashes': hashes}))
        atomic(directory / 'expected-firewall.json', m.encoded(record['after']['firewall']))
        print(m.encoded(handoff).decode())
    finally:
        os.close(lock_fd)


if __name__ == '__main__':
    try: main()
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'status': 'STOP_REVIEW_REQUIRED', 'error': str(error), 'operational_approval': False}), file=sys.stderr)
        raise SystemExit(2)
