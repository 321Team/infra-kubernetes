"""Constrained semantic projection of local Pod-dependent dataplane objects.

Only the captured iptables-nft / no workload-policy / single-stack ClusterIP
layout is supported. Unknown layouts fail closed. This is a pure verifier;
verified_members must come from the independent CRI/route/peer collector.
No policy is installed, and this projection alone never authorizes an action.
"""
import base64
import copy
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re

from cp1_cni_membership import need, members, fingerprint, same, HOST, BLOCK, select_members, number, STALE
from cp1_cni_dataplane import option

SHAPES = Path(__file__).with_name('cp1-cni-calico-shapes.json')
POLICY_KEYS = ('networkpolicies', 'calico_networkpolicies', 'calico_globalnetworkpolicies',
               'calico_hostendpoints', 'calico_networksets', 'calico_globalnetworksets')
FIXED_API = (*POLICY_KEYS, 'namespaces', 'serviceaccounts', 'services', 'calico_felix',
             'calico_ippools', 'calico_config', 'calico_daemonset', 'kube_proxy_config', 'kube_proxy_daemonset')
PROJECTION = 'fds.cp1-cni-pod-delta.v1'


def capture_members(capture, boot_id, clock):
    """Validate full raw CRI/peer facts, not uploaded attestation booleans.

    The caller must obtain this capture locally from source-pinned code.
    A serialized record does not authenticate its own origin.
    """
    need(capture.get('schema') == 'fds.cp1-cni-membership-raw.v1' and
         capture.get('mode') == 'READ_ONLY' and capture.get('complete') is True and
         capture.get('errors') == [] and capture.get('authorizes_apply') is False and
         capture.get('boot_id') == boot_id == clock.get('boot_id'), 'peer capture incomplete or boot differs')
    for key in ('wall','boottime'):
        need(number(capture[key]) <= number(capture['ended_'+key]) <= number(clock[key]) and
             0 <= clock[key]-capture[key] < STALE, 'peer capture stale/future')
    before,after=select_members(capture['raw']),select_members(capture['raw_after'])
    need(same(before,after) and same(after,capture['members']), 'paired peer identity differs')
    # Raw peer fields include PID start/netns identity, beyond the member tuple.
    need(same(capture['raw']['sandbox_peers'],capture['raw_after']['sandbox_peers']), 'peer namespace identity differs')
    return after


def substitute(value, mapping):
    if isinstance(value, str): return mapping.get(value, value)
    if isinstance(value, list): return [substitute(v, mapping) for v in value]
    if isinstance(value, dict): return {k: substitute(v, mapping) for k, v in value.items()}
    return value


def profile(prefix, name):
    if len(prefix + name) > 28 or len(prefix + name) == 28 and name.startswith('_'):
        name = '_' + base64.urlsafe_b64encode(hashlib.sha256(name.encode()).digest()).decode()[:27-len(prefix)]
    return prefix + name


def hash_chain(prefix, text):
    return prefix + base64.b32encode(hashlib.sha256(text.encode()).digest()).decode()[:16]


def strip_hash(tokens):
    result = []; i = 0; removed = 0
    while i < len(tokens):
        if tokens[i:i+3] == ['-m', 'comment', '--comment'] and i + 3 < len(tokens) and re.fullmatch(r'cali:[A-Za-z0-9_-]{16}', tokens[i+3]):
            removed += 1; i += 4
        else:
            result.append(tokens[i]); i += 1
    need(removed == 1, 'one Calico hash comment required')
    return result


def rule_shape(rule, *, calico=False):
    r = copy.deepcopy(rule)
    r.pop('handle', None)
    r['expr'] = [e for e in r['expr'] if 'counter' not in e]
    if calico and 'comment' in r:
        need(re.fullmatch(r'cali:[A-Za-z0-9_-]{16};?', r['comment']), 'unexpected Calico native comment')
        r.pop('comment')
    return r


def api_items(api, key):
    value = api[key]
    rows = value.get('items') if 'items' in value else [value]
    need(isinstance(rows, list), 'API list absent: ' + key)
    return rows


