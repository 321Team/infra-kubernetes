#!/usr/bin/env python3
"""Default plan only. Explicit execution requires reviewed source and qualification.

Uses the existing approved controller identity, inventory and strict SSH checks.
"""
import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FILES = ('cni_diag_core.py', 'cp1-cni-diagnostic-node.py', 'node-firewall.py')
NODE = 'k8s-control01'
REMOTE_ROOT = '/var/lib/fds/icr-d-net-01-01/cni-diagnostic/'


def need(value, message):
    if not value:
        raise ValueError(message)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def execution_inputs(decision, qualification, commit, hashes):
    need(decision.get('schema') == 'fds.cp1-cni-diagnostic-decision.v1', 'Decision schema differs')
    need(decision.get('source_commit') == commit and decision.get('execution_approved') is True,
         'Exact execution HEAD decision required')
    for field in ('review_url', 'execution_decision_url'):
        need(re.fullmatch(r'https://github\.com/321Team/(?:infra-kubernetes|fds-project-baseline)/'
                          r'(?:pull|issues)/[0-9]+#(?:pullrequestreview|issuecomment)-[0-9]+',
                          decision.get(field, '')), 'Missing review/execution reference: ' + field)
    for field in ('operator', 'backup_reference', 'console_reference'):
        need(isinstance(decision.get(field), str) and decision[field].strip(), 'Missing ' + field)
    need(decision.get('cp1_hep_transition_not_started') is True and
         decision.get('same_cp1_change_conflict_clear') is True, 'Execution coordination not recorded')
    core = load('cni_core', ROOT / 'scripts/cni_diag_core.py')
    need(qualification.get('schema') == 'fds.cp1-cni-qualification.v1' and
         qualification.get('stage') == 'RUNTIME_ISOLATED' and
         qualification.get('executor_bundle_sha256') == fingerprint(hashes),
         'Actual isolated integration evidence for these exact executor sources required; MOCK is insufficient')
    need(qualification.get('review_url') == decision.get('qualification_review_url') and
         qualification.get('review_url'), 'Qualification review reference missing')
    need(qualification.get('packages') == ['firewalld-2.4.3-4.el10.noarch',
         'nftables-1.1.5-6.el10.x86_64', 'NetworkManager-1.58.0-1.el10.x86_64',
         'iptables-nft-1.8.11-16.el10.x86_64'], 'Qualification platform differs from observed CP1')
    cases = qualification.get('cases', {})
    need(set(cases) == set(core.QUALIFICATION_CASES), 'Qualification case matrix incomplete')
    for case, record in cases.items():
        need(record.get('verdict') == 'PASS' and record.get('fresh_direct_flow') is True and
             record.get('ingress_arrival_observed') is True, 'Unverified integration case: ' + case)
        for key in ('packet_capture_sha256', 'nft_trace_sha256'):
            need(re.fullmatch(r'[0-9a-f]{64}', record.get(key, '')), 'Missing packet/trace evidence hash')
    for case in ('disconnect', 'killed_after_mutation', 'deadline', 'reboot', 'cleanup_retry', 'nm_ifcfg_unchanged'):
        need(qualification.get('recovery', {}).get(case) == 'PASS', 'Recovery integration incomplete: ' + case)
    need(qualification.get('all_owned_objects_absent') is True, 'Integration cleanup not verified')
    # Contents are independently reviewed evidence, not a locally generated attestation.
    # The controller verifies attached files' hashes, and never synthesizes PASS records.


