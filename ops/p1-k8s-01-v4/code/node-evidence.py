#!/usr/bin/env python3
"""Host-local evidence and firewalld checkpoint/restore. No HEP deletion."""
import argparse
import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path


def run(args, required=True):
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=60)
        r = {'argv': args, 'rc': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    except (OSError, subprocess.TimeoutExpired) as e:
        r = {'argv': args, 'rc': 125, 'stdout': '', 'stderr': str(e)}
    if required and r['rc']:
        raise RuntimeError(json.dumps(r))
    return r


def tree_hash(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def firewall_view(permanent=False):
    base = ['firewall-cmd'] + (['--permanent'] if permanent else [])
    result = {}
    for arg in ('--get-default-zone', '--list-all-zones', '--list-all-policies',
                '--get-ipsets'):
        # Only the presentation-only active suffix is ignored; no rules are dropped.
        result[arg] = run(base + [arg])['stdout'].replace(' (active)', '')
    for name in result['--get-ipsets'].split():
        result['ipset:' + name] = run(base + ['--info-ipset=' + name])['stdout']
    for arg in ('--get-all-rules', '--get-all-passthroughs'):
        result['direct:' + arg] = run(base + ['--direct', arg])['stdout']
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['collect', 'checkpoint-firewall', 'restore-firewall'])
    p.add_argument('--dir', type=Path, required=True)
    a = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('root required')
    os.umask(0o077)
    if a.action != 'restore-firewall':
        if a.dir.exists():
            raise SystemExit('use a new evidence/checkpoint directory')
        a.dir.mkdir(parents=True, mode=0o700)
    if a.action == 'collect':
        commands = [
            ['hostnamectl', '--static'], ['date', '--iso-8601=seconds'], ['timedatectl'],
            ['ip', '-j', 'address'], ['ip', '-j', 'route'], ['ip', 'route', 'get', '10.250.10.10'],
            ['ss', '-H', '-lntup'], ['getenforce'], ['auditctl', '-s'],
            ['systemctl', 'show', 'firewalld', '-p', 'LoadState', '-p', 'UnitFileState', '-p', 'ActiveState'],
            ['iptables', '--version'], ['iptables-save', '-c'], ['ip6tables-save', '-c'],
            ['nft', '-a', 'list', 'ruleset'], ['chronyc', '-n', 'sources'], ['chronyc', 'tracking']]
        results = [run(cmd, False) for cmd in commands]
        (a.dir / 'commands.json').write_text(json.dumps(results, indent=2))
        print('COLLECTED_REVIEW_REQUIRED; inspect every rc, chain path and counters')
        return
    snapshot = a.dir / 'checkpoint.json'
    fwroot = Path('/etc/firewalld')
    nmroot = Path('/etc/NetworkManager/system-connections')
    if a.action == 'checkpoint-firewall':
        if run(['systemctl', 'is-enabled', 'firewalld'])['stdout'].strip() != 'enabled':
            raise SystemExit('this transition requires enabled firewalld at start')
        run(['firewall-cmd', '--state'])
        run(['firewall-cmd', '--check-config'])
        runtime, permanent = firewall_view(), firewall_view(True)
        (a.dir / 'runtime.json').write_text(json.dumps(runtime, indent=2))
        (a.dir / 'permanent.json').write_text(json.dumps(permanent, indent=2))
        if runtime != permanent:
            raise SystemExit('runtime/permanent differ: reconcile and test before checkpoint; no automatic overwrite')
        archive = a.dir / 'firewalld-config.tar'
        run(['tar', '--acls', '--xattrs', '--selinux', '-cpf', str(archive), '-C', '/', 'etc/firewalld'])
        record = {'node': socket.gethostname(), 'runtime': runtime, 'firewalld_files': tree_hash(fwroot),
                  'nm_file_hashes': tree_hash(nmroot),
                  'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        snapshot.write_text(json.dumps(record, indent=2))
        (a.dir / 'checkpoint.sha256').write_text(hashlib.sha256(snapshot.read_bytes()).hexdigest() + '\n')
        print('CHECKPOINT_CREATED; attach actual approved/denied traffic evidence separately')
    else:
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != (a.dir / 'checkpoint.sha256').read_text().strip():
            raise SystemExit('checkpoint hash mismatch')
        saved = json.loads(snapshot.read_text())
        if saved['node'] != socket.gethostname():
            raise SystemExit('wrong node checkpoint')
        if hashlib.sha256((a.dir / 'firewalld-config.tar').read_bytes()).hexdigest() != saved['archive_sha256']:
            raise SystemExit('archive hash mismatch')
        if tree_hash(fwroot) != saved['firewalld_files'] or tree_hash(nmroot) != saved['nm_file_hashes']:
            raise SystemExit('configuration drift: retain HEP; restore reviewed files before this action')
        run(['systemctl', 'enable', '--now', 'firewalld'])
        run(['firewall-cmd', '--check-config'])
        run(['firewall-cmd', '--reload'])
        run(['firewall-cmd', '--state'])
        if firewall_view() != saved['runtime']:
            raise SystemExit('restored runtime differs from checkpoint; DO NOT REMOVE HEP')
        (a.dir / 'restore-result.json').write_text(json.dumps({
            'node': saved['node'], 'status': 'FIREWALL_CONFIG_RESTORED_TRAFFIC_TEST_REQUIRED'}, indent=2))
        print('FIREWALL_CONFIG_RESTORED_TRAFFIC_TEST_REQUIRED; keep HEP until protective rules/tests are reviewed')


if __name__ == '__main__':
    main()