def fixed_api(api):
    result = {}
    for key in FIXED_API:
        rows = []
        for item in api_items(api, key):
            m = item['metadata']
            # Exclude API transport/status bookkeeping only. Unknown top-level
            # spec/data/account fields remain in the protection digest.
            row = {k: copy.deepcopy(v) for k, v in item.items() if k not in ('metadata', 'status', 'kind', 'apiVersion')}
            row['identity'] = {k: copy.deepcopy(m.get(k)) for k in ('namespace', 'name', 'uid', 'labels', 'annotations', 'ownerReferences', 'deletionTimestamp')}
            rows.append(row)
        result[key] = sorted(rows, key=lambda r: (r['identity']['namespace'] or '', r['identity']['name']))
    for key in POLICY_KEYS:
        need(not api_items(api, key), 'workload/host protection policy layout requires a new model: ' + key)
    return result


def endpoints(api, verified_members):
    rows = members(verified_members)
    pods = {(p['metadata']['namespace'], p['metadata']['name'], p['metadata']['uid']): p for p in api_items(api, 'pods')}
    need(len(pods) == len(api_items(api, 'pods')) and
         len({key[:2] for key in pods}) == len(pods), 'duplicate Pod identity')
    eligible = {key for key, p in pods.items() if p['spec'].get('nodeName') == HOST and
                p['spec'].get('hostNetwork') is not True and not p['metadata'].get('deletionTimestamp') and
                p.get('status', {}).get('phase') not in ('Failed', 'Succeeded') and p.get('status', {}).get('podIP')}
    need(eligible == {(r['namespace'], r['pod'], r['uid']) for r in rows}, 'API/verified peer membership differs')
    accounts = {(r['metadata']['namespace'], r['metadata']['name']) for r in api_items(api, 'serviceaccounts')}
    namespaces = {r['metadata']['name'] for r in api_items(api, 'namespaces')}
    result = []
    for row in rows:
        p = pods[row['namespace'], row['pod'], row['uid']]
        need(p['status'].get('phase') in ('Pending', 'Running'), 'unknown Pod phase')
        need(p['status'].get('podIPs') == [{'ip': row['ip']}] and p['status'].get('podIP') == row['ip'], 'Pod/peer IP differs')
        account = p['spec'].get('serviceAccountName', 'default')
        need((row['namespace'], account) in accounts and row['namespace'] in namespaces, 'Pod principal absent')
        need(not any(k.startswith(('cni.projectcalico.org/floatingIPs', 'qos.projectcalico.org/'))
                     for k in p['metadata'].get('annotations', {})), 'unsupported endpoint settings')
        result.append(dict(row, service_account=account, labels=p['metadata'].get('labels', {})))
    return result


def nr(family, table, chain, expr):
    return {'family': family, 'table': table, 'chain': chain, 'expr': expr}


def xt(name, kind='match'): return {'xt': {'type': kind, 'name': name}}


def match(left, right, op='=='): return {'match': {'op': op, 'left': left, 'right': right}}


def meta(key): return {'meta': {'key': key}}


def payload(protocol, field): return {'payload': {'protocol': protocol, 'field': field}}