def verify_evidence_files(qualification, directory):
    hashes = set()
    for name, expected in qualification.get('files_sha256', {}).items():
        path = directory / name
        need(not Path(name).is_absolute() and '..' not in Path(name).parts and not path.is_symlink(),
             'Unsafe qualification evidence path')
        need(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
             'Qualification evidence file missing or differs: ' + name)
        hashes.add(expected)
    for record in qualification['cases'].values():
        need({record['packet_capture_sha256'], record['nft_trace_sha256']} <= hashes,
             'Referenced packet/trace artifact not supplied')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--execute', action='store_true')
    group.add_argument('--collect', metavar='RUN_ID')
    group.add_argument('--recover', metavar='RUN_ID')
    parser.add_argument('--decision', type=Path)
    parser.add_argument('--qualification', type=Path)
    args = parser.parse_args()
    hashes = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in FILES}
    if not (args.execute or args.collect or args.recover):
        print(json.dumps({'mode': 'PLAN_ONLY', 'node': NODE, 'duration_seconds': 1200,
                          'cleanup_reserve_seconds': 180, 'bundle_sha256': fingerprint(hashes),
                          'files_sha256': hashes, 'requires_execution_head_review': True,
                          'requires_actual_isolated_qualification': True,
                          'purpose': 'DIAGNOSTIC_ONLY', 'authorizes_cp1_acceptance': False}, indent=2))
        return
    controller = load('existing_controller', ROOT / 'scripts/run-node-firewall.py')
    need(socket.gethostname().split('.')[0] == 'ansible01', 'Run on ansible01')
    commit = controller.verify_source()
    # Reject untracked/replaced diagnostic sources even when an older controller's
    # generic clean-check ignores untracked files. No SOURCE_LOCK shortcut here.
    for name in FILES + ('run-cp1-cni-diagnostic.py',):
        relative = 'scripts/' + name
        committed = controller.run(['git', 'show', commit + ':' + relative], cwd=ROOT)
        need(committed.returncode == 0 and committed.stdout == (ROOT / relative).read_text(),
             'Diagnostic file is not exactly the committed source: ' + relative)
    preflight = controller.preflight()
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix='FDS_CP1_CNI_Diagnostic_', dir='/home/server01_ansible'))
    data = {'source_commit': commit, 'preflight': preflight, 'files_sha256': hashes,
            'purpose': 'DIAGNOSTIC_ONLY', 'acceptance_criteria_credit': False}
    code = ('import socket,os,json;print(json.dumps({"host":socket.gethostname().split(".")[0],'
            '"connection":os.environ.get("SSH_CONNECTION","").split()}))')
    identity = controller.run(controller.ssh_argv(NODE, ['/usr/bin/python3', '-I', '-c', code]), timeout=25)
    observed = json.loads(identity.stdout)
    need(identity.returncode == 0 and observed['host'] == NODE and len(observed['connection']) == 4 and
         observed['connection'][0] == controller.SOURCE_IP and observed['connection'][2] == controller.node.CP[NODE],
         'Remote SSH source/destination identity differs')
    run_id = args.collect or args.recover
    try:
        if args.execute:
            need(args.decision and args.qualification, '--execute requires --decision and --qualification')
            decision = json.loads(args.decision.read_text())
            qualification = json.loads(args.qualification.read_text())
            execution_inputs(decision, qualification, commit, hashes)
            verify_evidence_files(qualification, args.qualification.parent)
            run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S') + '-' + secrets.token_hex(6)
            request = {'run_id': run_id, 'namespace': 'fds-cni-' + run_id, 'source_commit': commit,
                       'issued_at': time.time(), 'decision': decision, 'qualification': qualification,
                       'files_sha256': hashes}
            data['request'] = request
            payload = {'request': request, 'sources': {name: (ROOT / 'scripts' / name).read_text() for name in FILES}}
            script = 'INSTALL_DATA = ' + repr(payload) + '\n' + payload['sources'][FILES[1]]
            # Installation/launch does not wait on the diagnostic process or own its lifetime.
            response = controller.run(controller.ssh_argv(NODE, ['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-']),
                                      input=script, timeout=90)
            need(response.returncode == 0, 'Launch outcome unknown; collect this run, do not start another')
            data['launch'] = json.loads(response.stdout)
            job = REMOTE_ROOT + run_id
            until = time.monotonic() + min(1200, max(1, request['issued_at'] + 1200 - time.time()))
            last_phase = None
            while time.monotonic() < until:
                remote = ['sudo', '-n', '/usr/bin/python3', '-I', '-B', job + '/' + FILES[1]]
                polled = controller.run(controller.ssh_argv(NODE, remote + ['poll', job]), timeout=25)
                need(polled.returncode == 0, 'SSH polling unavailable; local recovery remains armed')
                state = json.loads(polled.stdout)
                if state.get('phase') != last_phase:
                    last_phase = state.get('phase'); print('PHASE=' + str(last_phase), flush=True)
                if state.get('phase') == 'WAIT_CONTROLLER_SSH':
                    ack = controller.run(controller.ssh_argv(NODE, remote + ['ack', job, state['management_nonce']]), timeout=15)
                    need(ack.returncode == 0, 'Management acknowledgement failed; local recovery will withdraw')
                    data['management_ssh_observed_at'] = time.time()
                if state.get('cleanup_complete') or state.get('phase') == 'RECOVERY_REQUIRED':
                    break
                time.sleep(2)
            response = controller.run(controller.ssh_argv(NODE, remote + ['status', job]), timeout=30)
        else:
            need(re.fullmatch(r'[0-9]{14}-[0-9a-f]{12}', run_id), 'Invalid run ID')
            job = REMOTE_ROOT + run_id
            if args.recover:
                remote = ['sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block',
                          'fds-cni-recover-' + run_id + '.service']
            else:
                # Do not execute a newer local copy as the old job's recovery source.
                remote = ['sudo', '-n', '/usr/bin/python3', '-I', '-B',
                          job + '/' + FILES[1], 'status', job]
            response = controller.run(controller.ssh_argv(NODE, remote), timeout=30)
        data.update(rc=response.returncode, stderr=controller.node.redact(response.stderr)[:4000])
        need(response.returncode == 0, 'Remote response unavailable; collect existing run before retrying')
        data['result'] = json.loads(response.stdout) if response.stdout.strip() else {'recovery_requested': True}
        for name, entry in data['result'].pop('files', {}).items():
            need(name in {'STATE.json', 'REQUEST.json', 'ARMED.json', 'ACK.json', 'COMMANDS.jsonl', 'ACTIVE_NFT.json',
                          'negative-a.pcap', 'negative-b.pcap'}, 'Unexpected evidence filename')
            raw = base64.b64decode(entry['base64'], validate=True)
            need(hashlib.sha256(raw).hexdigest() == entry['sha256'], 'Transport evidence digest mismatch')
            (directory / ('REMOTE_' + name)).write_bytes(raw)
    except Exception as error:
        data['error'] = controller.node.redact(str(error))
    finally:
        data['run_id'] = run_id
        (directory / 'RESULT.json').write_text(json.dumps(data, indent=2) + '\n')
        archive, sidecar = controller.archive_output(directory)
        print('RUN_ID=' + str(run_id))
        print('STATUS=' + ('INCOMPLETE' if 'error' in data else data['result'].get('status', 'REVIEW_REQUIRED')))
        print('ARCHIVE=' + str(archive))
        print('SIDECAR=' + str(sidecar))
        if run_id:
            print('COLLECT=python3 scripts/run-cp1-cni-diagnostic.py --collect ' + run_id)


if __name__ == '__main__':
    main()
