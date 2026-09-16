#!/usr/bin/env python3
"""Run hash-pinned firewalld 2.4.3 Python rule builders, never nft/kernel.

Pass a directory containing unmodified upstream nftables.py, fw_policy.py,
fw_zone.py. AST extraction keeps the selected methods verbatim. Model objects,
rich-rule object construction, and packet evaluation are local test doubles;
the real XML/D-Bus parser, daemon transaction and kernel are NOT exercised.
"""
import argparse
import ast
import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path
from types import SimpleNamespace as NS
import xml.etree.ElementTree as ET

LOCK = {'nftables.py': '42ad0f052ffc119d1263c3b66d299360bfc78f7d0de7011f3fbe83a0f2b4c232',
        'fw_policy.py': '67394a8707f2bc9fb9a828c88a047cdc221e1ad12a3e46534e7b6808733786ab',
        'fw_zone.py': '0e96f43b963fb50627ea5de37d7a32ed335194074f61f21ff7886936ee5aad8a'}
NATIVE = ['_set_type_list', 'build_set_create_rules', '_set_get_family', '_set_match_fragment',
          '_set_entry_fragment', 'build_set_add_rules', '_rule_addr_fragment',
          '_rich_rule_family_fragment', '_rich_rule_destination_fragment', '_rich_rule_source_fragment',
          '_rich_rule_limit_fragment', '_rich_rule_chain_suffix', '_rich_rule_priority_fragment',
          '_rich_rule_log', '_rich_rule_audit', '_rich_rule_action',
          'build_policy_rich_source_destination_rules', 'build_policy_chain_rules',
          'build_policy_ingress_egress_pair_rules', '_policy_dispatch_sort_key', 'build_zone_forward_rules']


def extract(path, class_name, names, context):
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LOCK[path.name], 'upstream bytes differ'
    source = ast.parse(raw)
    cls = next(v for v in source.body if isinstance(v, ast.ClassDef) and v.name == class_name)
    selected = [v for v in cls.body if isinstance(v, ast.FunctionDef) and v.name in names]
    assert {v.name for v in selected} == set(names), 'method missing'
    cls.body = selected
    module = ast.Module(body=[cls], type_ignores=[])
    exec(compile(module, str(path), 'exec'), context)
    return context[class_name]


