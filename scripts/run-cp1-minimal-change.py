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
import math
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


def load_checked_raw(path, expected):
    m.need(path and re.fullmatch('[0-9a-f]{64}', expected or ''), 'input path and SHA256 required')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        m.need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'input not regular')
        raw = stream.read(8 * 1024 * 1024 + 1)
    m.need(len(raw) <= 8 * 1024 * 1024 and m.digest(raw) == expected, 'input SHA256/size differs')
    return raw


def load_checked(path, expected):
    return m.analyzer().parse(load_checked_raw(path, expected))


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

    def unit_active(self, unit):
        result = self.n.command(['systemctl', 'is-active', unit + '.timer'])
        return result['rc'] == 0 and result['stdout'].strip() == 'active' and not result['stderr'].strip()

    def timer_active(self, retained=False):
        if getattr(self, 'retained_withdraw_at', None):
            hard = self.unit_active(self.unit + '-retention')
            return hard and (retained or self.unit_active(self.unit))
        return self.unit_active(self.unit)

    def arm_one(self, unit, withdraw, mode):
        seconds = math.ceil((m.instant(withdraw) - self.now()).total_seconds())
        m.need(seconds > 360 and self.directory is not None, 'deadline too close for bounded apply')
        argv = ['systemd-run', '--unit=' + unit, '--on-active=' + str(seconds) + 's',
                '--timer-property=AccuracySec=1s', '--property=Type=oneshot', '--property=User=root',
                '--property=TimeoutStartSec=180s', '--quiet', '/usr/bin/python3', '-I', '-B',
                str(self.directory / 'scripts/run-cp1-minimal-change.py'), mode, '--job', self.job_id]
        self.command(argv)

    def arm(self, withdraw, retained_withdraw=None):
        self.retained_withdraw_at = retained_withdraw
        # Both one-shot deadlines are armed before any firewall mutation. No
        # live rearming race, extension daemon or cancellation on acceptance.
        if retained_withdraw:
            self.arm_one(self.unit + '-retention', retained_withdraw, 'deadline')
        self.arm_one(self.unit, withdraw, 'deadline' if retained_withdraw else 'recover')
        m.need(self.timer_active(), 'independent recovery timer not armed')

    def stage(self, kind, name, xml):
        directory = private(self.directory / 'xml' / kind, True)
        file = directory / (name + '.xml'); atomic(file, xml.encode())
        self.command(['firewall-cmd', '--permanent', '--new-' + SINGULAR[kind] + '-from-file=' + str(file), '--name=' + name])
        if kind == 'zones' and name == m.ZONE:
            # firewalld 2.4.3 may import the zone with forward=yes even when
            # an omitted XML field was read as false. Create the unbound
            # intermediate state explicitly, then force final forward=no
            # before the caller permits reload or policy creation.
            self.command(['firewall-cmd', '--permanent', '--zone=' + name, '--remove-forward'])

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
        units = [self.unit]
        if getattr(self, 'retained_withdraw_at', None):
            units.append(self.unit + '-retention')
        for unit in units:
            result = self.n.command(['systemctl', 'show', unit + '.timer', '--property=LoadState', '--value'])
            if result['stdout'].strip() == 'not-found' and not self.unit_active(unit):
                continue
            self.command(['systemctl', 'stop', unit + '.timer'])
            m.need(not self.unit_active(unit), 'recovery timer still active')


def root_document(directory, name):
    path = directory / name
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        st = os.fstat(stream.fileno())
        m.need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022, 'untrusted root document')
        raw = stream.read(8 * 1024 * 1024 + 1)
    m.need(len(raw) <= 8 * 1024 * 1024, 'root document too large')
    return m.analyzer().parse(raw)


def load_retention(io, record):
    if record.get('schema') not in ('fds.cp1-minimal-change.v2', 'fds.cp1-minimal-change.v3'):
        return None
    try:
        proof = root_document(io.directory, 'RETENTION.json')
    except FileNotFoundError:
        if record['schema'] != 'fds.cp1-minimal-change.v3':
            return None
        try:
            proof = root_document(io.directory, 'EXCEPTION_RETENTION.json')
        except FileNotFoundError:
            return None
        handoff = root_document(io.directory, 'HANDOFF.json')
        exception = root_document(io.directory, 'OPERATOR_EXCEPTION.json')
        return m.validate_exception_retention(proof, record, handoff, exception)
    handoff = root_document(io.directory, 'HANDOFF.json')
    acceptance = root_document(io.directory, 'ACCEPTANCE.json')
    return m.validate_retention(proof, record, handoff, acceptance)


