#!/usr/bin/env python3
"""Host-local evidence and firewalld checkpoint/restore. No HEP deletion."""
import argparse
import difflib
import hashlib
import json
import os
import re
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


def firewall_view(permanent=False, raw=None):
    """Return the normalized view; if raw is a dict, fill it with unnormalized output."""
    base = ['firewall-cmd'] + (['--permanent'] if permanent else [])
    result = {}
    if raw is None:
        raw = {}
    # --get-default-zone is a stand-alone option: firewalld rejects it when
    # combined with --permanent ("Can't use stand-alone options with other
    # options"), and it has no separate permanent/runtime variant anyway.
    # Always query it without --permanent.
    raw['--get-default-zone'] = run(['firewall-cmd', '--get-default-zone'])['stdout']
    result['--get-default-zone'] = raw['--get-default-zone'].replace(' (active)', '')
    for arg in ('--list-all-zones', '--list-all-policies', '--get-ipsets'):
        # firewall-cmd appends presentation-only status markers that do not
        # reflect any actual rule difference between runtime and permanent:
        # 'active' on the currently-applied zone/interface, 'disabled' on
        # policies that are currently off. These can appear either alone,
        # e.g. "zone1 (active)", or combined with other markers in the same
        # parenthetical, e.g. "public (default, active)" -- a plain
        # '.replace(" (active)", "")' only catches the standalone form and
        # silently leaves the combined form untouched. Strip the marker
        # wherever it appears inside the parenthetical instead.
        out = raw[arg] = run(base + [arg])['stdout']
        out = re.sub(r',\s*(active|disabled)(?=[,)])', '', out)
        out = re.sub(r'\s*\((active|disabled)\)', '', out)
        if arg == '--list-all-zones':
            # Interface-to-zone binding lives in NetworkManager connection
            # profiles, not in firewalld's permanent zone config: per
            # firewall-cmd(1), an "active zone" is defined as one with a
            # live interface/source binding, which is a runtime-only
            # concept by firewalld's own design. So --list-all-zones will
            # always show the bound interface under runtime and always
            # show it empty under permanent, on every correctly configured
            # node -- this is not a drift to flag, so normalize it out
            # before comparing.
            out = '\n'.join(
                line if not line.strip().startswith('interfaces:')
                else '  interfaces: '
                for line in out.splitlines()
            )
        # Multi-line rule bodies (rich rules, direct rules, forward-ports,
        # etc, each on its own indented line, e.g. lines starting with a
        # tab under 'rich rules:') are the same *set* of rules on runtime
        # and permanent, but firewalld lists them in a different order on
        # each side (live internal order vs on-disk file order). A raw
        # line-by-line diff flags that ordering difference as a config
        # mismatch even when every rule is present on both sides. Sort
        # each contiguous run of tab-indented lines so only an actual
        # missing/extra rule shows up as a difference, not the order.
        lines = out.splitlines()
        sorted_lines = []
        run_start = None
        for i, line in enumerate(lines + ['']):
            is_rule_line = line.startswith('\t')
            if is_rule_line and run_start is None:
                run_start = i
            elif not is_rule_line and run_start is not None:
                sorted_lines.extend(sorted(lines[run_start:i]))
                run_start = None
            if not is_rule_line and i < len(lines):
                sorted_lines.append(line)
        out = '\n'.join(sorted_lines)
        result[arg] = out
    for name in result['--get-ipsets'].split():
        result['ipset:' + name] = raw['ipset:' + name] = run(base + ['--info-ipset=' + name])['stdout']
    for arg in ('--get-all-rules', '--get-all-passthroughs'):
        result['direct:' + arg] = raw['direct:' + arg] = run(base + ['--direct', arg])['stdout']
    return result


def diff_views(left, right, left_name, right_name):
    """Unified diff of two firewall views, key by key, for review evidence."""
    out = []
    for key in sorted(set(left) | set(right)):
        out.extend(difflib.unified_diff(
            left.get(key, '').splitlines(), right.get(key, '').splitlines(),
            left_name + ':' + key, right_name + ':' + key, lineterm=''))
    return '\n'.join(out) + '\n' if out else ''


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
        runtime_raw, permanent_raw = {}, {}
        runtime, permanent = firewall_view(raw=runtime_raw), firewall_view(True, raw=permanent_raw)
        (a.dir / 'runtime.json').write_text(json.dumps(runtime, indent=2))
        (a.dir / 'permanent.json').write_text(json.dumps(permanent, indent=2))
        # Normalization hides presentation-only differences, so keep the
        # unnormalized output and both diffs for review: a real zone move must
        # stay visible in the raw diff even when the normalized views match.
        (a.dir / 'runtime.raw.json').write_text(json.dumps(runtime_raw, indent=2))
        (a.dir / 'permanent.raw.json').write_text(json.dumps(permanent_raw, indent=2))
        (a.dir / 'runtime-vs-permanent.raw.diff').write_text(
            diff_views(runtime_raw, permanent_raw, 'runtime', 'permanent'))
        (a.dir / 'runtime-vs-permanent.normalized.diff').write_text(
            diff_views(runtime, permanent, 'runtime', 'permanent'))
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
