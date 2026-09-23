"""One real GitHub policy review, one operator decision; never self-approval."""
import datetime as dt
import json
import re
import subprocess

REPO = 'repos/321Team/infra-kubernetes'
POLICY = 'fds.cp-session-policy.v1'
TARGETS = ['k8s-control01', 'k8s-control02', 'k8s-control03']
UTC = dt.timezone.utc


def need(value, reason):
    if not value:
        raise ValueError(reason)


def instant(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(result.utcoffset() is not None, 'timezone missing')
    return result


def api(endpoint, method='GET', payload=None):
    argv = ['gh', 'api', '--method', method, endpoint]
    raw = None
    if payload is not None:
        argv += ['--input', '-']
        raw = json.dumps(payload, ensure_ascii=False).encode()
    result = subprocess.run(argv, input=raw, capture_output=True, timeout=45)
    need(result.returncode == 0, 'GitHub request failed; credentials were not logged')
    return json.loads(result.stdout)


def approval(pr, reviews, binding, clock):
    head = binding['source_commit']; number = binding['review_pr']
    need(re.fullmatch('[0-9a-f]{40}', head) and type(number) is int and number > 0, 'invalid package identity')
    need(pr.get('number') == number and pr.get('head', {}).get('sha') == head and
         pr.get('base', {}).get('repo', {}).get('full_name') == '321Team/infra-kubernetes',
         'PR head/repository differs from package')
    decisions = [v for v in reviews if v.get('user', {}).get('login') == 'hayeounglee' and
                 v.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED')]
    need(decisions, 'WAITING_POLICY_DELEGATION_REVIEW')
    review = max(decisions, key=lambda v: (instant(v['submitted_at']), v['id']))
    token = 'CP_SESSION_DELEGATION_APPROVED / source ' + head
    need(review.get('state') == 'APPROVED' and review.get('commit_id') == head and
         token in (review.get('body') or '').splitlines(), 'WAITING_POLICY_DELEGATION_REVIEW')
    need(instant(review['submitted_at']) <= clock, 'future policy review')
    need(review.get('html_url') == f'https://github.com/321Team/infra-kubernetes/pull/{number}#pullrequestreview-{review["id"]}',
         'review URL mismatch')
    return {'reviewer': 'hayeounglee', 'review_url': review['html_url'],
            'reviewed_at': review['submitted_at'], 'policy': POLICY, 'source_commit': head}


def check(binding, clock=None):
    clock = clock or dt.datetime.now(UTC)
    need(api('user').get('login') == 'GWONUK', 'run with the designated operator GitHub account')
    number = binding['review_pr']; rows = []
    for page in range(1, 21):
        values = api(f'{REPO}/pulls/{number}/reviews?per_page=100&page={page}')
        need(isinstance(values, list), 'review page is invalid')
        rows.extend(values)
        if len(values) < 100:
            break
    else:
        raise ValueError('review history exceeds bounded reader')
    return approval(api(f'{REPO}/pulls/{number}'), rows, binding, clock)


def record_operator(binding, review, end, margin, evidence_directory):
    """Called only after the explicit local operator confirmation and real review."""
    body = ('CP_SESSION_OPERATOR_EXECUTION\n'
            + 'source=' + binding['source_commit'] + '\n'
            + 'source_bundle=' + binding['source_bundle_sha256'] + '\n'
            + 'policy=' + POLICY + '\n'
            + 'targets=' + ','.join(TARGETS) + '\n'
            + 'window_end=' + end + '\n'
            + 'recovery_margin_seconds=' + str(margin) + '\n'
            + 'scope=CP1 apply/test/continuation; CP2 then CP3 runtime firewall; no join/reset/HEP/Permanent promotion\n'
            + 'independent_acceptance=PENDING\n'
            + 'review=' + review['review_url'] + '\n'
            + 'operator_ack=existing backups, consoles, protection, rollback, staffed window and mutation freeze confirmed\n')
    row = api(f'{REPO}/issues/{binding["review_pr"]}/comments', 'POST', {'body': body})
    need(row.get('user', {}).get('login') == 'GWONUK' and row.get('body') == body and
         re.fullmatch(r'https://github.com/321Team/infra-kubernetes/pull/[1-9][0-9]*#issuecomment-[0-9]+', row.get('html_url', '')),
         'operator decision could not be verified')
    need(instant(review['reviewed_at']) <= instant(row['created_at']) <= dt.datetime.now(UTC),
         'operator decision timestamp invalid')
    (evidence_directory / 'OPERATOR_GITHUB_DECISION.json').write_text(json.dumps(row, ensure_ascii=False, indent=2) + '\n')
    return {'operator_decision_url': row['html_url'], 'operator_decided_at': row['created_at']}