def deadline_action(io, journal, record, proof):
    if record.get('cleanup_complete') is True:
        return record
    m.need(io.now() >= m.instant(record['withdraw_at']), 'deadline has not arrived')
    if proof is not None and io.now() < m.instant(record['retained_withdraw_at']) and io.timer_active(retained=True):
        provisional = proof.get('schema') == 'fds.cp1-exception-retention.v1'
        result = dict(status=m.PROVISIONAL if provisional else 'ACCEPTED_RETAINED', job_id=record['job_id'],
                      withdraw_at=record['retained_withdraw_at'], recovery_armed=True)
        if provisional:
            result.update(cp1_accepted=False, independent_review_status='PENDING')
        return result
    return m.recover(io, journal, record)


def commit_retention(io, record, bundle, acceptance, trial, trial_sha256):
    m.need(record.get('schema') in ('fds.cp1-minimal-change.v2', 'fds.cp1-minimal-change.v3'), 'old jobs cannot be extended or migrated')
    directory = io.directory
    handoff = root_document(directory, 'HANDOFF.json')
    existing = load_retention(io, record)
    if existing is not None and existing.get('schema') == 'fds.cp1-minimal-retention.v1':
        m.need(existing['acceptance_sha256'] == m.fingerprint(acceptance), 'retention already committed to different evidence')
        return live_status(io, record)
    original = root_document(directory, 'ORIGINAL.json')
    request, wrapper, _ = m.analyzer().archive_inputs(trial, trial_sha256)
    m.validate_handoff(handoff, original, request, wrapper, bundle, io.now())
    if existing is not None:
        # A genuine independent decision may supersede the provisional hold
        # only within the original acceptance window. Never backdate a review.
        m.need(io.now() + dt.timedelta(seconds=360) < m.instant(record['withdraw_at']),
               'too close to first deadline for independent acceptance')
        m.live_exception_gate(live_status(io, record), record, existing, io.now())
    else:
        m.live_gate(live_status(io, record), record, clock=io.now())
    proof = m.retention_proof(record, handoff, acceptance, io.now())
    atomic(directory / 'ACCEPTANCE.json', m.encoded(acceptance))
    # The commit marker is written last. CHANGE/HANDOFF hashes stay immutable;
    # interruption leaves the early deadline armed, including after file fsync.
    atomic(directory / 'RETENTION.json', m.encoded(proof))
    if io.now() >= m.instant(record['withdraw_at']):
        os.replace(directory / 'RETENTION.json', directory / 'RETENTION_LATE.json')
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        m.need(False, 'retention commit crossed the first deadline; early recovery remains required')
    return dict(status='RETENTION_COMMITTED', **proof)


def commit_operator_exception(io, record, bundle, exception, trial, trial_sha256, regression):
    m.need(record.get('schema') == 'fds.cp1-minimal-change.v3',
           'operator exception is for new source-reviewed v3 jobs only')
    directory = io.directory
    handoff = root_document(directory, 'HANDOFF.json')
    existing = load_retention(io, record)
    if existing is not None:
        m.need(existing.get('schema') == 'fds.cp1-exception-retention.v1' and
               existing.get('exception_sha256') == m.fingerprint(exception),
               'job already retained by different authority/evidence')
        return live_status(io, record)
    original = root_document(directory, 'ORIGINAL.json')
    request, wrapper, _ = m.analyzer().archive_inputs(trial, trial_sha256)
    m.validate_handoff(handoff, original, request, wrapper, bundle, io.now())
    m.validate_operator_exception(exception, handoff, io.now())
    checks = exception['checks']
    m.need(checks['positive']['evidence_sha256'] == trial_sha256 and
           checks['negative']['evidence_sha256'] == m.fingerprint(handoff['trial_report']) and
           checks['regression']['evidence_sha256'] == m.digest(regression), 'exception evidence hashes differ')
    expected = (b'MANAGEMENT_SSH=PASS\nENS160_ZONE=public\nKUBE_APISERVER_READYZ=PASS\n'
                b'CP1_NODE_READY=PASS\nEXTERNAL_DNS_IPV4=PASS\nEXTERNAL_HTTPS_REGISTRY_401=PASS\n'
                b'HOST_EXTERNAL_REGRESSION=PASS\n')
    m.need(regression == expected, 'expected HOST/external regression evidence missing')
    m.live_gate(live_status(io, record), record, clock=io.now())
    proof = m.exception_retention_proof(record, handoff, exception, io.now())
    atomic(directory / 'OPERATOR_EXCEPTION.json', m.encoded(exception))
    atomic(directory / 'EXCEPTION_REGRESSION.txt', regression)
    # Immutable CHANGE/HANDOFF and both timers stay intact. This new, root-only
    # marker explicitly records a pending review, never an independent approval.
    atomic(directory / 'EXCEPTION_RETENTION.json', m.encoded(proof))
    if io.now() >= m.instant(record['withdraw_at']):
        os.replace(directory / 'EXCEPTION_RETENTION.json', directory / 'EXCEPTION_RETENTION_LATE.json')
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        m.need(False, 'exception commit crossed first deadline; early recovery remains required')
    return dict(status=m.PROVISIONAL, **proof)


