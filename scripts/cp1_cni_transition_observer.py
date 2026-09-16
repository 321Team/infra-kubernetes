"""Whole-state verifier through definition creation, reload and restoration.

Validate exact six-rule native expressions and their tail position before
removing only these known, mutually disjoint grants from fixed comparison.
Actual rule presence remains in inventory and must match each transaction
prefix. No wildcard exemption for other firewalld/Calico objects exists.
"""
import copy
import re

from cp1_cni_membership import need, same, fingerprint, number, boot
from cp1_cni_observer import objects, success, parse_json, native_view, NAME
from cp1_cni_delta import project, FIXED_API, capture_members, rule_shape
from cp1_cni_transition import six_rules, validate_snapshot


def six_native():
    result = {}
    for address in (102,103,104):
        for port in ('10250','2379-2380'):
            key = 'rule family=ipv4 source address=10.1.93.%s/32 port port=%s protocol=tcp accept' % (address,port)
            result[key] = {'family':'inet','table':'firewalld','chain':'filter_IN_public_allow','expr':[
                {'match':{'op':'==','left':{'payload':{'protocol':'ip','field':'saddr'}},'right':'10.1.93.'+str(address)}},
                {'match':{'op':'==','left':{'payload':{'protocol':'tcp','field':'dport'}},
                          'right':10250 if port=='10250' else {'range':[2379,2380]}}}, {'accept':None}]}
    return result


def strip_six(raw, present):
    shapes = six_native(); found = []; kept = []; tail = False
    for item in raw['nftables']:
        value = item.get('rule')
        if value and (value.get('family'),value.get('table'),value.get('chain')) == ('inet','firewalld','filter_IN_public_allow'):
            shaped = rule_shape(value)
            match = next((rule for rule, expected in shapes.items() if same(shaped,expected)), None)
            if match is not None:
                need(match not in found, 'duplicate original native grant'); found.append(match); tail=True; continue
            need(not tail, 'foreign INPUT rule moved after original six tail')
        kept.append(copy.deepcopy(item))
    need(set(found) == set(present), 'original native grants differ from daemon inventory')
    return {'nftables':kept}


def linked_firewall(q, suffix, inventory, host):
    """Cross-check the original helper's snapshot against the same capture.

    Preserve the original serialized receipt projection unchanged. It is
    additional evidence, never a replacement for the complete native view.
    """
    fw = parse_json(q['original/firewall' + suffix])
    need(set(fw) == {'hostname','ip','selinux','default_zone','active_zone','nm_uuid','nm_zone',
                    'planes','active_policies','direct_runtime','direct_permanent'}, 'original firewall fields differ')
    need(fw['hostname']=='k8s-control01' and fw['ip']=='10.1.93.102' and fw['selinux']=='Enforcing' and
         fw['default_zone']==host['default_zone'] and fw['active_zone']==host['management_zone'] and
         fw['nm_uuid']==host['management_connection'] and fw['nm_zone'] in ('','public'),
         'original management identity differs')
    need(set(fw['planes'])==set(inventory), 'original plane inventory differs')
    policies=fw['active_policies']
    need(set(policies)=={'active_output','configurations'} and isinstance(policies['active_output'],str) and
         set(policies['configurations'])==set(inventory), 'original policy inventory differs')
    for plane,data in inventory.items():
        zone=copy.deepcopy(data['zones']['public']);rules=zone.pop('rich rules')
        zone['services']=' '.join(sorted(zone.get('services','').split()))
        need(same(fw['planes'][plane],{'fields':zone,'rules':rules}) and
             fw['direct_'+plane]==data['direct'], 'original public/direct projection raced')
        parsed={}
        for name,text in policies['configurations'][plane].items():
            row=objects(text);need(set(row)=={name}, 'original policy header differs');parsed.update(row)
        need(same(parsed,data['policies']), 'original policy projection raced')
    return fw


