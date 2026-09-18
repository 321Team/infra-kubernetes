#!/usr/bin/env python3
"""Separate controller entry for an actual source review plus delegation comment.

The reviewed Runtime package is immutable. This entry has separate provenance;
it does not fabricate review bodies or claim its code received the source review.
Default is an offline plan. --execute uses the original orchestration and CLI.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import socket
import sys

VERSION = 'fds.cp-approved-comment-entry.v1'
SOURCE_COMMIT = 'cfae027fad62a36ee228da3217011fa0787c7486'
BUNDLE_SHA = 'd229b8ba40716ffb4baeacf9ea80a009dc28c8155a9d38f4f84961fa4da28a41'
MANIFEST_SHA = 'ef3d209589d4d47e448ffbbf151b7ed1ea9ba9318bd1dbcd92f64cdbdd694821'
GRANT_ID = 5723302935
REVIEWER_ID = 95111439
REPO = 'repos/321Team/infra-kubernetes'
PR_URL = 'https://github.com/321Team/infra-kubernetes/pull/14'
API_PR = 'https://api.github.com/repos/321Team/infra-kubernetes/pulls/14'
KIT_NAME = 'FDS_CP_Session_cfae027_v4.0'
UTC = dt.timezone.utc
HOME_DIR = Path('/home/server01_ansible')


def need(value, reason):
    if not value:
        raise ValueError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def instant(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(result.utcoffset() is not None, 'timestamp timezone missing')
    return result


def reviewer(row):
    return (row.get('user', {}).get('login') == 'hayeounglee'
            and row.get('user', {}).get('id') == REVIEWER_ID)


def verify_authority(pr, reviews, grant, clock):
    """Validate two real records independently; never modify either input."""
    need(pr.get('number') == 14 and pr.get('head', {}).get('sha') == SOURCE_COMMIT
         and pr.get('base', {}).get('repo', {}).get('full_name') == '321Team/infra-kubernetes',
         'PR head/repository differs from reviewed Runtime')
    decisions = [r for r in reviews if r.get('user', {}).get('login') == 'hayeounglee'
                 and r.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED')]
    need(decisions, 'independent source review missing')
    source = max(decisions, key=lambda r: (instant(r['submitted_at']), r['id']))
    need(reviewer(source) and source.get('state') == 'APPROVED'
         and source.get('commit_id') == SOURCE_COMMIT, 'latest independent source review is not APPROVED for this HEAD')
    need(type(source.get('id')) is int and source['id'] > 0
         and source.get('html_url') == PR_URL + '#pullrequestreview-' + str(source['id'])
         and source.get('pull_request_url') == API_PR, 'source review URL/PR differs')
    need(instant(source['submitted_at']) <= clock, 'future source review')
    need(reviewer(grant) and grant.get('id') == GRANT_ID
         and grant.get('html_url') == PR_URL + '#issuecomment-' + str(GRANT_ID)
         and grant.get('issue_url') == 'https://api.github.com/repos/321Team/infra-kubernetes/issues/14',
         'delegation comment identity/author/PR differs')
    need(grant.get('body') == 'CP_SESSION_DELEGATION_APPROVED / source ' + SOURCE_COMMIT,
         'exact source-bound delegation is missing or changed')
    need(instant(grant['created_at']) <= instant(grant['updated_at']) <= clock,
         'delegation timestamps invalid or future')
    # The existing Runtime CLI accepts a real issuecomment URL. Keep the formal
    # source review separately instead of pretending the delegation was its body.
    return {'reviewer': 'hayeounglee', 'review_url': grant['html_url'],
            'reviewed_at': grant['created_at'], 'policy': 'fds.cp-session-policy.v1',
            'source_commit': SOURCE_COMMIT, 'source_review_url': source['html_url'],
            'source_reviewed_at': source['submitted_at'], 'source_review_id': source['id'],
            'delegation_comment_id': GRANT_ID, 'authority_evidence': 'SOURCE_REVIEW_PLUS_DELEGATION_COMMENT'}


def verify_kit(kit):
    """Pin all imported code before importing any package module."""
    need(kit.is_dir() and not kit.is_symlink(), 'approved package directory missing or symlinked')
    root = kit.resolve()
    manifest = root / 'PACKAGE_SHA256SUMS'
    need(manifest.is_file() and not manifest.is_symlink()
         and digest(manifest.read_bytes()) == MANIFEST_SHA, 'approved package manifest differs')
    entries = {}
    for line in manifest.read_text().splitlines():
        value, name = line.split('  ', 1)
        path = Path(name)
        need(re.fullmatch('[0-9a-f]{64}', value) and not path.is_absolute()
             and '..' not in path.parts and name not in entries, 'invalid manifest entry')
        file = root / path
        need(file.is_file() and all(not p.is_symlink() for p in [file, *file.parents] if p != root)
             and root in file.resolve().parents and digest(file.read_bytes()) == value,
             'approved package bytes differ: ' + name)
        entries[name] = value
    return entries


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_live(authority):
    need(authority.api('user').get('login') == 'GWONUK', 'use the designated operator GitHub account')
    rows = []
    for page in range(1, 21):
        values = authority.api(f'{REPO}/pulls/14/reviews?per_page=100&page={page}')
        need(isinstance(values, list), 'invalid review page')
        rows.extend(values)
        if len(values) < 100:
            break
    else:
        raise ValueError('review history exceeds bounded reader')
    grant = authority.api(f'{REPO}/issues/comments/{GRANT_ID}')
    pr = authority.api(f'{REPO}/pulls/14')
    proof = verify_authority(pr, rows, grant, dt.datetime.now(UTC))
    source = next(r for r in rows if r['id'] == proof['source_review_id'])
    return proof, {'SOURCE_PR.json': pr, 'SOURCE_REVIEW.json': source,
                   'DELEGATION_COMMENT.json': grant, 'REVIEW_HISTORY.json': rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--execute', action='store_true')
    modes.add_argument('--check', action='store_true')
    existing = HOME_DIR / KIT_NAME
    parser.add_argument('--kit', type=Path, default=existing if existing.exists() else Path(__file__).resolve().parent / KIT_NAME)
    parser.add_argument('--window-end')
    parser.add_argument('--recovery-margin', type=int)
    args = parser.parse_args(argv)
    os.umask(0o077)
    manifest = verify_kit(args.kit)
    kit = args.kit.resolve()
    w = load_module('approved_original_workflow', kit / 'RUN_CP_SESSION.py')
    need(w.verify_package() == manifest, 'package manifest validation differs')
    binding = json.loads((kit / 'PACKAGE_BINDING.json').read_bytes())
    need(binding['source_commit'] == SOURCE_COMMIT and binding['source_bundle_sha256'] == BUNDLE_SHA
         and binding['review_pr'] == 14, 'Runtime binding differs')
    core_entry = load_module('approved_runtime_entry', kit / 'source/scripts/run-cp1-minimal-change.py')
    core = core_entry.m
    need(core.fingerprint(core_entry.source_files()) == BUNDLE_SHA, 'Runtime source bundle differs')
    provenance = {'schema': VERSION, 'entry_sha256': digest(Path(__file__).read_bytes()),
                  'runtime_source_commit': SOURCE_COMMIT, 'runtime_source_bundle_sha256': BUNDLE_SHA,
                  'runtime_package_manifest_sha256': MANIFEST_SHA,
                  'entry_code_covered_by_source_review': False}
    if not args.execute and not args.check:
        print(json.dumps(dict(provenance, mode='PLAN_ONLY', kit=str(kit), runtime_executed=False,
                              authority='real exact-HEAD APPROVED review plus exact delegation comment',
                              workflow='original CP1 recovery/inspect/apply/test/continuation, then CP2 and CP3'), indent=2))
        return 0
    authority = load_module('approved_original_authority', kit / 'cp_session_authority.py')
    proof, records = check_live(authority)
    if args.check:
        print(json.dumps({'status': 'SOURCE_AND_DELEGATION_VERIFIED', 'proof': proof,
                          'runtime_executed': False, 'provenance': provenance}, indent=2))
        return 0
    need(os.geteuid() != 0 and pwd.getpwuid(os.getuid()).pw_name == 'server01_ansible'
         and socket.gethostname().split('.')[0] == 'ansible01', 'run on ansible01 as server01_ansible')
    print('현재 KST=' + dt.datetime.now(w.KST).isoformat())
    if args.window_end is None:
        args.window_end = input('CP 작업·인계·복구를 마칠 당일 KST 종료시각 (HH:MM): ').strip()
    if args.recovery_margin is None:
        args.recovery_margin = int(input('복구 여유(초, 최소 180; 현장 대응 가능한 값): ').strip())
    end = w.parse_session_window(args.window_end, args.recovery_margin, dt.datetime.now(UTC))
    print('CP1/CP2/CP3 기존 백업·콘솔·보호·복구 경로, 종료시각까지 운영자 대응과 병행 변경 없음 확인.')
    print('GWONUK의 실제 작업창 결정 댓글을 한 번 기록합니다. 결과의 독립 인수는 PENDING입니다.')
    need(input('확인했다면 ICR-CP-SESSION-GO 입력: ').strip() == 'ICR-CP-SESSION-GO', 'operator conditions unconfirmed')
    # Refresh actual authority after interactive input, without another human gate.
    proof, records = check_live(authority)
    end = w.parse_session_window(end, args.recovery_margin, dt.datetime.now(UTC))
    with (kit / 'EXECUTION.lock').open('a') as execution_lock:
        fcntl.flock(execution_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        need(not (kit / 'EXECUTION_ATTEMPT.json').exists(), 'package already attempted execution; inspect existing evidence')
        directory = HOME_DIR / ('ICR_CP_Session_' + dt.datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '_' + secrets.token_hex(4))
        directory.mkdir(mode=0o700)
        for name, value in records.items():
            (directory / name).write_bytes(w.encoded(value))
        (directory / 'ENTRY_PROVENANCE.json').write_bytes(w.encoded(provenance))
        shutil.copy2(Path(__file__), directory / 'RUN_CP_SESSION_APPROVED.py')
        shutil.copy2(kit / 'PACKAGE_SHA256SUMS', directory / 'PACKAGE_SHA256SUMS')
        binding.update(proof)
        work = w.Work(binding, core, directory, manifest)
        success = False
        try:
            binding.update(authority.record_operator(binding, proof, end, args.recovery_margin, directory))
            (directory / 'EXECUTION_BINDING.json').write_bytes(w.encoded(binding))
            work.run(end, args.recovery_margin)
            handoff, decision = w.continuation(work)
            w.followup_nodes(work, handoff, decision)
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