def live_status(io, record):
    if record.get('schema') == 'fds.cp1-minimal-change.v4':
        return session_status(io, record)
    proof = load_retention(io, record) if record.get('status') == 'ACTIVE_REVIEW_REQUIRED' else None
    cutoff = record['retained_withdraw_at'] if proof is not None else record['withdraw_at']
    if record.get('schema') in ('fds.cp1-minimal-change.v2', 'fds.cp1-minimal-change.v3') and record.get('status') == 'ACTIVE_REVIEW_REQUIRED':
        # A status snapshot owns the same lock as recovery and has a 360s
        # command budget. Do not start it across the effective recovery deadline.
        m.need(io.now() + dt.timedelta(seconds=360) < m.instant(cutoff), 'too close to recovery for a locked status snapshot')
    view = io.snapshot()
    active = m.validate_view(view, record['before'])
    status = record['status']
    armed = io.timer_active(retained=True) if proof is not None else io.timer_active()
    if status == 'ACTIVE_REVIEW_REQUIRED':
        m.need(active and armed and view == record['after'] and io.now() < m.instant(cutoff),
               'live change differs/expired/recovery absent')
    provisional = proof is not None and proof.get('schema') == 'fds.cp1-exception-retention.v1'
    result = {'status': (m.PROVISIONAL if provisional else 'ACCEPTED_RETAINED') if proof is not None else status, 'job_id': record['job_id'], 'source_bundle_sha256': record['source_bundle_sha256'],
            'change_receipt_sha256': m.fingerprint(record), 'withdraw_at': cutoff,
            'current': view, 'recovery_armed': armed, 'observed_at': io.now().isoformat()}
    if provisional:
        result.update(exception_sha256=proof['exception_sha256'], handoff_sha256=proof['handoff_sha256'],
                      cp1_accepted=False, independent_review_status='PENDING')
    elif proof is not None:
        result.update(acceptance_sha256=proof['acceptance_sha256'], handoff_sha256=proof['handoff_sha256'])
    return result


def load_session(io, record):
    try:
        proof = root_document(io.directory, 'SESSION_PROOF.json')
    except FileNotFoundError:
        return None
    return m.validate_session_proof(proof, record, root_document(io.directory, 'HANDOFF.json'),
                                    root_document(io.directory, 'SESSION_DECISION.json'))


def session_status(io, record):
    proof = load_session(io, record) if record.get('status') == 'ACTIVE_REVIEW_REQUIRED' else None
    if record.get('status') == 'ACTIVE_REVIEW_REQUIRED':
        m.need(io.now() + m.dt.timedelta(seconds=360) < m.instant(record['withdraw_at']),
               'too close to recovery for locked session snapshot')
    view = io.snapshot(); active = m.validate_view(view, record['before'])
    armed = io.timer_active()
    if record.get('status') == 'ACTIVE_REVIEW_REQUIRED':
        m.need(active and armed and view == record['after'] and io.now() < m.instant(record['withdraw_at']),
               'session state differs or final recovery unavailable')
    result = {'status': m.SESSION_ACTIVE if proof is not None else record['status'],
              'job_id': record['job_id'], 'source_bundle_sha256': record['source_bundle_sha256'],
              'change_receipt_sha256': m.fingerprint(record), 'withdraw_at': record['withdraw_at'],
              'current': view, 'recovery_armed': armed, 'observed_at': io.now().isoformat(),
              'cp1_accepted': False, 'independent_acceptance': 'PENDING'}
    if proof is not None:
        result.update(session_decision_sha256=proof['decision_sha256'], handoff_sha256=proof['handoff_sha256'])
    return result