def observe(record, membership_capture, clock, *, receipt_link=False):
    """No hashes/flags are accepted as proof of a native rule's meaning.

    The root caller authenticates local collector provenance. A JSON capture
    itself is not authentication. API loss blocks broad changes; independent
    narrow policy disable uses its own API-free observer.
    """
    need(type(receipt_link) is bool, 'receipt linkage mode must be explicit')
    need(record.get('schema')=='fds.cp1-cni-state-capture.v1' and record.get('complete') is True and
         record.get('errors')==[], 'transition whole capture incomplete')
    boot(clock['boot_id']); need(record.get('boot_id')==clock['boot_id'], 'transition boot differs')
    rows = capture_members(membership_capture,clock['boot_id'],clock)
    for key in ('wall','boottime'):
        need(number(record[key]) <= number(record['ended_'+key]) <= number(clock[key]) and
             0 <= clock[key]-record[key] < 30, 'transition capture stale/future')
    q = {k:success(v) for k,v in record['queries'].items()}
    for key in ('version','state','selinux','configuration','boot'):
        need(q[key]==q[key+'_after'], 'transition environment raced')
    need(q['version']=='2.4.3' and q['state']=='running' and q['selinux']=='Enforcing' and
         q['boot']==clock['boot_id'], 'transition environment unsupported')
    config = {}
    for line in q['configuration'].splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            need('=' in line, 'unknown daemon setting line')
            key,value=line.split('=',1);key=key.strip()
            need(key not in config,'duplicate daemon setting');config[key]=value.strip()
    need(config.get('FirewallBackend','nftables')=='nftables', 'transition requires nftables backend')
    # The baseline validator already requires Runtime/Permanent direct rules
    # to match. Unknown reload modes cannot silently change that contract.
    need(config.get('FlushAllOnReload','yes') in ('yes','no'), 'unknown reload flush setting')
    snapshots = []; original = []
    for suffix in ('','_after'):
        inventory = {}; descriptions = {}
        for plane in ('runtime','permanent'):
            prefix=plane+'/'
            data={kind:objects(q[prefix+kind+suffix]) for kind in ('zones','policies')}
            names=q[prefix+'ipsets'+suffix].split()
            need(len(names)==len(set(names)) and len(names)<=128,'invalid transition set inventory')
            data['ipsets']={}
            for name in names:
                parsed=objects(q[prefix+'ipsets/'+name+suffix]);need(set(parsed)=={name},'set header differs')
                data['ipsets'][name]=parsed[name]
            data['direct']=q[prefix+'direct'+suffix]
            for zone in data['zones'].values():
                rules=zone['rich rules'];need(len(rules)==len(set(rules)),'duplicate daemon zone rule')
                zone['rich rules']=sorted(rules)
            inventory[plane]=data
            if plane=='permanent':
                for kind,table in (('policy','policies'),('ipset','ipsets')):
                    key=prefix+kind+'-description'+suffix
                    if NAME in data[table]:descriptions[kind]=q[key]
                    else:need(key not in q,'unlisted managed description')
        runtime=inventory['runtime'];have_set=NAME in runtime['ipsets'];have_policy=NAME in runtime['policies']
        need(not have_policy or have_set,'runtime policy has no set')
        mode=('ACTIVE' if runtime['policies'][NAME].get('disable')=='no' else 'DISABLED') if have_policy else ('DISABLED' if have_set else 'BASELINE')
        compat={k:q[k+suffix] for k in ('iptables_save','ip6tables_save','ipsets')}
        native=native_view(parse_json(q['native'+suffix]),mode,compatibility=compat)
        if have_set:need(native['entries']==runtime['ipsets'][NAME]['entries'].split(),'native/daemon members differ')
        present=set(runtime['zones']['public']['rich rules']) & set(six_rules())
        foreign=strip_six({'nftables':native['fixed']['nftables']},present)
        # native_view validated this exact set, so its zero-element definition
        # is now an owned delta, not an unverified object name exemption.
        if have_set:
            foreign['nftables']=[x for x in foreign['nftables'] if not (x.get('set',{}).get('family')=='inet' and
                x['set'].get('table')=='firewalld' and x['set'].get('name')==NAME)]
        api={k:parse_json(q['api/'+k+suffix]) for k in (*FIXED_API,'pods','endpointslices')}
        fixed_native=project({'native':foreign,'xtables':native['fixed']['xtables'],
                              'kernel_ipsets':native['fixed']['kernel_ipsets'],'opaque_expressions':0},api,rows)
        host=parse_json(q['host'+suffix]);need(host.get('default_zone')==host.get('management_zone')=='public' and
            isinstance(host.get('management_connection'),str) and
            re.fullmatch(r'[a-fA-F0-9-]{36}',host['management_connection']), 'management zone/identity drift')
        if receipt_link:
            fw=linked_firewall(q,suffix,inventory,host);original.append(fw)
            host['management_connection_zone']=fw['nm_zone']
        fixed={'native':fixed_native,'host':host,'protection':parse_json(q['protection'+suffix]),
               'configuration':q['configuration'],'version':q['version']}
        snapshot={'inventory':inventory,'descriptions':descriptions,'fixed_sha256':fingerprint(fixed)}
        validate_snapshot(snapshot); snapshots.append(snapshot)
    need(same(snapshots[0],snapshots[1]), 'transition state changed between samples')
    if receipt_link:need(same(original[0],original[1]), 'original firewall changed between samples')
    return snapshots[0]
