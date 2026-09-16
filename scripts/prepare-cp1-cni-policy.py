#!/usr/bin/env python3
"""Read-only CP1 inventory and a non-executable, bounded policy proposal.

No apply/reload/recover mode. Exact interface membership is intentionally not
advertised as the dynamic cali+ scope. The proposal cannot satisfy the existing
zone-based handoff contract or authorize any node operation.
"""
import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NODE_SHA = '93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa'
HANDOFF_SHA = '8fbc4d03fd76fecd27bd6f82a3672f4d0220ed1db8c331c61d9310c98d7db1d3'
HOST = 'k8s-control01'
BLOCK = ipaddress.ip_network('10.244.246.64/26')
POLICY = 'fds-cp1-local-veth'
IPSET = 'fds-cp1-local-veth'
MAX_INPUT = 8 * 1024 * 1024


def need(ok, message):
    if not ok:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(value):
    return digest(encoded(value))


def module(name, path, expected):
    need(digest(path.read_bytes()) == expected, 'dependency bytes differ: ' + path.name)
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def instant(value):
    result = dt.datetime.fromisoformat(value)
    need(result.utcoffset() is not None, 'timezone required')
    return result


def unique(pairs):
    result = {}
    for k, v in pairs:
        need(k not in result, 'duplicate JSON key')
        result[k] = v
    return result


def parse(raw):
    def invalid(_):
        raise ValueError('non-finite JSON number')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def load_checked(path, expected):
    need(isinstance(expected, str) and re.fullmatch(r'[a-f0-9]{64}', expected), 'external SHA256 required')
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_INPUT + 1)
    need(len(raw) <= MAX_INPUT and digest(raw) == expected, 'input size/hash differs')
    return parse(raw)


def output(path, value):
    # Never overwrite an earlier capture or follow a final-component symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())


def successful(result):
    need(result.get('rc') == 0 and not result.get('stdout_truncated') and
         not result.get('stderr_truncated') and not result.get('stderr', '').strip(),
         'incomplete/uncertain read-only query')
    return result['stdout'].strip()


def collect(node, current=None):
    """One bounded capture; continue after individual failures to avoid re-queries.

    Administrative authentication and server identity use the existing pinned
    helper. No workload creation, API writes, firewall mutation, or unit changes.
    """
    current = current or (lambda: dt.datetime.now(dt.timezone.utc))
    node.COMMAND_STATE.update(count=0, timeouts=0, budget_exhausted=False,
                              deadline=time.monotonic() + 240)
    backend = node.BaseBackend(HOST)
    report = {'schema': 'fds.cp1-cni-policy-inspection.v1', 'started_at': current().isoformat(),
              'mode': 'READ_ONLY', 'queries': {}, 'errors': [], 'state': {}}
    try:
        # Fail before reading unrelated hosts. Identity is not inferred from a filename.
        backend.identity()
        def state(label):
            try:
                report['state'][label] = backend.cp1_state()
            except (ValueError, OSError, KeyError) as error:
                report['errors'].append(label + ': ' + str(error))
        state('before')
        query = report['queries']
        def run(key, argv):
            query[key] = node.command(argv)
            return query[key]
        run('version', ['firewall-cmd', '--version'])
        run('boot', ['cat', '/proc/sys/kernel/random/boot_id'])
        run('firewalld_config', ['cat', '/etc/firewalld/firewalld.conf'])
        run('links', ['ip', '-j', '-d', 'link', 'show'])
        run('routes', ['ip', '-j', '-4', 'route', 'show', 'table', 'all'])
        run('native', ['nft', '-j', 'list', 'ruleset'])
        for name, args in {
            'pods': ['get', 'pods', '-A', '-o', 'json'],
            'blocks': ['get', 'ipamblocks.crd.projectcalico.org', '-o', 'json'],
            'affinities': ['get', 'blockaffinities.crd.projectcalico.org', '-o', 'json'],
            'networkpolicies': ['get', 'networkpolicies.networking.k8s.io', '-A', '-o', 'json'],
            'calico_networkpolicies': ['get', 'networkpolicies.crd.projectcalico.org', '-A', '-o', 'json'],
        }.items():
            query[name] = backend.kubectl(args)
        def inventory(suffix=''):
            for plane in ('runtime', 'permanent'):
                prefix = ['firewall-cmd'] + (['--permanent'] if plane == 'permanent' else [])
                for kind in ('zones', 'ipsets'):
                    key = plane + '/' + kind + suffix
                    result = run(key, prefix + ['--get-' + kind])
                    try:
                        names = inventory_names(successful(result))
                        for name in names:
                            run(key + '/' + name, prefix + ['--info-' + kind[:-1] + '=' + name])
                    except (ValueError, KeyError) as error:
                        report['errors'].append(key + ': ' + str(error))
        inventory()
        # Full firewall/protection and membership sampling on both sides. The
        # capture is still not atomic; the executor must check again in-window.
        run('links_after', ['ip', '-j', '-d', 'link', 'show'])
        run('routes_after', ['ip', '-j', '-4', 'route', 'show', 'table', 'all'])
        query['pods_after'] = backend.kubectl(['get', 'pods', '-A', '-o', 'json'])
        query['blocks_after'] = backend.kubectl(['get', 'ipamblocks.crd.projectcalico.org', '-o', 'json'])
        query['affinities_after'] = backend.kubectl(['get', 'blockaffinities.crd.projectcalico.org', '-o', 'json'])
        query['networkpolicies_after'] = backend.kubectl(['get', 'networkpolicies.networking.k8s.io', '-A', '-o', 'json'])
        query['calico_networkpolicies_after'] = backend.kubectl(['get', 'networkpolicies.crd.projectcalico.org', '-A', '-o', 'json'])
        inventory('_after')
        run('native_after', ['nft', '-j', 'list', 'ruleset'])
        run('version_after', ['firewall-cmd', '--version'])
        run('firewalld_config_after', ['cat', '/etc/firewalld/firewalld.conf'])
        run('boot_after', ['cat', '/proc/sys/kernel/random/boot_id'])
        state('after')
    finally:
        backend.cache.cleanup()
        node.COMMAND_STATE['deadline'] = None
    report['ended_at'] = current().isoformat()
    report['status'] = 'CAPTURED_REVIEW_REQUIRED'
    return report


