#!/usr/bin/env python3
"""Operational entry: resume the approved CP2/CP3 path for one existing CP1 job.

This entry has its own provenance. The reviewed controller is pinned separately.
No CP1 apply, trial, continuation rewrite, timer change, or self-approval.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
FIX_COMMIT = '06286f31735a80982e50e5f94fd0e1f4a027eb8d'
ORIGINAL_COMMIT = 'cfae027fad62a36ee228da3217011fa0787c7486'
JOB = 'c564ae97530dfe80'
BUNDLE = 'd229b8ba40716ffb4baeacf9ea80a009dc28c8155a9d38f4f84961fa4da28a41'
KIT_SHA = '5b43d898e140d4708c818c31360467d6ffb24b8e200825f6f3a37af6eca63097'
READER_SHA = 'b235f5ad91b700d09a06fc1746e0d191ba241cdf495a1b0fe92694660f772471'
EVIDENCE_NAME = 'ICR_CP_Session_20260918T010834Z_72393be0.partial.tar.gz'
EVIDENCE_ROOT = 'ICR_CP_Session_20260918T010834Z_72393be0'
EVIDENCE_SHA = '7b0799cf8117a10371204e8265db8dc31e9bf325bb984f4dac1080d510ec3aed'
OPERATOR_COMMENT = 5723463564
HOME_DIR = Path('/home/server01_ansible')
REPO = 'repos/321Team/infra-kubernetes'


def need(value, reason):
    if not value:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def instant(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(result.utcoffset() is not None, 'timestamp timezone missing')
    return result


def parse(raw):
    def unique(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_fix_review(pr, reviews, clock):
    need(pr.get('number') == 15 and pr.get('head', {}).get('sha') == FIX_COMMIT
         and pr.get('base', {}).get('repo', {}).get('full_name') == '321Team/infra-kubernetes',
         'fix PR head/repository differs')
    decisions = [r for r in reviews if r.get('user', {}).get('login') == 'hayeounglee'
                 and r.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED')]
    need(decisions, 'fix source review missing')
    review = max(decisions, key=lambda r: (instant(r['submitted_at']), r['id']))
    need(review['state'] == 'APPROVED' and review.get('commit_id') == FIX_COMMIT
         and review['user'].get('id') == 95111439, 'latest fix review is not APPROVED for this source')
    need(review.get('html_url') == 'https://github.com/321Team/infra-kubernetes/pull/15#pullrequestreview-' + str(review['id'])
         and review.get('pull_request_url') == 'https://api.github.com/repos/321Team/infra-kubernetes/pulls/15'
         and instant(review['submitted_at']) <= clock, 'fix review provenance/time differs')
    return {'controller_source_review_url': review['html_url'],
            'controller_source_reviewed_at': review['submitted_at'],
            'controller_source_review_id': review['id'], 'controller_source_commit': FIX_COMMIT}


def verify_package():
    kit = ROOT / 'resume-kit'
    manifest = kit / 'PACKAGE_SHA256SUMS'
    need(manifest.is_file() and not manifest.is_symlink() and sha(manifest.read_bytes()) == KIT_SHA,
         'resume package manifest differs')
    entries = {}
    for line in manifest.read_text().splitlines():
        digest, name = line.split('  ', 1)
        path = kit / name
        need(re.fullmatch('[0-9a-f]{64}', digest) and not Path(name).is_absolute()
             and '..' not in Path(name).parts and name not in entries, 'unsafe manifest entry')
        need(path.is_file() and kit.resolve() in path.resolve().parents
             and not any(p.is_symlink() for p in [path, *path.parents])
             and sha(path.read_bytes()) == digest, 'resume source bytes differ: ' + name)
        entries[name] = digest
    reader = ROOT / 'tools/approved_authority_reader.py'
    need(reader.is_file() and not reader.is_symlink() and sha(reader.read_bytes()) == READER_SHA,
         'original authority reader differs')
    return kit, entries


def read_evidence(path):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 32 * 1024 * 1024,
         'original partial archive missing/unsafe')
    raw = path.read_bytes()
    need(sha(raw) == EVIDENCE_SHA, 'original partial archive SHA256 differs')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        members = archive.getmembers()
        need(len(members) < 500 and sum(m.size for m in members) < 32 * 1024 * 1024,
             'evidence archive exceeds bounds')
        seen = set()
        for item in members:
            name = Path(item.name)
            need(not name.is_absolute() and '..' not in name.parts and name.parts[0] == EVIDENCE_ROOT
                 and item.name not in seen and (item.isdir() or item.isfile()), 'unsafe evidence archive member')
            seen.add(item.name)
            if item.isfile():
                key = name.relative_to(EVIDENCE_ROOT).as_posix()
                files[key] = archive.extractfile(item).read()
    return files


def check_prior_failure(files):
    state = parse(files['RUN_STATE.json'])
    result = parse(files['CP2_APPLY/RESULT.json'])
    need(state.get('job_id') == JOB and state.get('source_commit') == ORIGINAL_COMMIT
         and state.get('cp1_session_committed') is True and state.get('passed_flows') == 5
         and state.get('independent_acceptance') == 'PENDING' and state.get('cp1_accepted') is False,
         'prior CP1 session outcome differs')
    need(result.get('hostname') == 'k8s-control02' and result.get('mode') == 'apply'
         and result.get('source_commit') == ORIGINAL_COMMIT and result.get('status') == 'STOP_REVIEW_REQUIRED'
         and result.get('runtime_applied') == result.get('permanent_applied') == 'not_attempted'
         and result.get('error') == 'remote result did not satisfy CP1_STATE_REVIEW_REQUIRED',
         'prior CP2 outcome is not the verified pre-mutation failure')
    need('CP2_APPLY/REQUEST.json' not in files and 'CP2_APPLY/TARGET.json' not in files
         and not any(name.startswith('CP3') for name in files), 'prior target mutation evidence exists')
    return state


def validate_prior(files, m, clock):
    state = check_prior_failure(files)
    original = parse(files['ORIGINAL_RECEIPT.stdout'])
    handoff = parse(files['HANDOFF.json'])
    decision = parse(files['SESSION_DECISION.json'])
    binding = parse(files['EXECUTION_BINDING.json'])
    need(binding.get('source_bundle_sha256') == BUNDLE and handoff['change']['job_id'] == JOB,
         'CP1 source/job binding differs')
    need(sha(files['ORIGINAL_RECEIPT.stdout']) == binding['original_receipt_sha256'], 'original receipt hash differs')
    trial_name = state['trial_archive']
    need(re.fullmatch(r'FDS_CP1_CNI_Trial_[A-Za-z0-9_]+\.tar\.gz', trial_name), 'unsafe prior trial filename')
    with tempfile.TemporaryDirectory(prefix='cp-resume-offline-') as temporary:
        trial = Path(temporary) / trial_name
        trial.write_bytes(files[trial_name])
        request, wrapper, _ = m.analyzer().archive_inputs(trial, state['trial_sha256'])
        record = m.validate_handoff(handoff, original, request, wrapper, BUNDLE, clock, decision)
        report = m.analyzer().analyze(request, wrapper)
    m.validate_session_decision(decision, handoff, clock)
    need(report == parse(files['FIVE_FLOW_ANALYSIS.json']) and report['passed_flows'] == 5 and not report['issues'],
         'prior trial analysis differs')
    need(decision['checks']['positive']['evidence_sha256'] == state['trial_sha256']
         and decision['checks']['negative']['evidence_sha256'] == m.fingerprint(handoff['trial_report'])
         and decision['checks']['regression']['evidence_sha256'] == sha(files['HOST_EXTERNAL_REGRESSION.stdout'])
         and b'HOST_EXTERNAL_REGRESSION=PASS' in files['HOST_EXTERNAL_REGRESSION.stdout'].splitlines(),
         'prior evidence digests differ')
    need(parse(files['CHANGE.json']) == record and record['withdraw_at'] == state['withdraw_at']
         and record['operator_decision_url'] == binding['operator_decision_url']
         and record['review_url'] == binding['review_url'], 'prior session state/authority differs')
    return state, binding, handoff, decision


def check_authority(api, reader, files):
    original, records = reader.check_live(api)
    reviews = []
    for page in range(1, 21):
        rows = api.api(f'{REPO}/pulls/15/reviews?per_page=100&page={page}')
        need(isinstance(rows, list), 'invalid fix review page')
        reviews.extend(rows)
        if len(rows) < 100:
            break
    else:
        raise ValueError('fix review history exceeds bounded reader')
    pr = api.api(f'{REPO}/pulls/15')
    fix = verify_fix_review(pr, reviews, dt.datetime.now(UTC))
    operator = api.api(f'{REPO}/issues/comments/{OPERATOR_COMMENT}')
    retained = parse(files['OPERATOR_GITHUB_DECISION.json'])
    need(operator.get('id') == OPERATOR_COMMENT and operator.get('user', {}).get('login') == 'GWONUK'
         and operator.get('html_url') == f'https://github.com/321Team/infra-kubernetes/pull/14#issuecomment-{OPERATOR_COMMENT}'
         and operator.get('issue_url') == 'https://api.github.com/repos/321Team/infra-kubernetes/issues/14'
         and operator.get('body') == retained['body'] and operator.get('created_at') == retained['created_at'],
         'existing operator decision changed/missing')
    records.update({'FIX_SOURCE_PR.json': pr, 'FIX_SOURCE_REVIEWS.json': reviews,
                    'EXISTING_OPERATOR_DECISION_LIVE.json': operator})
    return original, fix, records


def private_directory(path):
    for parent in reversed([path, *path.parents]):
        if not parent.exists() and not parent.is_symlink():
            parent.mkdir(mode=0o700)
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022,
             'unsafe resume state directory')


def claim_attempt(path, payload):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def run_followups(w, work, handoff, decision):
    # Deliberately use only the existing CP2 -> CP3 workflow.
    w.followup_nodes(work, handoff, decision)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--evidence', type=Path, default=HOME_DIR / EVIDENCE_NAME)
    args = parser.parse_args(argv)
    os.umask(0o077)
    kit, manifest = verify_package()
    w = load_module('resume_original_workflow', kit / 'RUN_CP_SESSION.py')
    need(w.verify_package() == manifest, 'manifest validators disagree')
    entry = load_module('resume_existing_cp1_core', kit / 'source/scripts/run-cp1-minimal-change.py')
    m = entry.m
    need(m.fingerprint(entry.source_files()) == BUNDLE, 'existing CP1 frozen source differs')
    lock = parse((kit / 'source/SOURCE_LOCK.json').read_bytes())
    need(lock['commit'] == FIX_COMMIT, 'reviewed controller source lock differs')
    files = read_evidence(args.evidence)
    state, binding, handoff, decision = validate_prior(files, m, dt.datetime.now(UTC))
    provenance = {'schema': 'fds.cp-resume-entry.v1', 'entry_sha256': sha(Path(__file__).read_bytes()),
                  'entry_code_covered_by_controller_source_review': False,
                  'controller_source_commit': FIX_COMMIT, 'original_cp1_source_commit': ORIGINAL_COMMIT,
                  'cp1_source_bundle_sha256': BUNDLE, 'prior_evidence_sha256': EVIDENCE_SHA,
                  'resume_package_manifest_sha256': KIT_SHA, 'job_id': JOB}
    if not args.execute:
        print(json.dumps(dict(provenance, mode='PLAN_ONLY', cp1_apply_replayed=False, trial_replayed=False,
                              timer_changed=False, withdraw_at=state['withdraw_at'], runtime_executed=False), indent=2))
        return 0
    need(os.geteuid() != 0 and w.pwd.getpwuid(os.getuid()).pw_name == 'server01_ansible'
         and w.socket.gethostname().split('.')[0] == 'ansible01', 'run on ansible01 as server01_ansible')
    api = load_module('resume_github_api', kit / 'cp_session_authority.py')
    reader = load_module('resume_original_authority_reader', ROOT / 'tools/approved_authority_reader.py')
    original_proof, fix_proof, records = check_authority(api, reader, files)
    need(original_proof['review_url'] == binding['review_url'], 'existing CP1 grant differs')
    binding.update(original_proof)
    binding.update(fix_proof)
    binding.update(source_commit=FIX_COMMIT, review_pr=15, original_cp1_source_commit=ORIGINAL_COMMIT)
    state_dir = HOME_DIR / '.local/state/fds-cp-session-resume' / JOB
    private_directory(state_dir)
    lock_path = state_dir / 'RESUME.lock'
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as run_lock:
        info = os.fstat(run_lock.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
             'unsafe resume lock')
        fcntl.flock(run_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = state_dir / 'CP2_APPLY_ATTEMPT.json'
        need(not marker.exists() and not marker.is_symlink(), 'this job already has a resume apply attempt; inspect result')
        directory = HOME_DIR / ('ICR_CP_Resume_' + dt.datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '_' + secrets.token_hex(4))
        directory.mkdir(mode=0o700)
        prior = directory / 'PRIOR_EVIDENCE'
        for name, raw in files.items():
            path = prior / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(raw)
        for name in ['HANDOFF.json', 'SESSION_DECISION.json', 'ORIGINAL_RECEIPT.stdout', 'CHANGE.json',
                     'HOST_EXTERNAL_REGRESSION.stdout', 'FIVE_FLOW_ANALYSIS.json', state['trial_archive'],
                     state['trial_archive'] + '.sha256']:
            (directory / name).write_bytes(files[name])
        for name, data in records.items():
            (directory / name).write_bytes(w.encoded(data))
        (directory / 'EXECUTION_BINDING.json').write_bytes(w.encoded(binding))
        (directory / 'ENTRY_PROVENANCE.json').write_bytes(w.encoded(provenance))
        shutil.copy2(Path(__file__), directory / 'RUN_CP_RESUME.py')
        shutil.copy2(kit / 'PACKAGE_SHA256SUMS', directory / 'PACKAGE_SHA256SUMS')

        class ResumeWork(w.Work):
            def step(self, name, argv, **kwargs):
                if name == 'CP2_APPLY':
                    claim_attempt(marker, {'job_id': JOB, 'evidence': str(directory),
                                           'controller_source_commit': FIX_COMMIT})
                return super().step(name, argv, **kwargs)

        work = ResumeWork(binding, m, directory, manifest)
        work.job = JOB
        work.state.update(state)
        work.state.update(status='RESUMING_CP2', source_commit=FIX_COMMIT, nodes={}, node_evidence=[],
                          resume_from_job=JOB, cp1_apply_replayed=False, trial_replayed=False, timer_changed=False)
        work.state.pop('error', None)
        success = False
        print('CP1_JOB=' + JOB + '\nEXISTING_WITHDRAW_AT=' + state['withdraw_at'], flush=True)
        try:
            frozen = w.CP1_BASE + '/' + JOB + '/scripts/run-cp1-minimal-change.py'
            live = m.analyzer().parse(work.runner('CP1_LIVE_BEFORE_RESUME', frozen, 'status', '--job', JOB))
            m.live_session_gate(live, handoff['change'], decision)
            (directory / 'CP1_LIVE_BEFORE_RESUME.json').write_bytes(w.encoded(live))
            run_followups(w, work, handoff, decision)
            success = True
        except (Exception, KeyboardInterrupt) as error:
            work.state.update(status='STOP_REVIEW_REQUIRED', error=str(error))
            print('[STOP] ' + str(error), file=sys.stderr)
        finally:
            work.finish(success)
    return 0 if success else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print('[STOP] ' + str(error), file=sys.stderr)
        raise SystemExit(2)