def calico_expected(rows):
    shapes = json.loads(SHAPES.read_text())
    saved, compiled = {}, {}
    def put(chain, srules, nrules):
        if chain in saved:
            need(saved[chain] == srules and compiled[chain] == nrules, 'profile hash collision')
        saved[chain], compiled[chain] = srules, nrules
    for row in rows:
        ns = 'kns.' + row['namespace']; sa = 'ksa.' + row['namespace'] + '.' + row['service_account']
        mapping = {'@NS_IN@': profile('cali-pri-', ns), '@NS_OUT@': profile('cali-pro-', ns),
                   '@SA_IN@': profile('cali-pri-', sa), '@SA_OUT@': profile('cali-pro-', sa)}
        for key, name, comment in (
            ('fw', 'cali-fw-' + row['interface'], ''), ('tw', 'cali-tw-' + row['interface'], ''),
            ('ns_in', mapping['@NS_IN@'], 'Profile ' + ns + ' ingress'),
            ('ns_out', mapping['@NS_OUT@'], 'Profile ' + ns + ' egress'),
            ('sa_in', mapping['@SA_IN@'], 'Profile ' + sa + ' ingress'),
            ('sa_out', mapping['@SA_OUT@'], 'Profile ' + sa + ' egress')):
            values = dict(mapping, **{'@CHAIN@': name, '@PROFILE_COMMENT@': comment})
            put(name, substitute(shapes[key]['saved'], values), substitute(shapes[key]['native'], values))
    interfaces = sorted(r['interface'] for r in rows)
    common = os.path.commonprefix(interfaces)
    bins = {}
    for interface in interfaces:
        bins.setdefault(interface[:len(common)+1], []).append(interface)
    for direction, switch, field, prefix in [('from', '-i', 'iifname', 'cali-fw-'), ('to', '-o', 'oifname', 'cali-tw-')]:
        root = 'cali-' + direction + '-wl-dispatch'
        def end(chain):
            return (['-A', chain, '-m', 'comment', '--comment', 'Unknown interface', '-j', 'DROP'],
                    nr('ip', 'filter', chain, [xt('comment'), xt('comment'), {'drop': None}]))
        def edge(chain, interface, target):
            return (['-A', chain, switch, interface, '-g', target], nr('ip', 'filter', chain,
                    [match(meta(field), interface.replace('+', '*')), xt('comment'), {'goto': {'target': target}}]))
        sroot, nroot = [], []
        for stem, group in bins.items():
            if len(group) > 1:
                child = root + '-' + stem[len(common):]
                sc, nc = [], []
                for interface in group:
                    a, b = edge(child, interface, prefix + interface); sc.append(a); nc.append(b)
                a, b = end(child); sc.append(a); nc.append(b); put(child, sc, nc)
                a, b = edge(root, stem + '+', child)
            else: a, b = edge(root, group[0], prefix + group[0])
            sroot.append(a); nroot.append(b)
        a, b = end(root); sroot.append(a); nroot.append(b); put(root, sroot, nroot)
    return saved, compiled