def inventory_names(raw):
    names = raw.split()
    need(len(names) == len(set(names)) and len(names) <= 128 and
         all(re.fullmatch(r'[A-Za-z0-9_-]+', n) for n in names), 'unsafe object inventory')
    return sorted(names)


def inventory_snapshot(queries, suffix=''):
    result = {}
    for plane in ('runtime', 'permanent'):
        result[plane] = {}
        for kind in ('zones', 'ipsets'):
            key = plane + '/' + kind + suffix
            need(key in queries, 'paired inventory absent: ' + key)
            names = inventory_names(queries[key])
            need(all(key + '/' + name in queries for name in names), 'inventory definition missing')
            need({k for k in queries if k.startswith(key + '/')} == {key + '/' + n for n in names},
                 'unlisted inventory definition')
            result[plane][kind] = {name: queries[key + '/' + name] for name in names}
        need('public' in result[plane]['zones'], 'public zone absent')
    return result


def native_structure(native):
    """Compare two observations without treating packet counter increments as drift.

    Only explicit rule counter statistics and named-counter statistics are zeroed.
    Handles, rule/list order, matches, verdicts, set elements/timeouts, quotas and
    all other fields stay in the comparison. This is not a semantic nft verifier.
    """
    result = copy.deepcopy(native)
    need(isinstance(result, dict) and isinstance(result.get('nftables'), list) and result['nftables'],
         'native rules absent')
    def counters(value):
        if isinstance(value, dict):
            if set(value) == {'counter'} and isinstance(value['counter'], dict):
                stats = value['counter']
                if set(stats) == {'packets', 'bytes'}:
                    need(all(type(n) is int and n >= 0 for n in stats.values()), 'invalid counter statistics')
                    stats.update(packets=0, bytes=0)
            for child in value.values():
                counters(child)
        elif isinstance(value, list):
            for child in value:
                counters(child)
    for item in result['nftables']:
        need(isinstance(item, dict) and len(item) == 1, 'ambiguous native object')
        if 'rule' in item:
            need(isinstance(item['rule'], dict) and isinstance(item['rule'].get('expr'), list),
                 'native rule expressions absent')
            counters(item['rule']['expr'])
        if 'counter' in item:
            counter = item['counter']
            need(isinstance(counter, dict) and {'name', 'family', 'table', 'packets', 'bytes'} <= set(counter),
                 'named counter incomplete')
            need(all(type(counter[k]) is int and counter[k] >= 0 for k in ('packets', 'bytes')),
                 'invalid counter statistics')
            counter.update(packets=0, bytes=0)
    return result


