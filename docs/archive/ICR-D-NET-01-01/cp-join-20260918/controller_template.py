#!/usr/bin/env python3
"""Review candidate: one onsite invocation; no VM access from the build host."""
import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tarfile
import time
import zlib

PAYLOADS = {}  # REPLACED_BY_BUILD
CONTRACT = {}  # REPLACED_BY_BUILD
REPO = '321Team/infra-kubernetes'
REVIEWER = 'hayeounglee'
REVIEWER_ID = 95111439
REMOTE = {'k8s-control01': '10.1.93.102', 'k8s-control02': '10.1.93.103', 'k8s-control03': '10.1.93.104'}
OLD_JOB = 'c564ae97530dfe80'
OLD_PINS = {
    'expected_change_sha256': '27c75c6e9f606800a2cc49ee271ea3d8a91a2dffd1550ed81286e57e9e824585',
    'handoff_sha256': 'c3cf97941c147146183b5c5342d98ada706611725f12a899f417d08f5bf51417',
    'session_decision_sha256': '22a7cc8a41a43eaf3bdd56a5833918c7a16d8417ce56913e0aa34785932002c8'
}


def need(ok, message):
    if not ok: raise ValueError(message)


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def gh(path):
    r = subprocess.run(['gh', 'api', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
    need(r.returncode == 0, 'GitHub read failed')
    return json.loads(r.stdout)


def verify_review(pr_number, source):
    need(re.fullmatch('[0-9a-f]{40}', source), 'exact source commit required')
    pr = gh(f'repos/{REPO}/pulls/{pr_number}')
    need(pr['head']['sha'] == source and pr['state'] == 'open' and not pr['draft'],
         'reviewed PR head/state differs; draft is not executable')
    reviews = []
    for page in range(1, 51):
        part = gh(f'repos/{REPO}/pulls/{pr_number}/reviews?per_page=100&page={page}')
        reviews.extend(part)
        if len(part) < 100: break
    else: raise ValueError('review pagination incomplete')
    owner = [r for r in reviews if r['user']['login'] == REVIEWER and r['user']['id'] == REVIEWER_ID
             and r['state'] in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED')]
    need(owner, 'new combined operation review absent')
    latest = max(owner, key=lambda r: r['id'])
    need(latest['state'] == 'APPROVED' and latest['commit_id'] == source,
         'new combined operation not approved at this source')
    content = gh(f'repos/{REPO}/contents/ops/cp-join-20260918/RUN_CP_JOIN.py?ref={source}')
    need(base64.b64decode(content['content']) == Path(__file__).read_bytes(), 'executed source differs from reviewed commit')
    contract_sha = hashlib.sha256(canonical(CONTRACT)).hexdigest()
    return {'schema': 'fds.cp-join-controller-verified-review.v1', 'repository': REPO,
            'source_commit': source, 'reviewed_commit': source, 'contract_sha256': contract_sha,
            'expected_contract_sha256': contract_sha, 'reviewer': REVIEWER, 'decision': 'APPROVED',
            'review_url': latest['html_url'], 'verified_by': 'controller-live-github-api',
            'reviewed_at': latest['submitted_at'], 'verified_at': now(),
            'scope': {'join': True, 'cp1_runtime_ownership_transfer': True,
            'success': CONTRACT['success'], 'failure': CONTRACT['failure'], 'deadline': CONTRACT['deadline']}}


def ssh_argv(node):
    return ['ssh', '-F', '/dev/null', '-i', '/home/server01_ansible/.ssh/p1_ansible_ed25519',
            '-b', '10.250.10.10', '-T', '-o', 'BatchMode=yes', '-o', 'PreferredAuthentications=publickey',
            '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=/home/server01_ansible/.ssh/known_hosts',
            '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none', '-o', 'ControlMaster=no',
            '-o', 'ControlPersist=no', '-o', 'ControlPath=none', '-o', 'ConnectTimeout=10',
            '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
            '-o', 'UpdateHostKeys=no', '-o', 'PermitLocalCommand=no', '-o', 'ForwardAgent=no',
            '-o', 'ClearAllForwardings=yes', 'svc_ansible@' + REMOTE[node],
            'sudo -n /usr/bin/python3 -I -B -']


def remote(node, name, request, timeout=180):
    payload = PAYLOADS[name]
    # Request travels on stdin, never argv/environment or a local secret file.
    script = ('import base64,json,zlib,sys\n'
              'ns={"__name__":"cp_join_payload"}\n'
              f'exec(zlib.decompress(base64.b64decode({payload!r})),ns)\n'
              f'request=json.loads({json.dumps(request)!r})\n'
              'try:\n'
              ' result=ns["main"](request)\n'
              ' print(json.dumps(result,sort_keys=True))\n'
              'except Exception as e:\n'
              ' print(json.dumps({"status":"STOP_REVIEW_REQUIRED","error_type":type(e).__name__}))\n'
              ' sys.exit(2)\n').encode()
    try:
        r = subprocess.run(ssh_argv(node), input=script, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError('REMOTE_OUTCOME_UNKNOWN; no automatic retry/reset: ' + node)
    need(r.returncode == 0, 'remote operation failed or outcome unknown: ' + node + '/' + request['mode'])
    need(len(r.stdout) <= 16 * 1024 * 1024, 'remote output limit')
    result = json.loads(r.stdout)
    need(result.get('status') != 'STOP_REVIEW_REQUIRED', 'remote rejected operation: ' + node)
    return result


def best_effort_failure_views(out, state, handoff_request):
    calls = [('FAILURE_TOPOLOGY.json', 'k8s-control01', 'cp_join_cluster.py', {'mode': 'topology'}, 90)]
    if handoff_request:
        calls.append(('FAILURE_HANDOFF.json', 'k8s-control01', 'cp1_join_handoff.py',
                      dict(handoff_request, mode='status'), 400))
    if state.get('current_node'):
        calls.append(('FAILURE_NODE.json', state['current_node'], 'cp_join_node.py',
                      {'mode': 'status', 'job_id': state['job_id'], 'node': state['current_node']}, 90))
    for filename, node, helper, request, timeout in calls:
        try: view = remote(node, helper, request, timeout)
        except Exception as error:
            view = {'status': 'OBSERVATION_INCOMPLETE', 'error_type': type(error).__name__}
        write_json(out / filename, view)


def topology_gate(view, expected):
    members = view['members']
    nodes = view['nodes']
    need({n['name'] for n in nodes} == set(expected), 'unexpected Node inventory')
    need({m['name'] for m in members} == set(expected), 'unexpected etcd member inventory')
    need(len(members) == len(expected) and all(not m['learner'] for m in members), 'voter set incomplete')
    need(all(n['ready'] and n['control_plane'] for n in nodes), 'control plane Node not Ready')
    need(next(n for n in nodes if n['name'] == 'k8s-control01')['uid'] == '75735bd5-937a-463f-a93f-a8e7e51fa7a1',
         'CP1 Node identity changed')
    need(next(m for m in members if m['name'] == 'k8s-control01')['id'] == '1755679419291778130',
         'CP1 etcd identity changed')
    for member in members:
        need(member['peer_urls'] == ['https://' + REMOTE[member['name']] + ':2380'], 'etcd peer identity changed')
    need(view['api_ready'] is True and all(r['health'] is True for r in view['endpoint_health']), 'cluster health failed')


def join_config(node, credentials):
    return {'apiVersion': 'kubeadm.k8s.io/v1beta4', 'kind': 'JoinConfiguration',
            'nodeRegistration': {'name': node, 'criSocket': 'unix:///run/containerd/containerd.sock'},
            'discovery': {'bootstrapToken': {'apiServerEndpoint': credentials['endpoint'],
            'token': credentials['token'], 'caCertHashes': ['sha256:' + credentials['ca_spki_sha256']]}},
            'controlPlane': {'localAPIEndpoint': {'advertiseAddress': REMOTE[node], 'bindPort': 6443},
                             'certificateKey': credentials['certificate_key']}}


def write_json(path, value):
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2); stream.write('\n')


def run(args, base_directory=Path('/home/server01_ansible')):
    os.umask(0o077)
    if not args.execute:
        print(json.dumps({'mode': 'PLAN_ONLY', 'contract': CONTRACT, 'vm_mutation': False}, indent=2)); return
    need(Path('/home/server01_ansible/.ssh/p1_ansible_ed25519').is_file(), 'run onsite on ansible01/server01_ansible')
    authority = verify_review(args.review_pr, args.source_commit)
    lockfd = os.open(base_directory / ('ICR_CP_Join_' + OLD_JOB + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    job = secrets.token_hex(8)
    out = base_directory / ('ICR_CP_Join_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + job)
    out.mkdir(mode=0o700)
    state = {'schema': 'fds.cp-join-controller-result.v1', 'job_id': job, 'started_at': now(),
             'status': 'STARTED', 'cp1_transferred': False, 'nodes_joined': [], 'recovery_complete': False,
             'independent_acceptance': 'PENDING', 'source_commit': args.source_commit}
    credentials = None
    credential_attempted = False
    handoff_request = None
    try:
        state['stage'] = 'START_CHECKS'
        write_json(out / 'AUTHORITY.json', authority)
        initial = remote('k8s-control01', 'cp_join_cluster.py', {'mode': 'inspect'})
        write_json(out / 'CP1_BEFORE.json', initial)
        topology_gate(initial['topology'], ['k8s-control01'])
        for node in ('k8s-control02', 'k8s-control03'):
            before = remote(node, 'cp_join_node.py', {'mode': 'inspect', 'job_id': job, 'node': node})
            write_json(out / (node + '_BEFORE.json'), before)
        handoff_request = dict(OLD_PINS, mode='inspect', job_id=OLD_JOB, authority=authority)
        write_json(out / 'TRANSFER_BEFORE.json', remote('k8s-control01', 'cp1_join_handoff.py', handoff_request, 400))
        # Fresh review recheck belongs to this invocation, not a new human approval.
        authority = verify_review(args.review_pr, args.source_commit)
        handoff_request.update(mode='transfer', authority=authority)
        state['stage'] = 'CP1_TRANSFER'
        write_json(base_directory / ('ICR_CP_Join_' + OLD_JOB + '.attempt.json'),
                   {'job_id': job, 'source_commit': args.source_commit, 'started_at': state['started_at'],
                    'evidence_directory': str(out), 'retry_policy': 'REVIEW_EXISTING_ATTEMPT_DO_NOT_DELETE'})
        transfer = remote('k8s-control01', 'cp1_join_handoff.py', handoff_request, 400)
        write_json(out / 'TRANSFER.json', transfer)
        need(transfer.get('join_may_start') is True, 'CP1 transfer not committed; no cleanup or Join')
        state['cp1_transferred'] = True
        credential_attempted = True
        state['stage'] = 'CREDENTIALS'
        credentials = remote('k8s-control01', 'cp_join_cluster.py', {'mode': 'credentials', 'job_id': job})
        expected = ['k8s-control01']
        for node in ('k8s-control02', 'k8s-control03'):
            current = remote('k8s-control01', 'cp_join_cluster.py', {'mode': 'topology'})
            topology_gate(current, expected)
            state['current_node'] = node
            state['stage'] = 'NODE_PREPARE'
            prepared = remote(node, 'cp_join_node.py', {'mode': 'prepare', 'job_id': job, 'node': node}, 300)
            write_json(out / (node + '_PREPARE.json'), prepared)
            need(prepared.get('status') == 'PREPARED', 'prepare not complete; no Join')
            state['stage'] = 'NODE_JOIN'
            print('JOIN_START=' + node, flush=True)
            result = remote(node, 'cp_join_node.py', {'mode': 'join', 'job_id': job, 'node': node,
                            'join_config': join_config(node, credentials)}, 1950)
            write_json(out / (node + '_JOIN.json'), result)
            need(result.get('status') == 'JOIN_COMMAND_SUCCEEDED_CLUSTER_VERIFICATION_REQUIRED',
                 'failed target retained for membership-aware recovery; next target not started')
            expected.append(node)
            state['stage'] = 'CLUSTER_CONVERGENCE'
            # Bounded convergence after normal kubeadm; no repeated diagnostics or reapply.
            deadline = time.monotonic() + 180
            while True:
                try:
                    current = remote('k8s-control01', 'cp_join_cluster.py', {'mode': 'topology'})
                    topology_gate(current, expected); break
                except ValueError:
                    if time.monotonic() >= deadline: raise
                    time.sleep(5)
            write_json(out / (node + '_AFTER.json'), current)
            state['nodes_joined'].append(node)
            print('JOIN_VERIFIED=' + node, flush=True)
        state['status'] = 'THREE_CP_READY_RUNTIME_RETAINED_REVIEW_REQUIRED'
        state['stage'] = 'CREDENTIAL_REVOCATION'
        state['recovery_complete'] = None  # no rollback is called on success
    except Exception as error:
        state['status'] = 'STOP_RECOVERY_REVIEW_REQUIRED'
        state['error_type'] = type(error).__name__
        # No reset or pre-join firewall rollback on uncertain membership/quorum.
        print('STOP: 현재 상태를 보존했습니다. 다음 노드 Join과 자동 reset은 실행하지 않습니다.', flush=True)
        best_effort_failure_views(out, state, handoff_request)
    finally:
        credentials = None
        if credential_attempted:
            try:
                write_json(out / 'CREDENTIAL_REVOCATION.json', remote('k8s-control01', 'cp_join_cluster.py',
                           {'mode': 'revoke', 'job_id': job}))
            except Exception as error:
                state['credential_cleanup'] = 'REVIEW_REQUIRED'
                state['credential_cleanup_error_type'] = type(error).__name__
                if state['status'] == 'THREE_CP_READY_RUNTIME_RETAINED_REVIEW_REQUIRED':
                    state['status'] = 'THREE_CP_READY_CREDENTIAL_CLEANUP_REQUIRED'
        state['ended_at'] = now()
        write_json(out / 'RESULT.json', state)
        (out / 'RUN_CP_JOIN.py').write_bytes(Path(__file__).read_bytes())
        hashes = ''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
                         for p in sorted(out.iterdir()) if p.is_file())
        (out / 'SHA256SUMS').write_text(hashes)
        archive = out.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar: tar.add(out, arcname=out.name)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(str(archive) + '.sha256').write_text(digest + '  ' + archive.name + '\n')
        print('STATUS=' + state['status'])
        print('ARCHIVE=' + str(archive))
        print('SIDECAR=' + str(archive) + '.sha256')
        os.close(lockfd)
    if state['status'] != 'THREE_CP_READY_RUNTIME_RETAINED_REVIEW_REQUIRED':
        raise SystemExit(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--review-pr', type=int)
    parser.add_argument('--source-commit')
    run(parser.parse_args())