def service_expected(api, rows):
    """Exact ClusterIP/Cluster/None affinity compiler, no remote dynamic grant.

    The currently fixed, non-Pod API endpoint is retained in fixed_context.
    Pod endpoints must bind to the fresh local peer/UID and Service selector.
    Zero-ready Services have no per-service NAT chain; their exact REJECT
    dispatch is checked separately. Unrecognized traffic modes fail closed.
    """
    services = api_items(api, 'services'); slices = api_items(api, 'endpointslices')
    saved, compiled, fixed_endpoints = {}, {}, []
    consumed = set()
    for svc in services:
        m, spec = svc['metadata'], svc['spec']; service = (m['namespace'], m['name'])
        need(spec.get('type') == 'ClusterIP' and spec.get('ipFamilies') == ['IPv4'] and
             spec.get('sessionAffinity') == 'None' and spec.get('internalTrafficPolicy') == 'Cluster' and
             not any(spec.get(k) for k in ('externalIPs', 'loadBalancerIP', 'trafficDistribution', 'publishNotReadyAddresses')),
             'unsupported Service mode')
        ipaddress.IPv4Address(spec['clusterIP'])
        related = []
        for i, item in enumerate(slices):
            im = item['metadata']
            if (im['namespace'], im.get('labels', {}).get('kubernetes.io/service-name')) == service:
                need(item.get('addressType') == 'IPv4', 'EndpointSlice family differs')
                related.append(item); consumed.add(i)
        for port in spec['ports']:
            protocol = port['protocol'].lower(); name = port['name']
            need(protocol in ('tcp', 'udp') and type(port['port']) is int and
                 type(port.get('targetPort')) is int and 0 < port['port'] < 65536 and 0 < port['targetPort'] < 65536,
                 'unsupported Service port')
            service_name = m['namespace'] + '/' + m['name'] + ':' + name
            chain = hash_chain('KUBE-SVC-', service_name + protocol)
            selected = []
            for item in related:
                ports = [p for p in item['ports'] if p['name'] == name and p['protocol'].lower() == protocol]
                need(len(ports) == 1 and ports[0]['port'] == port['targetPort'], 'EndpointSlice port differs')
                for endpoint in item['endpoints']:
                    need(len(endpoint.get('addresses', [])) == 1, 'ambiguous endpoint address')
                    address = endpoint['addresses'][0]; ipaddress.IPv4Address(address)
                    conditions = endpoint.get('conditions', {})
                    need(set(conditions) <= {'ready', 'serving', 'terminating'} and
                         all(type(v) is bool for v in conditions.values()), 'unknown EndpointSlice conditions')
                    if endpoint.get('targetRef'):
                        ref = endpoint['targetRef']
                        matches = [r for r in rows if (r['namespace'], r['pod'], r['uid'], r['ip']) ==
                                   (ref.get('namespace'), ref.get('name'), ref.get('uid'), address)]
                        need(ref.get('kind') == 'Pod' and len(matches) == 1 and endpoint.get('nodeName') == HOST and
                             spec.get('selector') and all(matches[0]['labels'].get(k) == v for k, v in spec['selector'].items()),
                             'Service endpoint is not a selected verified local Pod')
                        need(any(o.get('kind') == 'Service' and o.get('uid') == m['uid'] and o.get('controller') is True
                                 for o in item['metadata'].get('ownerReferences', [])), 'EndpointSlice owner differs')
                    else:
                        need(not spec.get('selector'), 'selected Service has an unbound endpoint')
                        fixed_endpoints.append({'service': service_name, 'endpoint': endpoint, 'port': port['targetPort']})
                    need(not conditions.get('terminating', False), 'terminating endpoint fallback requires separate model')
                    if conditions.get('ready', True): selected.append(address)
            need(len(selected) == len(set(selected)), 'Service has duplicate endpoints')
            if not selected: continue
            selected.sort()
            srules = [['-A', chain, '!', '-s', '10.244.0.0/16', '-d', spec['clusterIP'] + '/32', '-p', protocol,
                       '-m', 'comment', '--comment', service_name + ' cluster IP', '-m', protocol,
                       '--dport', str(port['port']), '-j', 'KUBE-MARK-MASQ']]
            nrules = [nr('ip', 'nat', chain, [match(meta('l4proto'), protocol),
                match(payload('ip', 'saddr'), {'prefix': {'addr': '10.244.0.0', 'len': 16}}, '!='),
                match(payload('ip', 'daddr'), spec['clusterIP']), xt('comment'),
                match(payload(protocol, 'dport'), port['port']), {'jump': {'target': 'KUBE-MARK-MASQ'}}])]
            for i, address in enumerate(selected):
                target = address + ':' + str(port['targetPort'])
                ep = hash_chain('KUBE-SEP-', service_name + protocol + target)
                s = ['-A', chain, '-m', 'comment', '--comment', service_name + ' -> ' + target]
                ex = [xt('comment')]
                if i < len(selected) - 1:
                    # kube-proxy formats 10 decimals; xt_statistic stores a
                    # 31-bit fraction; iptables-save prints 11 decimals.
                    p = float(format(1 / (len(selected) - i), '.10f'))
                    probability = format(math.floor(p * 2**31 + .5) / 2**31, '.11f')
                    s += ['-m', 'statistic', '--mode', 'random', '--probability', probability]; ex.append(xt('statistic'))
                srules.append(s + ['-j', ep]); nrules.append(nr('ip', 'nat', chain, ex + [{'jump': {'target': ep}}]))
                saved[ep] = [['-A', ep, '-s', address + '/32', '-m', 'comment', '--comment', service_name, '-j', 'KUBE-MARK-MASQ'],
                             ['-A', ep, '-p', protocol, '-m', 'comment', '--comment', service_name,
                              '-m', protocol, '-j', 'DNAT', '--to-destination', target]]
                compiled[ep] = [nr('ip', 'nat', ep, [match(payload('ip', 'saddr'), address), xt('comment'), {'jump': {'target': 'KUBE-MARK-MASQ'}}]),
                                nr('ip', 'nat', ep, [match(meta('l4proto'), protocol), xt('comment'), xt(protocol), xt('DNAT', 'target')])]
            saved[chain], compiled[chain] = srules, nrules
    need(consumed == set(range(len(slices))), 'orphan/unmodeled EndpointSlice')
    return saved, compiled, sorted(fixed_endpoints, key=fingerprint)