def validate_public_classification(inventory, peers):
    """Conservative eligibility screen; no claim to reproduce zone dispatch.

    Source-based non-public classification needs a separate reviewed analysis.
    An exact or prefix interface assignment of any candidate veth also blocks.
    """
    names = {p['interface'] for p in peers}
    for plane in ('runtime', 'permanent'):
        for name, raw in inventory[plane]['zones'].items():
            lines = raw.splitlines()
            need(lines and lines[0].split() and lines[0].split()[0] == name,
                 'zone header differs from inventory name')
            fields = {}
            for line in lines[1:]:
                # --info-zone's top-level classification fields only.
                if line.startswith('  ') and not line.startswith('    ') and ':' in line:
                    key, value = line.strip().split(':', 1)
                    need(key not in fields, 'duplicate zone field')
                    fields[key] = value.strip()
            need({'interfaces', 'sources'} <= set(fields), 'zone classification fields absent')
            need(not fields['sources'], 'source-based zone requires separate classification review')
            if name != 'public':
                for interface in fields['interfaces'].split():
                    need(not any(c in interface for c in '*?[]') and '+' not in interface[:-1],
                         'unsupported zone interface pattern')
                    matches = (any(n.startswith(interface[:-1]) for n in names) if interface.endswith('+')
                               else interface in names)
                    need(not matches, 'candidate veth assigned to non-public zone')


def memberships(pods, blocks, affinities, links, routes):
    own = [b['spec'] for b in blocks['items'] if b['spec'].get('cidr') == str(BLOCK)]
    need(len(own) == 1 and own[0].get('affinity') == 'host:' + HOST and
         own[0].get('deleted', False) is False, 'CP1 block ownership differs')
    affinity = [a['spec'] for a in affinities['items'] if a['spec'].get('cidr') == str(BLOCK)]
    need(len(affinity) == 1 and affinity[0].get('node') == HOST and
         affinity[0].get('state') == 'confirmed' and affinity[0].get('deleted', False) in (False, 'false'),
         'confirmed CP1 affinity absent/ambiguous')
    by_name = {link['ifname']: link for link in links}
    need(len(by_name) == len(links), 'duplicate interface name')
    result, seen_ips, seen_uids, seen_ifs = [], set(), set(), set()
    for pod in pods['items']:
        if pod.get('spec', {}).get('nodeName') != HOST or pod['spec'].get('hostNetwork', False):
            continue
        if pod.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
            continue
        meta, status = pod['metadata'], pod['status']
        need(not meta.get('deletionTimestamp') and status.get('phase') == 'Running' and
             any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in status.get('conditions', [])),
             'local workload is changing/not Ready')
        ips = status.get('podIPs', [])
        need(len(ips) == 1 and ips[0].get('ip') == status.get('podIP'), 'single-stack Pod IP required')
        ip = ipaddress.ip_address(status['podIP'])
        need(ip in BLOCK and ip not in (BLOCK.network_address, BLOCK.broadcast_address), 'Pod IP outside owned block')
        need(str(ip) not in seen_ips and meta['uid'] not in seen_uids, 'duplicate Pod IP/UID')
        matching = [r for r in routes if r.get('dst') in (str(ip), str(ip) + '/32')]
        need(len(matching) == 1, 'missing/ambiguous Pod host route')
        route = matching[0]
        need(route.get('table', 'main') in ('main', 254) and route.get('type', 'unicast') == 'unicast' and
             not any(k in route for k in ('gateway', 'via', 'nexthops', 'encap')), 'indirect Pod route')
        iface = route.get('dev', '')
        need(re.fullmatch(r'cali[0-9a-f]{11}', iface) and iface in by_name and iface not in seen_ifs,
             'route is not a unique concrete cali veth')
        link = by_name[iface]
        need(link.get('linkinfo', {}).get('info_kind') == 'veth' and
             type(link.get('ifindex')) is int and link['ifindex'] > 0 and
             type(link.get('link_index')) is int and link['link_index'] > 0 and 'UP' in link.get('flags', []),
             'veth identity/up state unavailable')
        result.append({'namespace': meta['namespace'], 'pod': meta['name'], 'uid': meta['uid'],
                       'ip': str(ip), 'interface': iface, 'ifindex': link['ifindex'], 'peer_ifindex': link['link_index'],
                       'labels': meta.get('labels', {}), 'service_account': pod['spec'].get('serviceAccountName', 'default')})
        seen_ips.add(str(ip)); seen_uids.add(meta['uid']); seen_ifs.add(iface)
    need(2 <= len(result) <= 62, 'bounded local workload set required')
    need(seen_ifs == {n for n in by_name if n.startswith('cali')}, 'unmapped cali interface')
    return sorted(result, key=lambda p: p['interface'])


