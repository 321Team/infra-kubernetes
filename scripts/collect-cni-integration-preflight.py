#!/usr/bin/env python3
"""Local read-only platform preparation; never creates execution qualification."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SAFE_PATH = '/usr/sbin:/usr/bin:/sbin:/bin'
LIMIT = 65536
PACKAGES = ['firewalld-2.4.3-4.el10.noarch', 'nftables-1.1.5-6.el10.x86_64',
            'NetworkManager-1.58.0-1.el10.x86_64', 'iptables-nft-1.8.11-16.el10.x86_64']
SOURCE_FILES = ('cni_diag_core.py', 'cp1-cni-diagnostic-node.py', 'node-firewall.py')
QUERIES = {
    'packages': ['rpm', '-q', 'firewalld', 'nftables', 'NetworkManager', 'iptables-nft'],
    'systemd_version': ['systemctl', '--version'],
    'virtual_machine': ['systemd-detect-virt', '--vm'],
    'container': ['systemd-detect-virt', '--container'],
    'firewalld_active': ['systemctl', 'is-active', 'firewalld.service'],
    'nm_active': ['systemctl', 'is-active', 'NetworkManager.service'],
    'firewalld_state': ['firewall-cmd', '--state'],
    'nft_version': ['nft', '--version'],
    'conntrack_version': ['conntrack', '-V'],
    'tcpdump_version': ['tcpdump', '--version'],
    'ip_version': ['ip', '-V'],
    'selinux': ['getenforce'],
}
REQUIRED_TOOLS = ('systemctl', 'systemd-run', 'systemd-detect-virt', 'firewall-cmd',
                  'nft', 'nmcli', 'ip', 'conntrack', 'tcpdump', 'rpm', 'python3')
FLOW_CASES = ('peer_8080', 'peer_8081', 'outside_pair', 'physical_spoof_ingress',
              'physical_egress', 'tunnel_ingress', 'existing_pod_egress')
RECOVERY_CASES = ('disconnect', 'killed_after_mutation', 'deadline', 'reboot',
                  'cleanup_retry', 'nm_ifcfg_unchanged')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_small(path):
    try:
        with open(path, 'rb') as stream:
            data = stream.read(LIMIT + 1)
        return {'text': data[:LIMIT].decode('utf-8', errors='replace'),
                'truncated': len(data) > LIMIT, 'error': None}
    except OSError as error:
        return {'text': '', 'truncated': False, 'error': type(error).__name__}


def query(argv, timeout):
    binary = shutil.which(argv[0], path=SAFE_PATH)
    record = {'argv': argv, 'binary': binary, 'returncode': None, 'timeout': False,
              'stdout': '', 'stderr': '', 'truncated': False, 'error': None}
    if not binary or timeout <= 0:
        record['error'] = 'MISSING_BINARY' if not binary else 'COLLECTION_BUDGET_EXHAUSTED'
        return record
    # Output is spooled to disk, and every command is in a separate process group.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen([binary, *argv[1:]], stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True,
                                       env={'PATH': SAFE_PATH, 'LC_ALL': 'C.UTF-8',
                                            'SYSTEMD_PAGER': 'cat', 'PAGER': 'cat'})
            try:
                record['returncode'] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                record['timeout'] = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                record['returncode'] = process.wait()
        except OSError as error:
            record['error'] = type(error).__name__
        for key, stream in [('stdout', out), ('stderr', err)]:
            stream.seek(0)
            raw = stream.read(LIMIT + 1)
            record[key] = raw[:LIMIT].decode('utf-8', errors='replace')
            record['truncated'] |= len(raw) > LIMIT
    return record


def assess(observed):
    blockers = []
    files = observed['host_files']
    for name, result in files.items():
        if result['error'] or result['truncated']:
            blockers.append('INCOMPLETE_HOST_FILE:' + name)
    if files['pid1']['text'].strip() != 'systemd':
        blockers.append('PID1_NOT_SYSTEMD')
    if observed['euid'] != 0:
        blockers.append('ROOT_COLLECTION_REQUIRED')
    fields = dict(line.split(':', 1) for line in files['self_status']['text'].splitlines() if ':' in line)
    try:
        capabilities = int(fields['CapEff'].strip(), 16)
    except (KeyError, ValueError):
        capabilities = 0
    for bit, name in [(12, 'NET_ADMIN'), (13, 'NET_RAW')]:
        if not capabilities & (1 << bit):
            blockers.append('MISSING_CAP_' + name)
    for name, location in observed['binaries'].items():
        if location is None:
            blockers.append('MISSING_TOOL:' + name)
    commands = observed['commands']
    for name, result in commands.items():
        allowed = (0, 1) if name == 'container' else (0,)
        if (result['error'] or result['timeout'] or result['truncated'] or
                result['returncode'] not in allowed):
            blockers.append('INCOMPLETE_OR_FAILED_QUERY:' + name)
    if commands['packages']['stdout'].splitlines() != PACKAGES:
        blockers.append('OBSERVED_CP1_PACKAGE_SET_MISMATCH')
    if commands['virtual_machine']['stdout'].strip() in ('', 'none'):
        blockers.append('VM_NOT_OBSERVED')
    if commands['container']['returncode'] != 1 or commands['container']['stdout'].strip() != 'none':
        blockers.append('CONTAINER_OR_UNKNOWN_CONTAINER_DETECTION')
    for name in ('firewalld_active', 'nm_active'):
        if commands[name]['stdout'].strip() != 'active':
            blockers.append('SERVICE_NOT_ACTIVE:' + name)
    if commands['firewalld_state']['stdout'].strip() != 'running':
        blockers.append('FIREWALLD_NOT_RUNNING')
    if commands['selinux']['stdout'].strip() != 'Enforcing':
        blockers.append('SELINUX_NOT_ENFORCING')
    if len(observed['executor_files_sha256']) != len(SOURCE_FILES):
        blockers.append('EXECUTOR_SOURCE_SET_INCOMPLETE')
    return {
        'status': 'PREPARATION_BLOCKED' if blockers else 'PREPARATION_REVIEW_REQUIRED',
        'blockers': blockers,
        'stage': 'PREPARATION_ONLY', 'runtime_integration_executed': False,
        'qualification_created': False, 'execution_authorized': False,
        'acceptance_criteria_credit': False,
        'isolation_verified': False,
        'manual_review_required': ['VM identity and network separation from production',
                                   'Approved isolated test entry and fixture provenance',
                                   'Packet path and independent recovery test results',
                                   'Exact executor source and evidence review'],
    }


def collect():
    observed = {'collected_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'hostname': platform.node(), 'kernel': platform.release(),
                'python': platform.python_version(), 'euid': os.geteuid(),
                'host_files': {name: read_small(path) for name, path in {
                    'os_release': '/etc/os-release', 'pid1': '/proc/1/comm',
                    'self_status': '/proc/self/status'}.items()},
                'binaries': {name: shutil.which(name, path=SAFE_PATH) for name in REQUIRED_TOOLS},
                'commands': {}, 'executor_files_sha256': {}}
    deadline = time.monotonic() + 50
    for name, argv in QUERIES.items():
        observed['commands'][name] = query(argv, min(4, deadline - time.monotonic()))
    for name in SOURCE_FILES:
        path = ROOT / 'scripts' / name
        if path.is_file():
            observed['executor_files_sha256'][name] = digest(path.read_bytes())
    observed['executor_bundle_sha256'] = digest(json.dumps(
        observed['executor_files_sha256'], sort_keys=True, separators=(',', ':')).encode())
    observed['collector_sha256'] = digest(Path(__file__).read_bytes())
    return observed


def write_artifacts(parent, observed):
    # Only a fresh output directory is written. No host/service/network mutations.
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='FDS_CNI_Integration_Preflight_', dir=parent))
    directory = root / 'evidence'
    directory.mkdir(mode=0o700)
    result = {'schema': 'fds.cni-integration-preparation.v1',
              **assess(observed), 'observed': observed}
    matrix = {'schema': 'fds.cni-integration-test-plan.v1', 'stage': 'NOT_EXECUTED',
              'executor_bundle_sha256': observed['executor_bundle_sha256'],
              'cases': {name: {'verdict': 'NOT_TESTED', 'evidence': []} for name in FLOW_CASES},
              'recovery': {name: {'verdict': 'NOT_TESTED', 'evidence': []} for name in RECOVERY_CASES},
              'all_owned_objects_absent': None}
    for name, value in [('RESULT.json', result), ('INTEGRATION_MATRIX.json', matrix)]:
        (directory / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    (directory / 'SHA256SUMS').write_text(''.join(
        digest(path.read_bytes()) + '  ' + path.name + '\n' for path in sorted(directory.iterdir())))
    archive = root / (root.name + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(directory, arcname=root.name)
    sidecar = archive.with_name(archive.name + '.sha256')
    sidecar.write_text(digest(archive.read_bytes()) + '  ' + archive.name + '\n')
    return result, archive, sidecar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--output-parent', type=Path, default=Path('/var/tmp'))
    args = parser.parse_args()
    if not args.collect:
        print(json.dumps({'mode': 'PLAN_ONLY', 'commands': QUERIES,
                          'writes': 'New evidence directory, archive, and checksums only',
                          'creates_qualification': False, 'authorizes_execution': False}, indent=2))
        return 0
    result, archive, sidecar = write_artifacts(args.output_parent, collect())
    print('STATUS=' + result['status'])
    print('BLOCKERS=' + ','.join(result['blockers']))
    print('ARCHIVE=' + str(archive.resolve()))
    print('SIDECAR=' + str(sidecar.resolve()))
    # A blocked collection still produces complete artifacts for review.
    return 2 if result['blockers'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