def service_dispatch(api, chains):
    saved={'nat':[],'filter':[]};compiled={'nat':[],'filter':[]};seen=set()
    for svc in api_items(api,'services'):
        m,spec=svc['metadata'],svc['spec']
        for port in spec['ports']:
            protocol=port['protocol'].lower();name=m['namespace']+'/'+m['name']+':'+port['name']
            key=(spec['clusterIP'],protocol,port['port'])
            need(key not in seen,'overlapping Service dispatch');seen.add(key)
            chain=hash_chain('KUBE-SVC-',name+protocol);ready=chain in chains
            table='nat' if ready else 'filter';comment=name+(' cluster IP' if ready else ' has no endpoints')
            target=chain if ready else 'REJECT'
            rule=['-A','KUBE-SERVICES','-d',spec['clusterIP']+'/32','-p',protocol,'-m','comment','--comment',comment,
                  '-m',protocol,'--dport',str(port['port']),'-j',target]
            if not ready:rule+=['--reject-with','icmp-port-unreachable']
            saved[table].append(rule)
            compiled[table].append(nr('ip',table,'KUBE-SERVICES',[match(meta('l4proto'),protocol),
                match(payload('ip','daddr'),spec['clusterIP']),xt('comment'),match(payload(protocol,'dport'),port['port']),
                {'jump':{'target':chain}} if ready else xt('REJECT','target')]))
    # No NodePort Service is supported, but kube-proxy retains its final jump.
    saved['nat'].append(['-A','KUBE-SERVICES','-m','comment','--comment',
         'kubernetes service nodeports; NOTE: this must be the last rule in this chain',
         '-m','addrtype','--dst-type','LOCAL','-j','KUBE-NODEPORTS'])
    compiled['nat'].append(nr('ip','nat','KUBE-SERVICES',[xt('comment'),xt('addrtype'),{'jump':{'target':'KUBE-NODEPORTS'}}]))
    return saved,compiled