def documents(peers):
    # Exact names, never wildcard set entries: a set element named cali+ does
    # not acquire the zone interface parser's cali* prefix semantics.
    names = sorted({p['interface'] for p in peers})
    need(2 <= len(names) <= 62 and all(re.fullmatch(r'cali[0-9a-f]{11}', n) for n in names),
         'exact cali names required')
    ipset = ET.Element('ipset', {'type': 'hash:net,iface'})
    ET.SubElement(ipset, 'option', {'name': 'family', 'value': 'inet'})
    for name in names:
        ET.SubElement(ipset, 'entry').text = str(BLOCK) + ',' + name
    policy = ET.Element('policy', {'target': 'CONTINUE', 'priority': '-1'})
    ET.SubElement(policy, 'ingress-zone', {'name': 'public'})
    ET.SubElement(policy, 'egress-zone', {'name': 'public'})
    rule = ET.SubElement(policy, 'rule', {'family': 'ipv4'})
    ET.SubElement(rule, 'source', {'ipset': IPSET})
    ET.SubElement(rule, 'destination', {'ipset': IPSET})
    ET.SubElement(rule, 'accept')
    def xml(root):
        ET.indent(root, space='  ')
        return ET.tostring(root, encoding='unicode') + '\n'
    return {'ipsets/' + IPSET + '.xml': xml(ipset), 'policies/' + POLICY + '.xml': xml(policy)}


