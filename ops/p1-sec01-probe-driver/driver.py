#!/usr/bin/env python3
"""Probe-only host driver. No SSH, token issuance, retries or name-based cleanup.

Permit validation checks an operator-supplied record, NOT GitHub authenticity.
See README: independent live GO verification is a mandatory operator step.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time

SCHEMA = 'fds.sec01.probe-driver.v1'
HOST = 'k8s-control01'
STATE = Path('/root/sec01-probe-driver-state')
KC = 'kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s'
FILES = ('probe.sh', 'backup.sh', 'common.sh', 'checks.py')
MODES = ('precheck', 'run')
# Includes the existing script's precheck/observation/diagnostics/cleanup/post/seal,
# plus scheduling overhead. This driver never kills cleanup to meet a window.
BUDGET = {'precheck': 240, 'run': 1380}
HEX = re.compile(r'[0-9a-f]{64}')
GO = re.compile(r'https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-[0-9]+')


class Stop(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Stop(message)


def utc():
    return datetime.now(timezone.utc)


def stamp(value):
    need(isinstance(value, str), 'invalid timestamp')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Stop('invalid timestamp') from None
    need(result.utcoffset() is not None, 'timezone required')
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path, limit=16 * 1024 * 1024):
    """Bounded regular-file reads; no symlink or hardlink inputs."""
    path = Path(path)
    need(not path.is_symlink(), 'symlink input')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'not a single-link file')
        need(before.st_size <= limit, 'oversize file')
        data = f.read(limit + 1)
        after = os.fstat(f.fileno())
    need(len(data) == before.st_size and
         (before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
         (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'file changed during read')
    return data


def write_new(path, data):
    with Path(path).open('xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def private_dir(path, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    s = path.lstat()
    need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and
         stat.S_IMODE(s.st_mode) == 0o700, 'expected root-owned 0700 directory')


def bundle_check(bundle, expected):
    need(HEX.fullmatch(expected) is not None, 'invalid bundle SHA')
    raw = read(bundle / 'BUNDLE.json')
    need(digest(raw) == expected, 'bundle identity mismatch')
    meta = json.loads(raw)
    need(meta['schema'] == SCHEMA and re.fullmatch(r'[0-9a-f]{40}', meta['commit']), 'invalid source identity')
    wanted = {'driver.py'} | {'source/' + f for f in FILES}
    need(set(meta['files']) == wanted, 'unexpected bundle file set')
    need(not any(p.is_symlink() for p in bundle.rglob('*')), 'bundle symlink')
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    need(actual == wanted | {'BUNDLE.json'}, 'extra/missing bundle file')
    for name, sha in meta['files'].items():
        need(HEX.fullmatch(sha) is not None and digest(read(bundle / name)) == sha, 'source drift')
    need(digest(read(Path(__file__))) == meta['files']['driver.py'], 'running driver differs from bundle')
    return meta


def permit_check(p, mode, sha, commit, now):
    """Fail closed on binding/time errors. External approval remains operator verified."""
    need(p['schema'] == SCHEMA and p['mode'] == mode, 'permit mode mismatch')
    need(p['bundle_sha256'] == sha and p['source_commit'] == commit, 'permit source mismatch')
    need(p['host'] == HOST and p['scope'] == 'isolated-exec-probe-only', 'permit scope mismatch')
    need(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', p['session']) is not None, 'invalid session')
    need(p['executor'] in ('GWONUK', 'hayeounglee', 'supreme33-kr'), 'unknown executor')
    need(p['approver'] in ('GWONUK', 'hayeounglee', 'supreme33-kr') and
         p['approver'] != p['executor'], 'independent GO required')
    need(GO.fullmatch(p['go_url']) is not None, 'invalid GO reference')
    approved, start, last, end = map(stamp, (p['approved_at'], p['window_start'], p['latest_start'], p['window_end']))
    need(approved <= start <= now <= last < end, 'outside approved window')
    need((end - last).total_seconds() >= BUDGET[mode], 'insufficient full lifecycle budget')
    need(p['live_go_rechecked'] is True and p['notice_confirmed'] is True and
         p['no_overlap_confirmed'] is True, 'operator preconditions missing')
    checked = stamp(p['checked_at'])
    need(approved <= checked <= now and (now - checked).total_seconds() <= 60, 'stale operator confirmation')
    need(p['go_body_sha256'] == digest(p['go_body'].encode()), 'GO body mismatch')
    need(p['go_body'].strip() != '', 'empty GO body')
    return p


@contextmanager
def lock(state):
    import fcntl  # Linux only; offline validators are portable.
    private_dir(state, create=True)
    path = state / 'host.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+b') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid == 0 and
             stat.S_IMODE(s.st_mode) == 0o600, 'invalid lock file')
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('another driver holds host lock') from None
        yield


def no_other_tests(proc=Path('/proc')):
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / 'cmdline').read_bytes().split(b'\0')
        except FileNotFoundError:
            continue
        # Read errors other than a vanished process are NOT ignored.
        need(not any(Path(os.fsdecode(a)).name in ('probe.sh', 'backup.sh') for a in args),
             'existing Probe/Backup process; no automatic kill')


def receipt_check(work, mode, rc):
    """Inspect original seal + tar without extracting or rewriting either."""
    folders = [p for p in work.iterdir() if p.is_dir() and p.name.startswith('p1-sec01-ac06-probe-' + mode + '-')]
    need(len(folders) == 1, 'missing/ambiguous output directory')
    folder = folders[0]
    manifest = read(folder / 'SHA256SUMS')
    expected = {}
    for line in manifest.decode().splitlines():
        sha, name = line.split('  ', 1)
        need(HEX.fullmatch(sha) is not None and name not in expected and
             not name.startswith('/') and '\\' not in name and
             all(x not in ('', '.', '..') for x in name.split('/')), 'invalid output manifest')
        expected[name] = sha
    need(bool(expected), 'empty output manifest')
    need(not any(p.is_symlink() for p in folder.rglob('*')), 'output symlink')
    actual = {p.relative_to(folder).as_posix(): digest(read(p)) for p in folder.rglob('*')
              if p.is_file() and p != folder / 'SHA256SUMS'}
    need(actual == expected, 'output hash mismatch')
    side = read(Path(str(folder) + '.completion.txt')).decode()
    need(re.fullmatch(r'EXIT_CODE=' + str(rc) + r' REV=v2\.2-20261008 MODE=' + mode +
                      r' OUT=' + re.escape(folder.name) + r' SEALCHECK=' + re.escape(folder.name) + r'\.sealcheck\.txt\n', side),
         'completion/exit code mismatch')
    need(read(Path(str(folder) + '.sealcheck.txt')).decode().strip() == 'seal=OK', 'seal not confirmed')
    archive = Path(str(folder) + '.tar.gz')
    data = read(archive, 64 * 1024 * 1024)
    need(read(Path(str(archive) + '.sha256')).decode() == digest(data) + '  ' + archive.name + '\n', 'archive SHA mismatch')
    wanted = {folder.name + '/' + name: sha for name, sha in expected.items()}
    wanted[folder.name + '/SHA256SUMS'] = digest(manifest)
    observed = {}
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            if member.isdir():
                need(member.name == folder.name or member.name.startswith(folder.name + '/'), 'foreign tar directory')
                continue
            need(member.isfile() and member.name in wanted and member.name not in observed and
                 member.size <= 16 * 1024 * 1024, 'unexpected tar member')
            with tar.extractfile(member) as f:
                observed[member.name] = digest(f.read(member.size + 1))
    need(observed == wanted, 'archive member mismatch')
    return {'directory': folder.name, 'archive': archive.name,
            'archive_sha256': digest(data), 'files': len(expected), 'seal': 'VERIFIED'}


def precheck_check(session, sha, commit, now, mono, boot):
    result = json.loads(read(session / 'precheck' / 'result.json'))
    need(result['script_rc'] == 0 and result['driver_rc'] == 0, 'precheck failed')
    need(result['bundle_sha256'] == sha and result['source_commit'] == commit, 'precheck binding mismatch')
    need(result['boot_id'] == boot, 'host rebooted after precheck')
    need(0 <= mono - result['finished_monotonic'] <= 300 and
         0 <= (now - stamp(result['finished_at'])).total_seconds() <= 300, 'stale precheck')
    receipt_check(session / 'precheck', 'precheck', 0)
    return result


def call_probe(bundle, mode, work):
    # Allowlist environment: inherited KC/BASH_ENV/PYTHONPATH cannot change commands.
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LC_ALL': 'C.UTF-8',
           'KC': KC, 'CONFIRM': 'YES', 'PYTHONDONTWRITEBYTECODE': '1'}
    argv = ['/bin/bash', str(bundle / 'source' / 'probe.sh'), mode]
    child = None
    sent = False
    def forward(signum, _frame):
        nonlocal sent
        if not sent:
            sent = True
            # Signal only shell; its reviewed handler owns bounded UID cleanup.
            if child is not None:
                try:
                    child.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    pass  # Child has already finished; collect its actual exit code.
    old = {s: signal.signal(s, forward) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        with (work / 'stdout.txt').open('xb') as out, (work / 'stderr.txt').open('xb') as err:
            child = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=err, start_new_session=True)
            if sent:
                child.send_signal(signal.SIGTERM)
            # No outer short timeout: do not interrupt the existing finalization budget.
            return child.wait(), argv
    finally:
        for s, handler in old.items():
            signal.signal(s, handler)


def execute(bundle, sha, permit_path):
    need(sys.platform == 'linux' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == HOST,
         'execution requires k8s-control01 root through approved account path')
    os.umask(0o077)
    bundle = bundle.resolve(strict=True)
    private_dir(bundle)
    meta = bundle_check(bundle, sha)
    raw = read(permit_path)
    p = json.loads(raw)
    mode = p['mode']
    need(mode in MODES, 'unknown mode')
    with lock(STATE):
        no_other_tests()
        now = utc()
        permit_check(p, mode, sha, meta['commit'], now)
        session = STATE / p['session']
        private_dir(session, create=True)
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if mode == 'run':
            previous = precheck_check(session, sha, meta['commit'], now, time.monotonic(), boot)
            need(previous['executor'] == p['executor'], 'executor changed after precheck')
        claims = STATE / 'claims'
        private_dir(claims, create=True)
        # One permanent claim per GO-comment URL + mode, even across new session names.
        # Failed/unknown runs consume the claim too; only a NEW independent GO may retry.
        write_new(claims / digest((p['go_url'] + '/' + mode).encode()), raw)
        work = session / mode
        work.mkdir(mode=0o700)  # Existing mode folder is never reused.
        write_new(work / 'permit.json', raw)
        write_new(work / 'BUNDLE.json', read(bundle / 'BUNDLE.json'))
        started = utc().isoformat()
        write_new(work / 'started.json', json_bytes({'started_at': started, 'bundle_sha256': sha,
                  'source_commit': meta['commit'], 'executor': p['executor'], 'pid': os.getpid()}))
        bundle_check(bundle, sha)
        permit_check(p, mode, sha, meta['commit'], utc())
        rc, argv = call_probe(bundle, mode, work)
        result = {'schema': SCHEMA, 'mode': mode, 'source_commit': meta['commit'], 'bundle_sha256': sha,
                  'executor': p['executor'], 'started_at': started, 'finished_at': utc().isoformat(),
                  'finished_monotonic': time.monotonic(), 'boot_id': boot, 'argv': argv,
                  'script_rc': rc, 'driver_rc': rc, 'runtime_acceptance': 'NOT_EVALUATED'}
        try:
            bundle_check(bundle, sha)
            result['receipt'] = receipt_check(work, mode, rc)
        except (Stop, OSError, ValueError, tarfile.TarError):
            result['receipt'] = {'seal': 'UNVERIFIED'}
            result['driver_rc'] = 13 if rc == 13 else 12
        write_new(work / 'result.json', json_bytes(result))
        print(json.dumps({'session': str(session), 'mode': mode, 'script_rc': rc,
                          'driver_rc': result['driver_rc'], 'receipt': result['receipt']}))
        return result['driver_rc']


def collect(session, destination):
    """Offline/local file copy only. Preserve original archives and failure rc."""
    need(not destination.exists(), 'destination exists')
    need(not session.is_symlink(), 'session symlink')
    source = {}
    for mode in MODES:
        work = session / mode
        if not work.exists():
            continue
        result = json.loads(read(work / 'result.json'))
        need(result['mode'] == mode and result['receipt']['seal'] == 'VERIFIED', 'incomplete/unverified result; preserve in place')
        checked = receipt_check(work, mode, result['script_rc'])
        need(checked == result['receipt'], 'receipt changed')
        folder = checked['directory']
        names = ('permit.json', 'BUNDLE.json', 'started.json', 'result.json', 'stdout.txt', 'stderr.txt',
                 folder + '.tar.gz', folder + '.tar.gz.sha256', folder + '.completion.txt', folder + '.sealcheck.txt')
        for name in names:
            source[mode + '/' + name] = read(work / name, 64 * 1024 * 1024)
    need(bool(source), 'no completed modes to collect')
    os.umask(0o077)
    destination.mkdir(mode=0o700)
    for name, data in source.items():
        target = destination / name
        target.parent.mkdir(mode=0o700, exist_ok=True)
        write_new(target, data)
    write_new(destination / 'SHA256SUMS', ''.join(digest(b) + '  ' + n + '\n' for n, b in sorted(source.items())).encode())
    print(json.dumps({'files': len(source), 'output': str(destination), 'runtime_acceptance': 'NOT_EVALUATED'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    check = sub.add_parser('check-bundle')
    check.add_argument('bundle', type=Path); check.add_argument('sha256')
    run = sub.add_parser('execute')
    run.add_argument('bundle', type=Path); run.add_argument('sha256'); run.add_argument('permit', type=Path)
    export = sub.add_parser('collect')
    export.add_argument('session', type=Path); export.add_argument('destination', type=Path)
    a = parser.parse_args()
    if a.action == 'check-bundle':
        bundle_check(a.bundle, a.sha256); print('BUNDLE_BYTES=VERIFIED; GO=NOT_VERIFIED'); return 0
    if a.action == 'execute':
        return execute(a.bundle, a.sha256, a.permit)
    collect(a.session, a.destination)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Stop as error:
        print('STOP: ' + str(error) + '; preserve session/claims, do not retry.', file=sys.stderr)
        sys.exit(2)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        print('STOP: validation or I/O failure; preserve session/claims, do not retry or delete objects.', file=sys.stderr)
        sys.exit(2)