def project(view, api, verified_members):
    """Project only AFTER exact validation; no blanket cali-/KUBE- filtering."""
    protection = fixed_api(api); rows = endpoints(api, verified_members)
    cs, cn = calico_expected(rows); ks, kn, fixed_ep = service_expected(api, rows)
    result = copy.deepcopy(view)
    # This module consumes a covered() result from the enclosing observer.
    # It does not accept an opaque-coverage boolean as independent evidence.
    need(set(result) == {'native', 'xtables', 'kernel_ipsets', 'opaque_expressions'}, 'whole covered view required')
    needed = {('ip', 'filter', c): (r, cn[c], True) for c, r in cs.items()}
    needed.update({('ip', 'nat', c): (r, kn[c], False) for c, r in ks.items()})
    ds,dn=service_dispatch(api,ks)
    needed.update({('ip',table,'KUBE-SERVICES'):(ds[table],dn[table],False) for table in ds})
    def dynamic_name(family, table, name):
        return family == 'ip' and (table in ('filter','nat') and name=='KUBE-SERVICES' or
                                  table == 'filter' and re.fullmatch(r'cali-(?:fw-|tw-|pri-|pro-|from-wl-dispatch(?:-|$)|to-wl-dispatch(?:-|$)).*', name) or
                                  table == 'nat' and name.startswith(('KUBE-SVC-', 'KUBE-SEP-')))
    indexed_chains, indexed_rules = {}, {}
    for item in result['native']['nftables']:
        if 'chain' in item:
            v = item['chain']; indexed_chains[v['family'], v['table'], v['name']] = v
        if 'rule' in item:
            v = item['rule']; indexed_rules.setdefault((v['family'], v['table'], v['chain']), []).append(v)
    actual_names = {key for key in indexed_chains if dynamic_name(*key)}
    need(actual_names == set(needed), 'dynamic native chain inventory differs')
    for key, (expected_s, expected_n, calico) in needed.items():
        family, table, chain = key
        value = indexed_chains[key]
        need(set(value) <= {'family', 'table', 'name', 'handle'}, 'dynamic chain has hook/policy/unknown fields')
        xtable = result['xtables'][family][table]
        need(xtable['chains'].get(chain) == '-', 'dynamic saved chain policy differs')
        saved = [r for r in xtable['rules'] if r[1] == chain]
        saved = [strip_hash(r) for r in saved] if calico else saved
        compiled = [rule_shape(r, calico=calico) for r in indexed_rules.get(key, [])]
        if chain=='KUBE-SERVICES':
            # Service IP/protocol/port keys were proved disjoint. Their map
            # iteration order may change; the NodePort tail must remain last.
            tail=1 if table=='nat' else 0
            if tail:
                need(saved[-1:]==expected_s[-1:] and compiled[-1:]==expected_n[-1:],'NodePort tail differs')
                saved,expected_s,compiled,expected_n=saved[:-1],expected_s[:-1],compiled[:-1],expected_n[:-1]
            saved,expected_s=sorted(saved,key=fingerprint),sorted(expected_s,key=fingerprint)
            compiled,expected_n=sorted(compiled,key=fingerprint),sorted(expected_n,key=fingerprint)
        need(saved == expected_s, 'dynamic saved semantics differ: ' + chain)
        need(compiled == expected_n, 'dynamic native semantics differ: ' + chain)
    for family, tables in result['xtables'].items():
        for table, data in tables.items():
            actual = {name for name in data['chains'] if dynamic_name(family, table, name)}
            expected = {c for f, t, c in needed if (f, t) == (family, table)}
            need(actual == expected, 'dynamic save inventory differs')
            data['chains'] = {c: p for c, p in data['chains'].items() if c not in expected}
            data['rules'] = [r for r in data['rules'] if r[1] not in expected]
    arp = {}
    for row in rows:
        chain = 'filter-cali-arp-' + row['interface']
        arp[chain] = nr('arp', 'calico-arp', chain, [match(meta('oifname'), row['interface']),
                     match(payload('arp', 'operation'), 'reply'), match(payload('arp', 'saddr ip'), row['ip']), {'drop': None}])
    arp_chains = {k[2] for k in indexed_chains if k[:2] == ('arp', 'calico-arp') and k[2].startswith('filter-cali-arp-cali')}
    need(arp_chains == set(arp), 'ARP endpoint chain inventory differs')
    for chain, expected in arp.items():
        value = indexed_chains['arp', 'calico-arp', chain]
        need(set(value) <= {'family', 'table', 'name', 'handle'} and
             [rule_shape(r, calico=True) for r in indexed_rules.get(('arp', 'calico-arp', chain), [])] == [expected],
             'ARP endpoint semantics differ')
    arp_maps = [x['map'] for x in result['native']['nftables'] if x.get('map', {}).get('family') == 'arp' and x['map'].get('table') == 'calico-arp']
    need(len(arp_maps) == 1, 'one ARP dispatch map required')
    amap = arp_maps[0]
    need(set(amap) <= {'family', 'table', 'name', 'handle', 'type', 'map', 'elem'} and
         amap.get('name') == 'filter-cali-arp-dispatch' and amap.get('type') == 'ifname' and amap.get('map') == 'verdict' and
         sorted(amap.get('elem', []), key=lambda x: x[0]) == sorted([[r['interface'], {'goto': {'target': 'filter-cali-arp-' + r['interface']}}] for r in rows], key=lambda x: x[0]),
         'ARP dispatch map semantics differ')
    amap['elem'] = []
    removed = set(needed) | {('arp', 'calico-arp', c) for c in arp}
    keep = []
    for item in result['native']['nftables']:
        kind, v = next(iter(item.items()))
        if kind in ('chain', 'rule') and (v.get('family'), v.get('table'), v.get('name' if kind == 'chain' else 'chain')) in removed: continue
        # IDs are allocation metadata, not rules. Preserve all predicates,
        # actions, object definitions and rule order when controllers recreate
        # identical objects. Never recursively strip a user field named handle.
        if kind in ('table', 'chain', 'rule', 'set', 'map', 'counter'): v.pop('handle', None)
        keep.append(item)
    result['native']['nftables'] = keep
    result.pop('opaque_expressions')  # Count changes with verified Pod count.
    result['api_protection'] = protection; result['fixed_service_endpoints'] = fixed_ep
    return result