def prepare(capture, original, original_input_sha256, capture_input_sha256, current=None):
    current = current or dt.datetime.now(dt.timezone.utc)
    need(capture.get('schema') == 'fds.cp1-cni-policy-inspection.v1' and capture.get('mode') == 'READ_ONLY',
         'read-only capture required')
    start, end = instant(capture['started_at']), instant(capture['ended_at'])
    need(0 <= (end - start).total_seconds() <= 300 and end <= current,
         'capture incomplete/future')
    need(not capture['errors'], 'capture has errors')
    for value in (original_input_sha256, capture_input_sha256):
        need(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value), 'input byte hash absent')
    for result in capture['queries'].values():
        successful(result)
    q = {k: successful(v) for k, v in capture['queries'].items()}
    for key in ('version', 'boot', 'firewalld_config'):
        need(key + '_after' in q and q[key] == q[key + '_after'], key + ' changed/missing during capture')
    need(q['version'] == '2.4.3', 'unverified firewalld version')
    need(re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', q['boot']), 'boot identity absent')
    settings = {}
    for line in q['firewalld_config'].splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            need(key.strip() not in settings, 'duplicate firewalld setting')
            settings[key.strip()] = value.strip()
    need(settings.get('FirewallBackend', 'nftables') == 'nftables', 'nftables backend required')
    # This comparison does not strip CNI fields out of the accepted old receipt.
    handoff = module('cp1_handoff_proposal', ROOT / 'scripts/check-cp1-cni-handoff.py', HANDOFF_SHA)
    accepted = handoff.original_validator()(original)
    before, after = capture['state']['before'], capture['state']['after']
    need(before['sha256'] == fingerprint(before['stable']) == after['sha256'] == fingerprint(after['stable']),
         'firewall/protection changed during capture')
    need(fingerprint(before['stable']['firewall']) == fingerprint(accepted['after']), 'original accepted after differs')
    fw = before['stable']['firewall']
    need(fw['hostname'] == HOST and fw['default_zone'] == fw['active_zone'] == 'public', 'zone/host differs')
    inventory = inventory_snapshot(q)
    for plane in ('runtime', 'permanent'):
        need(IPSET not in inventory[plane]['ipsets'], 'ipset name collision')
        need(POLICY not in fw['active_policies']['configurations'][plane], 'policy name collision')
    need(fingerprint(inventory) == fingerprint(inventory_snapshot(q, '_after')),
         'zone/ipset inventory changed during capture')
    first = memberships(parse(q['pods']), parse(q['blocks']), parse(q['affinities']), parse(q['links']), parse(q['routes']))
    last = memberships(parse(q['pods_after']), parse(q['blocks_after']), parse(q['affinities_after']), parse(q['links_after']), parse(q['routes_after']))
    need(first == last, 'Pod/route/interface membership changed during capture')
    validate_public_classification(inventory, first)
    def policy_specs(raw):
        return sorted([{'namespace': v['metadata'].get('namespace', ''), 'name': v['metadata']['name'],
                        'uid': v['metadata']['uid'], 'spec': v['spec']} for v in parse(raw)['items']],
                      key=lambda v: (v['namespace'], v['name']))
    for key in ('networkpolicies', 'calico_networkpolicies'):
        need(fingerprint(policy_specs(q[key])) == fingerprint(policy_specs(q[key + '_after'])),
             'workload network policy changed during capture')
    native = parse(q['native'])
    need('native_after' in q, 'paired native observation absent')
    native_after = parse(q['native_after'])
    native_stable = native_structure(native)
    need(fingerprint(native_stable) == fingerprint(native_structure(native_after)),
         'native structure changed during capture')
    generated = documents(first)
    return {'schema': 'fds.cp1-cni-policy-proposal.v1', 'status': 'DRAFT_NOT_EXECUTABLE',
            'mechanism': 'PUBLIC_POLICY_EXACT_VETH_IPSET', 'scope': {'host': HOST, 'block': str(BLOCK),
                'layer4': 'ALL', 'membership': 'FROZEN_EXACT_INTERFACES', 'dynamic_cali_prefix': False,
                'purpose': 'PRE_JOIN_CANDIDATE', 'max_window_seconds': 7200},
            'source_sha256': digest(Path(__file__).read_bytes()), 'created_at': current.isoformat(),
            'capture_ended_at': end.isoformat(), 'capture_age_seconds': (current - end).total_seconds(),
            'fresh_for_activation': False,
            'capture_input_sha256': capture_input_sha256, 'original_input_sha256': original_input_sha256,
            'original_receipt_sha256': fingerprint(accepted), 'before': copy.deepcopy(before['stable']),
            'boot_id': q['boot'], 'memberships': first, 'memberships_sha256': fingerprint(first),
            'documents': generated, 'document_sha256': {k: digest(v.encode()) for k, v in generated.items()},
            'native_before_sha256': fingerprint(native),
            'native_after_sha256': fingerprint(native_after),
            'native_structure_sha256': fingerprint(native_stable),
            'inventory_sha256': fingerprint(inventory),
            'consistency': {'paired_observations': True, 'atomic_snapshot': False,
                            'native_semantics_verified': False, 'transient_aba_changes_excluded': False},
            'preservation': {'zone_bindings_changed': False, 'public_definition_changed': False,
                             'host_policy_added': False, 'external_accept_added': False,
                             'runtime_preservation_proven': False},
            'required_before_activation': ['independent_local_recovery_armed', 'ownership_journal_fsynced',
                'exact_before_and_membership_recheck', 'original_runtime_rules_restored_after_reload',
                'complete_native_delta_and_priority_verification', 'actual_cp1_acceptance'],
            'invalidation': ['expiry', 'boot_changed', 'pod_uid_changed', 'veth_identity_changed',
                             'ipam_ownership_changed', 'firewall_or_protection_drift'],
            'authorizes_apply': False, 'authorizes_cp2_change': False, 'authorizes_join': False,
            'existing_handoff_contract_compatible': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    collect_parser = sub.add_parser('collect')
    collect_parser.add_argument('--output', required=True)
    proposal_parser = sub.add_parser('plan')
    for arg in ('capture', 'capture-sha256', 'original', 'original-sha256', 'output'):
        proposal_parser.add_argument('--' + arg, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.mode == 'collect':
            node = module('cp1_policy_inventory_node', ROOT / 'scripts/node-firewall.py', NODE_SHA)
            result = collect(node)
        else:
            result = prepare(load_checked(args.capture, args.capture_sha256),
                             load_checked(args.original, args.original_sha256),
                             args.original_sha256, args.capture_sha256)
        output(args.output, result)
        print(json.dumps({'status': result['status'], 'file': args.output, 'sha256': digest(encoded(result))}))
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'BLOCKED', 'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