def commit_session(io, record, bundle, decision, trial, trial_sha256, regression):
    m.need(record.get('schema') == 'fds.cp1-minimal-change.v4', 'session continuation requires a new v4 job')
    existing = load_session(io, record)
    if existing is not None:
        m.need(existing['decision_sha256'] == m.fingerprint(decision), 'session already committed to different decision')
        return session_status(io, record)
    handoff = root_document(io.directory, 'HANDOFF.json')
    original = root_document(io.directory, 'ORIGINAL.json')
    request, wrapper, _ = m.analyzer().archive_inputs(trial, trial_sha256)
    m.validate_handoff(handoff, original, request, wrapper, bundle, io.now())
    m.validate_session_decision(decision, handoff, io.now())
    checks = decision['checks']
    m.need(checks['positive']['evidence_sha256'] == trial_sha256 and
           checks['negative']['evidence_sha256'] == m.fingerprint(handoff['trial_report']) and
           checks['regression']['evidence_sha256'] == m.digest(regression), 'session evidence hashes differ')
    expected = (b'MANAGEMENT_SSH=PASS\nENS160_ZONE=public\nKUBE_APISERVER_READYZ=PASS\n'
                b'CP1_NODE_READY=PASS\nEXTERNAL_DNS_IPV4=PASS\nEXTERNAL_HTTPS_REGISTRY_401=PASS\n'
                b'HOST_EXTERNAL_REGRESSION=PASS\n')
    m.need(regression == expected, 'HOST/external regression evidence incomplete')
    m.live_gate(session_status(io, record), record, clock=io.now())
    proof = m.session_proof(record, handoff, decision, io.now())
    atomic(io.directory / 'SESSION_DECISION.json', m.encoded(decision))
    atomic(io.directory / 'SESSION_REGRESSION.txt', regression)
    atomic(io.directory / 'SESSION_PROOF.json', m.encoded(proof))
    if io.now() >= m.instant(record['withdraw_at']):
        os.replace(io.directory / 'SESSION_PROOF.json', io.directory / 'SESSION_PROOF_LATE.json')
        fd = os.open(io.directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        m.need(False, 'session commit crossed final deadline')
    return session_status(io, record)


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
    parser.add_argument('mode', nargs='?', default='plan', choices=('plan', 'inspect', 'apply', 'status', 'recover', 'deadline', 'issue-receipt', 'accept-retention', 'retain-by-exception', 'continue-session'))
    for name in ('original', 'authorization', 'inspection', 'acceptance', 'exception', 'regression', 'decision'):
        parser.add_argument('--' + name); parser.add_argument('--' + name + '-sha256')
    parser.add_argument('--job'); parser.add_argument('--trial'); parser.add_argument('--trial-sha256')
    args = parser.parse_args(); os.umask(0o077)
    files = source_files(); bundle = m.fingerprint(files)
    if args.mode == 'plan':
        print(json.dumps({'mode': 'PLAN_ONLY', 'hostname': m.HOST, 'block': m.BLOCK,
                          'source_bundle_sha256': bundle, 'files': files, 'operational_approval': False,
                          'new_workflow': 'v4-reviewed-cp-session', 'old_job_migration': False,
                          'exception_policy': m.EXCEPTION_POLICY, 'session_policy': m.SESSION_POLICY,
                          'provisional_is_accepted': False}, indent=2)); return
    m.need(os.geteuid() == 0, 'invoke as normal account with sudo; root login is not required')
    private(BASE, True)
    lock_fd = os.open(BASE / 'LOCK', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        m.need(os.fstat(lock_fd).st_uid == 0 and stat.S_ISREG(os.fstat(lock_fd).st_mode) and
               not os.fstat(lock_fd).st_mode & 0o077, 'untrusted lock')
        deadline = time.monotonic() + (30 if args.mode in ('recover', 'deadline') else 0)
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
        io.retained_withdraw_at = record.get('retained_withdraw_at')
        if args.mode == 'continue-session':
            decision = load_checked(args.decision, args.decision_sha256)
            regression = load_checked_raw(args.regression, args.regression_sha256)
            print(m.encoded(commit_session(io, record, bundle, decision, args.trial,
                                          args.trial_sha256, regression)).decode()); return
        if args.mode == 'recover':
            io.n.COMMAND_STATE['deadline'] = time.monotonic() + 150
            print(m.encoded(m.recover(io, journal, record)).decode()); return
        if args.mode == 'deadline':
            m.need(record.get('schema') in ('fds.cp1-minimal-change.v2', 'fds.cp1-minimal-change.v3'), 'deadline requires a workday job')
            try:
                proof = load_retention(io, record) if record.get('cleanup_complete') is not True else None
            except (ValueError, OSError, KeyError, TypeError, AttributeError, OverflowError):
                # An absent/partial/invalid acceptance must never suppress recovery.
                proof = None
            print(m.encoded(deadline_action(io, journal, record, proof)).decode()); return
        if args.mode == 'accept-retention':
            acceptance = load_checked(args.acceptance, args.acceptance_sha256)
            print(m.encoded(commit_retention(io, record, bundle, acceptance, args.trial, args.trial_sha256)).decode()); return
        if args.mode == 'retain-by-exception':
            exception = load_checked(args.exception, args.exception_sha256)
            regression = load_checked_raw(args.regression, args.regression_sha256)
            print(m.encoded(commit_operator_exception(io, record, bundle, exception, args.trial,
                                                      args.trial_sha256, regression)).decode()); return
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
