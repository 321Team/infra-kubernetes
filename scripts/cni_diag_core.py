"""Pure CP1 diagnostic contract and write-ahead firewall transaction.

No host IO occurs on import. Runtime adapters must supply snapshot, command and
durable save. This evidence is deliberately incompatible with CP1 acceptance.
"""
import copy
import hashlib
import ipaddress
import json
import re
import shlex
import time
import xml.etree.ElementTree as ET

NODE = 'k8s-control01'
ZONE = 'fds-cni-trial'
POLICY = 'fds-cp1-cni-probe'
BLOCK = ipaddress.ip_network('10.244.246.64/26')
DURATION = 1200
RESERVE = 180
FLAGS = dict(purpose='DIAGNOSTIC_ONLY', acceptance_criteria_credit=False,
             authorizes_cp1_acceptance=False, authorizes_cp2_change=False,
             requires_consumer_extension=False)
QUALIFICATION_CASES = ('peer_8080', 'peer_8081', 'outside_pair',
                       'physical_spoof_ingress', 'physical_egress',
                       'tunnel_ingress', 'existing_pod_egress')


def need(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def rule(value):
    return shlex.join(shlex.split(value))


def six_rules():
    return sorted(rule('rule family="ipv4" source address="10.1.93.%s/32" '
                       'port port="%s" protocol="tcp" accept' % (ip, port))
                  for ip in (102, 103, 104) for port in ('10250', '2379-2380'))


class Budget:
    """Persisted wall + BOOTTIME bounds; suspend/clock rollback cannot extend work."""
    def __init__(self, record, boot_id, wall=time.time,
                 clock=lambda: time.clock_gettime(time.CLOCK_BOOTTIME)):
        self.record, self.boot_id, self.wall, self.clock = record, boot_id, wall, clock

    def remaining(self, cleanup=False):
        need(self.record['boot_id'] == self.boot_id, 'Boot changed: recovery only')
        cap = min(self.record['wall_end'] - self.wall(),
                  self.record['boot_start'] + DURATION - self.clock())
        cap -= 0 if cleanup else RESERVE
        need(cap > 1, 'Diagnostic window exhausted')
        return cap


def parse_objects(raw):
    """Parse both bulk zone and policy output; reject unknown continuation lines."""
    objects, current, rich = {}, None, False
    for line in raw.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            name = line.split()[0]
            need(re.fullmatch(r'[A-Za-z0-9_-]+', name) and name not in objects,
                 'Invalid or duplicate firewall object')
            current = objects[name] = {'rich rules': []}
            rich = False
        elif line.strip().startswith('rule ') and rich:
            current['rich rules'].append(rule(line.strip()))
        else:
            need(current is not None and ':' in line, 'Invalid firewall output')
            key, val = line.strip().split(':', 1)
            if key == 'rich rules':
                need(not val.strip(), 'Unexpected inline rich rule')
                rich = True
                continue
            need(not rich and key not in current, 'Unknown/duplicate firewall field')
            current[key] = ' '.join(sorted(val.split()))
    for obj in objects.values():
        obj['rich rules'].sort()
    return objects


def empty_zone():
    return {'target': 'default', 'ingress-priority': '0', 'egress-priority': '0',
            'icmp-block-inversion': 'no', 'interfaces': '', 'sources': '',
            'services': '', 'ports': '', 'protocols': '', 'forward': 'no',
            'masquerade': 'no', 'forward-ports': '', 'source-ports': '',
            'icmp-blocks': '', 'rich rules': []}


def pair_rules(peers):
    need(len(peers) == 2, 'Exactly two peers required')
    ips = [str(ipaddress.IPv4Address(p['ip'])) for p in peers]
    need(len(set(ips)) == 2 and all(ipaddress.ip_address(ip) in BLOCK for ip in ips),
         'Distinct trial addresses in approved CP1 block required')
    return sorted(rule('rule family="ipv4" source address="%s/32" '
                       'destination address="%s/32" port port="8080" protocol="tcp" accept'
                       % (ips[a], ips[b])) for a, b in ((0, 1), (1, 0)))


def disabled_policy(peers):
    return {'disable': 'yes', 'priority': '-500', 'target': 'CONTINUE',
            'ingress-zones': ZONE, 'egress-zones': ZONE, 'services': '',
            'ports': '', 'protocols': '', 'masquerade': 'no', 'forward-ports': '',
            'source-ports': '', 'icmp-blocks': '', 'rich rules': pair_rules(peers)}


def xml_documents(run_id, peers):
    zone = ET.Element('zone')  # Omitted forward tag means disabled, not default new-zone behavior.
    ET.SubElement(zone, 'short').text = 'FDS diagnostic ' + run_id
    ET.SubElement(zone, 'description').text = run_id
    policy = ET.Element('policy', target='CONTINUE', priority='-500')
    ET.SubElement(policy, 'short').text = 'FDS diagnostic ' + run_id
    ET.SubElement(policy, 'description').text = run_id
    ET.SubElement(policy, 'disable')
    ET.SubElement(policy, 'ingress-zone', name=ZONE)
    ET.SubElement(policy, 'egress-zone', name=ZONE)
    pair_rules(peers)
    for a, b in ((0, 1), (1, 0)):
        item = ET.SubElement(policy, 'rule', family='ipv4')
        ET.SubElement(item, 'source', address=peers[a]['ip'] + '/32')
        ET.SubElement(item, 'destination', address=peers[b]['ip'] + '/32')
        ET.SubElement(item, 'port', port='8080', protocol='tcp')
        ET.SubElement(item, 'accept')
    return {ZONE + '.xml': ET.tostring(zone, encoding='unicode'),
            POLICY + '.xml': ET.tostring(policy, encoding='unicode')}


def validate_peers(peers, ns_uid, links, routes, system_interfaces):
    need(len(peers) == 2 and len({p['uid'] for p in peers}) == 2, 'Peer UID mismatch')
    pair_rules(peers)
    used = set()
    for p in peers:
        need(p['namespace_uid'] == ns_uid and p['node'] == NODE, 'Peer namespace/node mismatch')
        need(p['name'] in ('a', 'b') and p['uid'] and not p['host_network'], 'Invalid trial Pod')
        need(re.fullmatch(r'cali[a-zA-Z0-9]{1,11}', p['interface']), 'Not an exact Calico veth')
        need(p['interface'] not in used | set(system_interfaces) |
             {'ens160', 'lo', 'vxlan.calico'}, 'Interface collision')
        used.add(p['interface'])
        link = next((v for v in links if v.get('ifname') == p['interface']), None)
        need(link and link.get('linkinfo', {}).get('info_kind') == 'veth', 'Host link is not veth')
        need(link['ifindex'] == p['host_ifindex'] == p['pod_iflink'] and
             link.get('link_index') == p['pod_ifindex'], 'Veth peer/ifindex mismatch')
        matches = [r for r in routes if r.get('dst') in (p['ip'], p['ip'] + '/32')]
        need(len(matches) == 1 and matches[0].get('dev') == p['interface'] and
             matches[0].get('type', 'unicast') == 'unicast' and not matches[0].get('gateway'),
             'Pod /32 host route mismatch')


def validate_reload_baseline(state):
    need(state['default'] == 'public', 'Default zone drift')
    runtime, permanent = copy.deepcopy(state['runtime']), copy.deepcopy(state['permanent'])
    for p in (runtime, permanent):
        need(ZONE not in p['zones'] and POLICY not in p['policies'], 'Diagnostic name collision')
    actual = runtime['zones']['public']['rich rules']
    saved = permanent['zones']['public']['rich rules']
    need(sorted(set(actual) - set(saved)) == six_rules() and set(saved) <= set(actual),
         'Runtime restoration must be the original six rules only')
    runtime['zones']['public']['rich rules'] = saved
    need(runtime['zones']['public']['interfaces'] == 'ens160', 'Management binding drift')
    need(permanent['zones']['public']['interfaces'] in ('', 'ens160'), 'Permanent binding drift')
    runtime['zones']['public']['interfaces'] = permanent['zones']['public']['interfaces']
    need(runtime == permanent, 'Reload would discard unrelated Runtime differences')
    need(all(not z['sources'] for z in permanent['zones'].values()),
         'Source zone classification needs separate review')


def reloaded(state):
    result = copy.deepcopy(state)
    result['runtime'] = copy.deepcopy(result['permanent'])
    result['runtime']['zones']['public']['interfaces'] = 'ens160'
    return result


def validate_native_policy(nft, peers):
    """Fail closed on an unrecognized compiled dispatch form.

    This is a structural check, not per-packet trace evidence or a proof that
    earlier Calico/conntrack rules denied traffic. Integration must cover both.
    """
    rules = [x['rule'] for x in nft.get('nftables', []) if 'rule' in x]
    wanted_ifaces = {p['interface'] for p in peers}
    wanted_pairs = {(p['ip'], q['ip']) for p, q in (peers, list(reversed(peers)))}
    policy_rules = [r for r in rules if POLICY in r.get('chain', '')]
    need(policy_rules, 'Compiled diagnostic policy chain missing')

    def values(right):
        if isinstance(right, str):
            return {right}
        if isinstance(right, dict) and isinstance(right.get('set'), list):
            return set(right['set'])
        return set()

    entries = []
    for r in rules:
        if POLICY in r.get('chain', ''):
            continue
        expr = r.get('expr', [])
        targets = [e[k]['target'] for e in expr for k in ('jump', 'goto') if k in e]
        if not any(POLICY in t for t in targets):
            continue
        meta = {}
        for e in expr:
            m = e.get('match', {})
            key = m.get('left', {}).get('meta', {}).get('key')
            if key and m.get('op') == '==':
                meta[key] = values(m['right'])
        need(meta.get('iifname') and meta.get('oifname') and
             meta['iifname'] <= wanted_ifaces and meta['oifname'] <= wanted_ifaces,
             'Policy dispatch lacks exact ingress AND egress restrictions')
        entries.append(meta)
    need(entries and set.union(*(m['iifname'] for m in entries)) == wanted_ifaces and
         set.union(*(m['oifname'] for m in entries)) == wanted_ifaces, 'Incomplete veth dispatch')
    accepted = set()
    for r in policy_rules:
        if not any('accept' in e for e in r.get('expr', [])):
            continue
        matches = {}
        for e in r['expr']:
            m = e.get('match', {})
            payload = m.get('left', {}).get('payload', {})
            if payload and m.get('op') == '==':
                right = m['right']
                if isinstance(right, dict) and 'prefix' in right:
                    need(right['prefix']['len'] == 32, 'Compiled prefix is broader than /32')
                    right = right['prefix']['addr']
                matches[(payload.get('protocol'), payload.get('field'))] = right
        pair = (matches.get(('ip', 'saddr')), matches.get(('ip', 'daddr')))
        need(pair in wanted_pairs and matches.get(('tcp', 'dport')) == 8080,
             'Compiled ACCEPT exceeds exact Pod pair/TCP8080')
        accepted.add(pair)
    need(accepted == wanted_pairs, 'Both /32 directions not compiled')
    return {'status': 'STATIC_MATCH', 'dispatch_rules': len(entries), 'allow_pairs': sorted(accepted)}


class Transaction:
    """Save intent before every command; reconcile unknown outcomes before retry.

    Adapter must hold the job lock for this object's lifetime. The independent
    recovery service terminates the job cgroup before acquiring that same lock.
    """
    def __init__(self, adapter, journal):
        self.io, self.j = adapter, journal

    def reconcile(self):
        current = self.io.snapshot()
        intent = self.j.get('intent')
        if intent:
            need(current in (intent['before'], intent['after']), 'Unknown outcome plus firewall drift')
            self.j['expected'] = current
            self.j['intent'] = None
            self.io.save(self.j)
        need(current == self.j['expected'], 'Concurrent firewall change')
        return current

    def change(self, label, args, after, cleanup=False):
        self.io.guard(cleanup)
        before = self.reconcile()
        self.j['intent'] = dict(label=label, before=before, after=after, argv=args)
        self.io.save(self.j)
        self.io.command(args, cleanup=cleanup)
        need(self.io.snapshot() == after, 'Post-mutation state differs: ' + label)
        self.j['expected'], self.j['intent'] = after, None
        self.j.setdefault('completed', []).append(label)
        self.io.save(self.j)

    def alter(self, label, args, edit, cleanup=False):
        after = copy.deepcopy(self.reconcile())
        edit(after)
        for plane in ('runtime', 'permanent'):
            for kind in ('zones', 'policies'):
                for obj in after[plane][kind].values():
                    obj['rich rules'].sort()
        self.change(label, args, after, cleanup)

    def restore_six(self, cleanup=False):
        current = self.reconcile()
        for item in six_rules():
            if item not in current['runtime']['zones']['public']['rich rules']:
                self.alter('restore-original-rule', ['--zone=public', '--add-rich-rule=' + item],
                           lambda s, r=item: s['runtime']['zones']['public']['rich rules'].append(r),
                           cleanup)
                # Snapshots and expected rules are canonical sorted lists.
                current = self.reconcile()

    def prepare(self, peers, directory):
        validate_reload_baseline(self.reconcile())
        self.j['peers'] = copy.deepcopy(peers)
        self.io.save(self.j)
        self.alter('create-permanent-zone', ['--permanent', '--new-zone-from-file=' +
                    str(directory) + '/' + ZONE + '.xml', '--name=' + ZONE],
                   lambda s: s['permanent']['zones'].__setitem__(ZONE, empty_zone()))
        self.alter('create-disabled-policy', ['--permanent', '--new-policy-from-file=' +
                    str(directory) + '/' + POLICY + '.xml', '--name=' + POLICY],
                   lambda s: s['permanent']['policies'].__setitem__(POLICY, disabled_policy(peers)))
        self.change('load-disabled-definitions', ['--reload'], reloaded(self.reconcile()))
        # Recovery commands may restore the original six after the work cutoff.
        self.restore_six(cleanup=True)
        self.io.management()
        self.io.verify_peers(peers, bound=False)
        for p in peers:
            self.alter('bind-' + p['name'], ['--zone=' + ZONE, '--add-interface=' + p['interface']],
                       lambda s, v=p['interface']: s['runtime']['zones'][ZONE].__setitem__(
                           'interfaces', ' '.join(sorted(s['runtime']['zones'][ZONE]['interfaces'].split() + [v]))))
        self.io.verify_peers(peers, bound=True)
        self.alter('enable-runtime-policy', ['--policy=' + POLICY, '--remove-disable'],
                   lambda s: s['runtime']['policies'][POLICY].__setitem__('disable', 'no'))

    def withdraw(self):
        current = self.io.snapshot()
        allowed = [self.j['expected']]
        if self.j.get('intent'):
            allowed += [self.j['intent']['before'], self.j['intent']['after']]
        if getattr(self.io, 'boot_changed', False):
            allowed += [reloaded(v) for v in list(allowed)]
        # CNI can remove a destroyed Pod's veth before recovery gets the lock.
        if hasattr(self.io, 'interface_gone'):
            for original in list(allowed):
                candidate = copy.deepcopy(original)
                zone = candidate['runtime']['zones'].get(ZONE)
                if zone:
                    zone['interfaces'] = ' '.join(i for i in zone['interfaces'].split()
                        if not self.io.interface_gone(i, self.j.get('peers', [])))
                    allowed.append(candidate)
        if current in allowed:
            if self.j.get('intent'):
                self.j.setdefault('recovered_intents', []).append(self.j['intent']['label'])
            self.j['expected'], self.j['intent'] = current, None
            self.j['boot_recovery'] = getattr(self.io, 'boot_changed', False)
            self.io.save(self.j)
        # On unrelated drift, narrowly disable our known runtime policy first.
        # No reload/delete/foreign-object overwrite follows until drift is reviewed.
        if current not in allowed:
            own = current['runtime']['policies'].get(POLICY)
            if own and any(own == v['runtime']['policies'].get(POLICY) for v in allowed):
                self.io.command(['--policy=' + POLICY, '--add-disable'], cleanup=True)
                self.j['emergency_disable_observed'] = self.io.snapshot()['runtime']['policies'].get(POLICY)
                self.io.save(self.j)
            raise ValueError('Firewall drift: own policy disabled where verified; console review required')
        self.reconcile()
        if POLICY in self.j['expected']['runtime']['policies']:
            if self.j['expected']['runtime']['policies'][POLICY]['disable'] != 'yes':
                self.alter('disable-runtime-policy', ['--policy=' + POLICY, '--add-disable'],
                           lambda s: s['runtime']['policies'][POLICY].__setitem__('disable', 'yes'), True)
        if ZONE in self.j['expected']['runtime']['zones']:
            bound = self.j['expected']['runtime']['zones'][ZONE]['interfaces'].split()
            for interface in bound:
                self.io.verify_cleanup_interface(interface, self.j.get('peers', []))
                self.alter('unbind-' + interface, ['--zone=' + ZONE, '--remove-interface=' + interface],
                           lambda s, v=interface: s['runtime']['zones'][ZONE].__setitem__('interfaces',
                               ' '.join(x for x in s['runtime']['zones'][ZONE]['interfaces'].split() if x != v)), True)
        for kind, name in (('policies', POLICY), ('zones', ZONE)):
            if name in self.j['expected']['permanent'][kind]:
                self.alter('delete-' + name, ['--permanent', '--delete-' +
                           ('policy' if kind == 'policies' else 'zone') + '=' + name],
                           lambda s, k=kind, n=name: s['permanent'][k].pop(n), True)
        if any(n in self.j['expected']['runtime'][k] for k, n in (('policies', POLICY), ('zones', ZONE))):
            self.change('unload-diagnostic-definitions', ['--reload'], reloaded(self.reconcile()), True)
        self.restore_six(cleanup=True)
        need(self.reconcile() == self.j['baseline'], 'Original firewall not restored')
        self.j['firewall_restored'] = True
        self.io.save(self.j)
