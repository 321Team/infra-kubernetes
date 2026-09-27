#!/usr/bin/env python3
"""Pinned upstream builders plus local packet model; not daemon/kernel validation."""
import argparse
import ast
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
from types import SimpleNamespace as NS
import xml.etree.ElementTree as ET
from test_cp1_minimal_change import baseline, m

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
    LOCK['functions.py']='effb5c89fede743e7350e2c32e61f5f23b7f1a907d1d061462880869d9d993e0'
    context={'TABLE_NAME':'firewalld','POLICY_CHAIN_PREFIX':'policy_','DEFAULT_ZONE_TARGET':'default',
             'FirewallError':ValueError,'INVALID_RULE':'INVALID_RULE','INVALID_TYPE':'INVALID_TYPE',
             'INVALID_ENTRY':'INVALID_ENTRY','ipaddress':ipaddress,'socket':socket,'re':re}
    raw=(directory/'functions.py').read_bytes();assert hashlib.sha256(raw).hexdigest()==LOCK['functions.py']
    names=['check_mac','check_single_address','check_address','checkIP','checkIP6','checkIPnMask','checkIP6nMask','normalizeIP6']
    funcs=[f for f in ast.parse(raw).body if isinstance(f,ast.FunctionDef) and f.name in names]
    assert len(funcs)==len(names)
    exec(compile(ast.Module(body=funcs,type_ignores=[]),str(directory/'functions.py'),'exec'),context)
    for name in ('Accept','Reject','Drop','Mark','Masquerade','ForwardPort','IcmpBlock','Tcp_Mss_Clamp'):
        context['Rich_'+name]=type('Rich_'+name,(),{})
    Native=extract(directory/'nftables.py','nftables',NATIVE,context)
    Policies=extract(directory/'fw_policy.py','FirewallPolicy',
                     ['policy_base_chain_name','_get_table_chains_for_policy_dispatch'],context)
    base,_=baseline();documents=m.documents(base)
    zones={m.ZONE:NS(interfaces=['cali+'],ingress_priority=0,egress_priority=0,priority_max=32767,forward=False),
           'public':NS(interfaces=['ens160'],ingress_priority=0,egress_priority=0,priority_max=32767,forward=True)}
    sets={};elements={}
    for name in m.SETS:
        root=ET.fromstring(documents['ipsets/'+name+'.xml'])
        sets[name]=NS(type=root.attrib['type'],options={x.attrib['name']:x.attrib['value'] for x in root.findall('option')})
        elements[name]=[x.text for x in root.findall('entry')]
    fw=NS(nftables_enabled=True,_firewall_backend='nftables',get_log_denied=lambda:'off',
          zone=NS(get_zone=lambda name:zones[name]),ipset=NS(get_ipset=lambda name:sets[name]))
    policy=Policies();policy._fw=fw;fw.policy=policy;policy._policies={};policy.get_policy=lambda name:policy._policies[name]
    backend=Native();backend._fw=fw
    output={};rule_sets={};dispatch={};matrices=[]
    for name in (m.LOCAL,m.EGRESS):
        root=ET.fromstring(documents['policies/'+name+'.xml'])
        p=NS(name=name,derived_from_zone=False,priority=int(root.attrib['priority']),target=root.attrib['target'],
             ingress_zones=[root.find('ingress-zone').attrib['name']],egress_zones=[root.find('egress-zone').attrib['name']])
        policy._policies[name]=p
        tables=policy._get_table_chains_for_policy_dispatch(name)
        assert ('filter','FORWARD') in tables and not any(t=='filter' and c in ('INPUT','OUTPUT') for t,c in tables)
        chain_rules=backend.build_policy_chain_rules(True,name,'filter','FORWARD')
        assert not any(any(k in x for k in ('accept','drop','reject'))
                       for r in chain_rules for x in r['add'].get('rule',{}).get('expr',[]))
        rule_sets[name]=[]
        for xml in root.findall('rule'):
            def address(which):
                e=xml.find(which)
                return None if e is None else NS(addr=e.get('address'),ipset=e.get('ipset'),invert=False)
            action=context['Rich_Accept']();action.limit=None
            rich=NS(family=xml.attrib['family'],source=address('source'),destination=address('destination'),
                    element=None,action=action,log=None,audit=None,priority=0)
            rules=[r for r in backend.build_policy_rich_source_destination_rules(True,name,rich) if r]
            rule=rules[0]['add']['rule'];assert rule['chain']=='filter_FWD_policy_'+name+'_allow'
            rule_sets[name].append(rule)
        # ANY is expanded to actual active zones/interfaces by the daemon.
        # Probe both named ens160 and default fallback, plus the local pair.
        pairs=[(m.ZONE,'cali+')] if name==m.LOCAL else [(m.ZONE,'cali+'),('public','ens160'),('public','+')]
        dispatch[name]=[backend.build_policy_ingress_egress_pair_rules(True,name,'filter','FORWARD',m.ZONE,z,'cali+','',iface,'')[0]['add']['rule']
                        for z,iface in pairs]
        output[name]={'body':rule_sets[name],'dispatch':dispatch[name],'tables':tables}
    native_sets={}
    for name in m.SETS:
        native_sets[name]=backend.build_set_add_rules(name,elements[name])[0]['add']['element']['elem']
        create=backend.build_set_create_rules(name,sets[name].type,sets[name].options)[0]['add']['set']
        assert create['type']==[('ipv6_addr' if name==m.SETS[1] else 'ipv4_addr'),'ifname']
    # The private zone has forward=no: no generic oifname cali* ACCEPT is installed.
    # Compare both custom dispatch sort keys to the same-pair zone termination.
    policy._policies['zone-derived']=NS(derived_from_zone=m.ZONE,priority=0,target='default',ingress_zones=[m.ZONE],egress_zones=['ANY'])
    for name in (m.LOCAL,m.EGRESS):
        for rule in dispatch[name]:
            args=(name,m.ZONE,m.ZONE,'cali*','','cali*','',policy._policies[name].priority)
            start=backend._policy_dispatch_sort_key(*args)['%%POLICY_SORT_KEY%%']
            end=backend._policy_dispatch_sort_key('zone-derived',m.ZONE,m.ZONE,'cali*','','cali*','',0,last=True)['%%POLICY_SORT_KEY%%']
            assert start<end
    def left(expr,packet):
        if 'meta' in expr:return packet[expr['meta']['key']]
        if 'payload' in expr:return packet[expr['payload']['field']]
        if 'concat' in expr:return tuple(left(v,packet) for v in expr['concat'])
        raise AssertionError(expr)
    def compare(actual,want):
        if isinstance(want,dict) and 'prefix' in want:
            network=ipaddress.ip_network(want['prefix']['addr']+'/'+str(want['prefix']['len']))
            return ipaddress.ip_address(actual) in network
        if isinstance(want,dict) and 'concat' in want:return all(compare(a,b) for a,b in zip(actual,want['concat']))
        if isinstance(want,str) and want.endswith('*'):return actual.startswith(want[:-1])
        return actual==want
    def matches(expr,packet):
        for exp in expr:
            if not exp or any(k in exp for k in ('accept','jump')):continue
            match=exp['match'];assert match['op']=='=='
            actual=left(match['left'],packet);want=match['right']
            if isinstance(want,str) and want.startswith('@'):
                if not any(compare(actual,e) for e in native_sets[want[1:]]):return False
            elif not compare(actual,want):return False
        return True
    def accepts(name,packet):
        return packet['hook']=='FORWARD' and any(matches(r['expr'],packet) for r in dispatch[name]) and \
            any(matches(r['expr'],packet) for r in rule_sets[name])
    default={'hook':'FORWARD','nfproto':'ipv4','saddr':'10.244.246.80','daddr':'10.244.246.81','iifname':'calia','oifname':'calib','protocol':'tcp','dport':8080}
    cases=[('local_tcp',{},True,False),('local_udp',{'protocol':'udp'},True,False),('local_icmp',{'protocol':'icmp'},True,False),
           ('new_cali_veth',{'oifname':'califuture'},True,False),('all_l4_8081',{'dport':8081},True,False),
           ('reverse',{'saddr':'10.244.246.81','daddr':'10.244.246.80'},True,False),
           ('physical_ingress',{'iifname':'ens160'},False,False),('spoofed_physical_egress',{'oifname':'ens160'},False,True),
           ('outside_source',{'saddr':'10.244.246.128'},False,False),('outside_destination',{'daddr':'10.244.246.63'},False,False),
           ('vxlan_ingress',{'iifname':'vxlan.calico'},False,False),('vxlan_egress',{'oifname':'vxlan.calico'},False,False),
           ('host_input',{'hook':'INPUT'},False,False),('host_output',{'hook':'OUTPUT'},False,False),
           ('external_dns',{'oifname':'ens160','daddr':'10.1.93.2','protocol':'udp','dport':53},False,True),
           ('external_api',{'oifname':'ens160','daddr':'10.1.93.109','dport':6443},False,True),
           ('external_high_half',{'oifname':'ens160','daddr':'203.0.113.1'},False,True),
           ('unclassified_egress',{'oifname':'dummy0','daddr':'203.0.113.1'},False,False),
           ('ipv6_external_low',{'nfproto':'ipv6','saddr':'2001:db8::1','daddr':'2001:db8::2','oifname':'ens160'},False,True),
           ('ipv6_external_high',{'nfproto':'ipv6','saddr':'fd00::1','daddr':'fd00::2','oifname':'ens160'},False,True),
           ('ipv6_local',{'nfproto':'ipv6','saddr':'fd00::1','daddr':'fd00::2'},False,False)]
    for label,changes,local,external in cases:
        packet=dict(default,**changes);observed=[accepts(name,packet) for name in (m.LOCAL,m.EGRESS)]
        assert observed==[local,external],(label,observed)
        # Existing public intra-zone acceptance was oifname ens160, both IP families.
        if packet['iifname'].startswith('cali') and packet['hook']=='FORWARD':
            assert external==(packet['oifname']=='ens160'),label
        matrices.append({'case':label,'local_accept':observed[0],'preserved_external_accept':observed[1]})
    host=ET.fromstring(documents['zones/'+m.ZONE+'.xml'])
    assert host.find('forward') is None and host.find('interface') is None
    assert sorted(x.attrib['name'] for x in host.findall('service'))==base['objects']['runtime']['zones']['public']['fields']['services'].split()
    assert len(host.findall('rule'))==len(base['objects']['runtime']['zones']['public']['rules'])
    return {'status':'PINNED_UPSTREAM_BUILDERS_PASS','source_sha256':LOCK,'packet_cases':matrices,'generated':output,
            'sets':native_sets,'full_daemon_tested':False,'real_xml_parser_tested':False,'kernel_tested':False,
            'live_cp1_tested':False,'operational_approval':False,
            'limits':'Selected verbatim methods; rich objects, zone membership and packet evaluator are local models.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--upstream-dir',type=Path,required=True);args=p.parse_args()
    print(json.dumps(probe(args.upstream_dir),indent=2,sort_keys=True))
