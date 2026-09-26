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

PAYLOADS = {'cp_join_cluster.py': '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', 'cp_join_node.py': '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', 'cp1_join_handoff.py': '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'}
CONTRACT = {'schema': 'fds.cp-join-operation-contract.v1', 'operator': 'GWONUK', 'reviewer': 'hayeounglee', 'targets': ['k8s-control02', 'k8s-control03'], 'existing_cp1_job': 'c564ae97530dfe80', 'cleanup': 'BACKUP_ETC_KUBERNETES_AND_QUARANTINE_THREE_PINNED_CP2_MANIFESTS', 'join': 'CP2_THEN_CP3_WITH_NORMAL_KUBEADM_PREFLIGHT', 'cp1_runtime_ownership_transfer': True, 'success': 'RETAIN_JOINED_CLUSTER_AND_RUNTIME', 'failure': 'PHASE_OWNED_RECOVERY_ONLY', 'deadline': 'NO_AUTOMATIC_WITHDRAWAL', 'retention_end': 'EXPLICIT_REPLACEMENT_PROTECTION_HANDOFF_OR_MEMBERSHIP_AWARE_WITHDRAWAL', 'independent_acceptance': 'PENDING', 'permanent_promotion': False, 'worker_join': False, 'automatic_reset': False, 'automatic_etcd_member_removal': False, 'automatic_snapshot_restore': False}
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