def probe(directory):
    source = Path(__file__).resolve().parents[1] / 'scripts/prepare-cp1-cni-policy.py'
    spec = importlib.util.spec_from_file_location('policy_proposal_upstream', source)
    proposal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proposal)
    context = {'TABLE_NAME': 'firewalld', 'POLICY_CHAIN_PREFIX': 'policy_', 'DEFAULT_ZONE_TARGET': 'default',
               'FirewallError': ValueError, 'INVALID_RULE': 'INVALID_RULE', 'INVALID_TYPE': 'INVALID_TYPE',
               'INVALID_ENTRY': 'INVALID_ENTRY', 'ipaddress': ipaddress}
    for name in ('Accept', 'Reject', 'Drop', 'Mark', 'Masquerade', 'ForwardPort', 'IcmpBlock', 'Tcp_Mss_Clamp'):
        context['Rich_' + name] = type('Rich_' + name, (), {})
    Native = extract(directory / 'nftables.py', 'nftables', NATIVE, context)
    Policies = extract(directory / 'fw_policy.py', 'FirewallPolicy',
                       ['policy_base_chain_name', '_get_table_chains_for_policy_dispatch'], context)
    Zones = extract(directory / 'fw_zone.py', 'FirewallZone', ['_interface'], context)
    peers = [{'interface': 'cali0123456789a'}, {'interface': 'cali0123456789b'}]
    documents = proposal.documents(peers)
    set_xml = ET.fromstring(documents['ipsets/' + proposal.IPSET + '.xml'])
    policy_xml = ET.fromstring(documents['policies/' + proposal.POLICY + '.xml'])
    entries = [v.text for v in set_xml.findall('entry')]
    set_obj = NS(type=set_xml.attrib['type'], options={v.attrib['name']: v.attrib['value'] for v in set_xml.findall('option')})
    policy_obj = NS(name=proposal.POLICY, derived_from_zone=False, priority=int(policy_xml.attrib['priority']),
                    target=policy_xml.attrib['target'], ingress_zones=[policy_xml.find('ingress-zone').attrib['name']],
                    egress_zones=[policy_xml.find('egress-zone').attrib['name']])
    public = NS(interfaces=['ens160'], ingress_priority=0, egress_priority=0, priority_max=32767, forward=True)
    fw = NS(nftables_enabled=True, _firewall_backend='nftables', get_log_denied=lambda: 'off',
            zone=NS(get_zone=lambda _: public), ipset=NS(get_ipset=lambda _: set_obj))
    policy = Policies(); fw.policy = policy; policy._fw = fw
    policy._policies = {proposal.POLICY: policy_obj}; policy.get_policy = lambda name: policy._policies[name]
    backend = Native(); backend._fw = fw
    rich_xml = policy_xml.find('rule')
    source_obj = NS(addr=None, ipset=rich_xml.find('source').attrib['ipset'], invert=False)
    dest_obj = NS(addr=None, ipset=rich_xml.find('destination').attrib['ipset'], invert=False)
    action = context['Rich_Accept'](); action.limit = None
    rich = NS(family=rich_xml.attrib['family'], source=source_obj, destination=dest_obj,
              element=None, action=action, log=None, audit=None, priority=0)
    added = [r for r in backend.build_policy_rich_source_destination_rules(True, proposal.POLICY, rich) if r]
    assert len(added) == 1
    rule = added[0]['add']['rule']
    expr = [e for e in rule['expr'] if e]
    assert rule['chain'] == 'filter_FWD_policy_' + proposal.POLICY + '_allow'
    assert expr[-1] == {'accept': None}
    assert [e['match']['left'] for e in expr[:-1]] == [
        {'meta': {'key': 'nfproto'}},
        {'concat': [{'payload': {'protocol': 'ip', 'field': 'daddr'}}, {'meta': {'key': 'oifname'}}]},
        {'concat': [{'payload': {'protocol': 'ip', 'field': 'saddr'}}, {'meta': {'key': 'iifname'}}]}]
    create_set = backend.build_set_create_rules(proposal.IPSET, set_obj.type, set_obj.options)
    set_entries = backend.build_set_add_rules(proposal.IPSET, entries)
    assert create_set[0]['add']['set']['type'] == ['ipv4_addr', 'ifname']
    expected_elements = [{'concat': [{'prefix': {'addr': '10.244.246.64', 'len': 26}}, p['interface']]} for p in peers]
    assert set_entries[0]['add']['element']['elem'] == expected_elements
    table_chains = policy._get_table_chains_for_policy_dispatch(proposal.POLICY)
    assert ('filter', 'FORWARD') in table_chains
    assert not any(c in ('INPUT', 'OUTPUT') for t, c in table_chains)
    chain_rules = backend.build_policy_chain_rules(True, proposal.POLICY, 'filter', 'FORWARD')
    assert not any(any(k in e for k in ('accept', 'drop', 'reject'))
                   for r in chain_rules for e in r['add'].get('rule', {}).get('expr', []))
    catchall_dispatch = backend.build_policy_ingress_egress_pair_rules(
        True, proposal.POLICY, 'filter', 'FORWARD', 'public', 'public', '+', '', '+', '')
    # Default-zone policy dispatch can be broad; the rich-rule intersection must
    # carry BOTH interface constraints, including for spoofed physical traffic.
    assert catchall_dispatch[0]['add']['rule']['expr'] == [{'jump': {'target': 'filter_FWD_policy_' + proposal.POLICY}}]

    def matches(packet):
        if packet['hook'] != 'FORWARD':
            return False
        for expression in expr[:-1]:
            m = expression['match']
            assert m['op'] == '=='
            if m['left'] == {'meta': {'key': 'nfproto'}}:
                if packet['family'] != m['right']:
                    return False
            else:
                parts = m['left']['concat']
                ip = ipaddress.ip_address(packet[parts[0]['payload']['field']])
                iface = packet[parts[1]['meta']['key']]
                assert m['right'] == '@' + proposal.IPSET
                if not any(ip in ipaddress.ip_network(e['concat'][0]['prefix']['addr'] + '/' +
                                                      str(e['concat'][0]['prefix']['len'])) and iface == e['concat'][1]
                           for e in expected_elements):
                    return False
        return True
    base = {'hook': 'FORWARD', 'family': 'ipv4', 'saddr': '10.244.246.80', 'daddr': '10.244.246.81',
            'iifname': peers[0]['interface'], 'oifname': peers[1]['interface'], 'protocol': 'tcp', 'dport': 8080}
    cases = [('local_tcp_8080', {}, True), ('local_tcp_8081_not_a_negative', {'dport': 8081}, True),
             ('local_udp', {'protocol': 'udp'}, True), ('local_icmp', {'protocol': 'icmp'}, True),
             ('reverse', {'saddr': base['daddr'], 'daddr': base['saddr'], 'iifname': base['oifname'], 'oifname': base['iifname']}, True),
             ('physical_spoofed_source', {'iifname': 'ens160'}, False),
             ('physical_spoofed_destination', {'oifname': 'ens160'}, False),
             ('tunnel_in', {'iifname': 'vxlan.calico'}, False), ('tunnel_out', {'oifname': 'vxlan.calico'}, False),
             ('nonpeer_in', {'saddr': '10.244.247.80'}, False), ('nonpeer_out', {'daddr': '10.244.247.81'}, False),
             ('new_veth', {'oifname': 'cali0123456789c'}, False),
             ('external_dns', {'oifname': 'ens160', 'daddr': '10.1.93.2', 'protocol': 'udp', 'dport': 53}, False),
             ('external_api', {'oifname': 'ens160', 'daddr': '10.1.93.109', 'dport': 6443}, False),
             ('external_harbor', {'oifname': 'ens160', 'daddr': '10.1.93.110', 'dport': 443}, False),
             ('host_input', {'hook': 'INPUT'}, False), ('host_output', {'hook': 'OUTPUT'}, False),
             ('ipv6', {'family': 'ipv6', 'saddr': 'fd00::1', 'daddr': 'fd00::2'}, False)]
    matrix = []
    for name, change, expected in cases:
        observed = matches(dict(base, **change))
        assert observed is expected, name
        matrix.append({'name': name, 'new_rule_matches': observed, 'expected': expected})

    # Execute the upstream default-zone special case, not our interpretation.
    forwarded = []
    zones = Zones(); zones._fw = NS(enabled_backends=lambda: [NS(policies_supported=True,
        build_zone_forward_rules=lambda *args, **kwargs: forwarded.append(kwargs) or [])])
    zones.get_zone = lambda _: public; zones.policy_name_from_zones = lambda *args: 'public-to-any'
    for name in ('_interface_or_source_update_zone_termination',
                 '_interface_or_source_update_policies_derived_from_zone', '_interface_or_source_update_policies'):
        setattr(zones, name, lambda *args: None)
    transaction = NS(add_rules=lambda *args: None)
    zones._interface(True, 'public', '+', transaction)
    assert not forwarded, 'default fallback unexpectedly grants wildcard intra-zone forwarding'
    zones._interface(True, 'public', 'ens160', transaction)
    assert forwarded == [{'interface': 'ens160'}]
    wildcard_entry = backend._set_entry_fragment(proposal.IPSET, '10.244.246.64/26,cali+')
    assert wildcard_entry[0]['concat'][1] == 'cali+', 'must not assume prefix conversion in sets'
    return {'status': 'UPSTREAM_FUNCTION_PROBE_PASS', 'upstream_ref': 'v2.4.3', 'source_sha256': LOCK,
            'scope': 'PINNED_UPSTREAM_FUNCTIONS_WITH_MODEL_OBJECTS_AND_LOCAL_MATCH_EVALUATOR',
            'functions_executed': NATIVE + ['policy_base_chain_name', '_get_table_chains_for_policy_dispatch', '_interface'],
            'packet_matrix': matrix, 'new_rule': rule, 'set_create': create_set, 'set_entries': set_entries,
            'dispatch_tables': table_chains, 'default_public_dispatch': catchall_dispatch,
            'default_zone_plus_is_not_intra_zone_forward': True, 'set_plus_is_not_zone_prefix_conversion': True,
            'full_daemon_tested': False, 'real_xml_parser_tested': False, 'kernel_tested': False,
            'actual_cp1_connectivity_tested': False, 'operational_approval': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-dir', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.upstream_dir), sort_keys=True, indent=2))
