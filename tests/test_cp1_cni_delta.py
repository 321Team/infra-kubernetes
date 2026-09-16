"""Captured rules are the oracle; no production compiler builds fixtures.

Identities are synthetic. These tests prove constrained semantic comparison,
not target CRI ownership, runtime Pod replacement or a packet-flow result.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cp1_cni_delta as d

REFERENCE = Path(__file__).with_name('fixtures') / 'CP1_POD_DELTA_REFERENCE.json'


def fixture():
    f = json.loads(REFERENCE.read_text())
    return f['view'], f['api'], f['members']


def replace(value, old, new):
    if isinstance(value, str): return value.replace(old, new)
    if isinstance(value, list): return [replace(x, old, new) for x in value]
    if isinstance(value, dict): return {replace(k, old, new): replace(v, old, new) for k, v in value.items()}
    return value


def unready_dns(view,api):
    """Source-derived kube-proxy zero-ready example, independent of compiler.

    Unlike the baseline fixture this layout is not a captured target result.
    Native xt REJECT is the only supported encoding; unknown encodings block.
    """
    for sl in api['endpointslices']['items']:
        if sl['metadata']['namespace']=='kube-system':
            for e in sl['endpoints']:e['conditions']['ready']=False
    nat=view['xtables']['ip']['nat'];filt=view['xtables']['ip']['filter']
    service={'KUBE-SVC-TCOU7JCQXEZGVUNU','KUBE-SVC-ERIFXISQEP7F7OF4','KUBE-SVC-JD5MR3NA4I4DYORP'}
    removed=service|{r[-1] for r in nat['rules'] if r[1] in service and r[-1].startswith('KUBE-SEP-')}
    dispatch=[r for r in nat['rules'] if r[1]=='KUBE-SERVICES' and '10.96.0.10/32' in r]
    for r in dispatch:
        new=copy.deepcopy(r);new[new.index('--comment')+1]=new[new.index('--comment')+1].replace(' cluster IP',' has no endpoints')
        new[-1]='REJECT';new+=['--reject-with','icmp-port-unreachable'];filt['rules'].append(new)
    nat['chains']={k:v for k,v in nat['chains'].items() if k not in removed}
    nat['rules']=[r for r in nat['rules'] if r[1] not in removed and r not in dispatch]
    objects=[];rejects=[]
    for item in view['native']['nftables']:
        kind,body=next(iter(item.items()))
        if body.get('family')=='ip' and body.get('table')=='nat':
            if body.get('name') in removed or body.get('chain') in removed:continue
            if kind=='rule' and body['chain']=='KUBE-SERVICES' and any(e.get('jump',{}).get('target') in service for e in body['expr']):
                body=copy.deepcopy(body);body['table']='filter';body['expr'][-1]={'xt':{'type':'target','name':'REJECT'}}
                rejects.append({'rule':body});continue
        objects.append(item)
    view['native']['nftables']=objects+rejects


class DeltaTests(unittest.TestCase):
    def test_all_service_endpoints_unready_still_preserves_eligible_pod_membership(self):
        unready_dns(self.v,self.a)
        for p in self.a['pods']['items']:p['status']['conditions']=[{'type':'Ready','status':'False'}]
        self.same()
        # A hidden REJECT parameter is not a harmless compatibility detail.
        rule=next(r for r in self.v['xtables']['ip']['filter']['rules'] if '--reject-with' in r)
        rule[-1]='tcp-reset';self.changed()

    def test_zero_local_pods_zero_endpoint_rules_remain_a_supported_empty_set(self):
        unready_dns(self.v,self.a)
        self.a['pods']['items']=[p for p in self.a['pods']['items'] if p['spec'].get('hostNetwork')]
        for sl in self.a['endpointslices']['items']:
            if sl['metadata']['namespace']=='kube-system':sl['endpoints']=[]
        self.rows=[]
        table=self.v['xtables']['ip']['filter']
        removed={c for c in table['chains'] if c.startswith(('cali-fw-','cali-tw-','cali-pri-','cali-pro-'))}
        table['chains']={c:p for c,p in table['chains'].items() if c not in removed}
        table['rules']=[r for r in table['rules'] if r[1] not in removed and not
                       (r[1] in ('cali-from-wl-dispatch','cali-to-wl-dispatch') and ('-i' in r or '-o' in r))]
        objects=[]
        for item in self.v['native']['nftables']:
            kind,v=next(iter(item.items()))
            if v.get('family')=='ip' and v.get('table')=='filter':
                if v.get('name') in removed or v.get('chain') in removed:continue
                if kind=='rule' and v['chain'] in ('cali-from-wl-dispatch','cali-to-wl-dispatch') and 'match' in v['expr'][0]:continue
            if v.get('family')=='arp':
                if str(v.get('name',v.get('chain',''))).startswith('filter-cali-arp-cali'):continue
                if kind=='map':v['elem']=[]
            objects.append(item)
        self.v['native']['nftables']=objects
        self.same()

    def test_disjoint_service_dispatch_order_can_change_but_tail_cannot(self):
        rules=self.v['xtables']['ip']['nat']['rules']
        indices=[i for i,r in enumerate(rules) if r[1]=='KUBE-SERVICES' and '-d' in r]
        for i,r in zip(indices,list(reversed([rules[i] for i in indices]))):rules[i]=r
        native=self.v['native']['nftables']
        indices_native=[i for i,x in enumerate(native) if x.get('rule',{}).get('chain')=='KUBE-SERVICES' and
                        x['rule'].get('family')=='ip' and x['rule'].get('table')=='nat' and 'match' in x['rule']['expr'][0]]
        for i,r in zip(indices_native,list(reversed([native[i] for i in indices_native]))):native[i]=r
        self.same()
        indices=[i for i,r in enumerate(rules) if r[1]=='KUBE-SERVICES']
        rules[indices[0]],rules[indices[-1]]=rules[indices[-1]],rules[indices[0]]
        self.changed()

    def setUp(self):
        self.v, self.a, self.rows = fixture()
        self.base = d.fingerprint(d.project(self.v, self.a, self.rows))

    def changed(self):
        try:
            result = d.fingerprint(d.project(self.v, self.a, self.rows))
        except (ValueError, KeyError, TypeError):
            return
        self.assertNotEqual(result, self.base, 'unsafe change disappeared')

    def same(self):
        self.assertEqual(d.fingerprint(d.project(self.v, self.a, self.rows)), self.base)

    def rule(self, chain):
        return next(x['rule'] for x in self.v['native']['nftables'] if x.get('rule', {}).get('chain') == chain)

    def test_reference_and_known_upstream_identifiers(self):
        self.same()
        self.assertEqual(d.profile('cali-pro-', 'ksa.kube-system.coredns'), 'cali-pro-_u2Tn2rSoAPffvE7JO6')
        self.assertEqual(d.hash_chain('KUBE-SVC-', 'kube-system/kube-dns:dnstcp'.replace('dnstcp', 'dnsudp')),
                         'KUBE-SVC-TCOU7JCQXEZGVUNU')

    def test_pod_uid_sandbox_and_interface_replacement_preserves_fixed_policy(self):
        # Deliberately reverse interface sort order relative to Pod names.
        old = self.rows[0]['interface']
        self.v, self.a, self.rows = replace([self.v, self.a, self.rows], old, 'califffffffffff')
        uid = self.rows[0]['uid']
        self.a, self.rows = replace([self.a, self.rows], uid, 'replacement-pod-uid')
        self.rows[0]['sandbox_id'] = 'f' * 64
        self.rows[0]['ifindex'] = 200
        for chain,switch,field in [('cali-from-wl-dispatch','-i','iifname'),('cali-to-wl-dispatch','-o','oifname')]:
            rules=self.v['xtables']['ip']['filter']['rules']
            positions=[i for i,r in enumerate(rules) if r[1]==chain and switch in r]
            ordered=sorted((rules[i] for i in positions),key=lambda r:r[r.index(switch)+1])
            for i,r in zip(positions,ordered):rules[i]=r
            rules=self.v['native']['nftables']
            positions=[i for i,x in enumerate(rules) if x.get('rule',{}).get('chain')==chain and 'match' in x['rule']['expr'][0]]
            ordered=sorted((rules[i] for i in positions),key=lambda x:x['rule']['expr'][0]['match']['right'])
            for i,r in zip(positions,ordered):rules[i]=r
        self.same()

    def test_pod_ip_replacement_updates_dnat_hash_arp_and_peer(self):
        old, new = '10.244.246.77', '10.244.246.82'
        for name, proto, port in [('dns', 'udp', 53), ('dns-tcp', 'tcp', 53), ('metrics', 'tcp', 9153)]:
            prefix = 'kube-system/kube-dns:' + name + proto
            def chain(ip):
                return 'KUBE-SEP-' + base64.b32encode(hashlib.sha256((prefix+ip+':'+str(port)).encode()).digest()).decode()[:16]
            self.v = replace(self.v, chain(old), chain(new))
        self.v, self.a, self.rows = replace([self.v, self.a, self.rows], old, new)
        # kube-proxy sorts endpoint IPs, so replacement .82 follows .78.
        for chain in ['KUBE-SVC-TCOU7JCQXEZGVUNU','KUBE-SVC-ERIFXISQEP7F7OF4','KUBE-SVC-JD5MR3NA4I4DYORP']:
            rules = [r for r in self.v['xtables']['ip']['nat']['rules'] if r[1] == chain]
            for token in ('--comment', '-j'):
                i, j = rules[1].index(token)+1, rules[2].index(token)+1
                rules[1][i], rules[2][j] = rules[2][j], rules[1][i]
            rules = [x['rule'] for x in self.v['native']['nftables'] if x.get('rule',{}).get('chain') == chain]
            rules[1]['expr'][-1], rules[2]['expr'][-1] = rules[2]['expr'][-1], rules[1]['expr'][-1]
        self.same()

    def test_api_bookkeeping_and_ready_changes_do_not_grant_new_scope(self):
        for key in d.FIXED_API:
            for item in d.api_items(self.a,key):
                item['metadata']['resourceVersion'] = '9999'
                item['status'] = {'fixture':'changed'}
        for p in self.a['pods']['items']:
            p['status']['conditions'] = [{'type':'Ready','status':'False'}]
        self.same()

    def test_endpoint_slice_reordering_is_not_policy_drift(self):
        for s in self.a['endpointslices']['items']: s['endpoints'].reverse()
        self.a['endpointslices']['items'].reverse()
        self.same()

    def test_shallow_handles_only_are_ignored(self):
        for item in self.v['native']['nftables']:
            v = next(iter(item.values()))
            if 'handle' in v: v['handle'] += 100000
        self.same()
        self.rule('filter_IN_public')['extra'] = {'handle':'not allocation metadata'}
        self.changed()

    def test_protected_api_changes_are_retained(self):
        for key in d.FIXED_API:
            with self.subTest(key=key):
                self.v, self.a, self.rows = fixture()
                values = d.api_items(self.a,key)
                if values: values[0]['metadata'].setdefault('labels',{})['new-policy-scope'] = 'changed'
                else: self.a[key]['items'].append({'metadata':{'name':'new-policy','uid':'new'},'spec':{'action':'Allow'}})
                self.changed()

    def test_dynamic_native_verdict_changed_even_when_hidden_xt_is_covered(self):
        r = self.rule('cali-from-wl-dispatch')
        r['expr'][-1] = {'accept':None}
        self.changed()

    def test_dynamic_saved_hidden_parameters_changed(self):
        cases = [('--ctstate','NEW'),('--set-xmark','0xffffffff/0xffffffff'),('--to-destination','1.1.1.1:53'),
                 ('--probability','0.90000000000'),('--dport','22')]
        for key,new in cases:
            with self.subTest(key=key):
                self.v, self.a, self.rows = fixture()
                rule = next(r for table in self.v['xtables']['ip'].values() for r in table['rules']
                            if key in r and (r[1].startswith(('cali-fw-','cali-tw-','KUBE-SVC-','KUBE-SEP-'))))
                rule[rule.index(key)+1] = new
                self.changed()

    def test_foreign_rule_order_and_kernel_set_elements_are_retained(self):
        self.v['kernel_ipsets']['cali40network-ip-pools']['entries'] = ['0.0.0.0/0']
        self.changed()
        self.v, self.a, self.rows = fixture()
        self.v['xtables']['ip']['filter']['rules'].reverse()
        self.changed()

    def test_missing_stale_and_extra_endpoint_chain_rejected(self):
        for operation in ('missing','extra','hook'):
            with self.subTest(operation=operation):
                self.v, self.a, self.rows = fixture()
                item = next(x for x in self.v['native']['nftables'] if x.get('chain',{}).get('name','').startswith('cali-fw-'))
                if operation == 'missing': self.v['native']['nftables'].remove(item)
                elif operation == 'extra':
                    extra=copy.deepcopy(item);extra['chain']['name']='cali-fw-cali99999999999';self.v['native']['nftables'].append(extra)
                else: item['chain']['hook']='forward'
                self.changed()

    def test_arp_wrong_address_target_and_extra_map_element(self):
        for operation in ('ip','target','extra'):
            with self.subTest(operation=operation):
                self.v, self.a, self.rows = fixture()
                if operation=='ip': self.rule('filter-cali-arp-calic2af0f77fd7')['expr'][2]['match']['right']='10.244.246.89'
                else:
                    amap=next(x['map'] for x in self.v['native']['nftables'] if 'map' in x)
                    if operation=='extra': amap['elem'].append(['ens160',{'accept':None}])
                    else: amap['elem'][0][1]={'accept':None}
                self.changed()

    def test_service_endpoint_identity_selector_and_remote_node_fail_closed(self):
        for field,value in [('uid','other'),('nodeName','k8s-control02'),('selector','other'),('owner','other')]:
            with self.subTest(field=field):
                self.v,self.a,self.rows=fixture()
                s=next(s for s in self.a['endpointslices']['items'] if s['metadata']['namespace']=='kube-system')
                if field=='uid': s['endpoints'][0]['targetRef']['uid']=value
                elif field=='nodeName':s['endpoints'][0][field]=value
                elif field=='owner':s['metadata']['ownerReferences'][0]['uid']=value
                else:next(s for s in self.a['services']['items'] if s['metadata']['name']=='kube-dns')['spec']['selector']={'app':value}
                self.changed()

    def test_unready_all_or_duplicate_endpoint_rejected(self):
        for case in ('empty','duplicate','condition'):
            with self.subTest(case=case):
                self.v,self.a,self.rows=fixture();s=self.a['endpointslices']['items'][0]
                if case=='empty':s['endpoints']=[]
                elif case=='duplicate':s['endpoints'].append(copy.deepcopy(s['endpoints'][0]))
                else:s['endpoints'][0]['conditions']['ready']=1
                self.changed()

    def test_duplicate_api_pod_unknown_phase_and_missing_peer_rejected(self):
        for case in ('duplicate','phase','peer'):
            with self.subTest(case=case):
                self.v,self.a,self.rows=fixture()
                if case=='duplicate':self.a['pods']['items'].append(copy.deepcopy(self.a['pods']['items'][0]))
                elif case=='phase':next(p for p in self.a['pods']['items'] if not p['spec'].get('hostNetwork'))['status']['phase']='Unknown'
                else:self.rows.pop()
                self.changed()

    def test_statistics_rounding_and_dispatch_shallow_split(self):
        # Independently published xt_statistic lround(2**31*p), %.11f.
        rows=d.endpoints(self.a,self.rows)
        svc=next(x for x in self.a['endpointslices']['items'] if x['metadata']['namespace']=='kube-system')
        endpoint=copy.deepcopy(svc['endpoints'][0]);source=next(r for r in rows if r['uid']==endpoint['targetRef']['uid'])
        extra=dict(source,uid='third',pod='third',ip='10.244.246.83',interface='cali11111111111',ifindex=100,sandbox_id='f'*64)
        endpoint['targetRef'].update(uid='third',name='third');endpoint['addresses']=[extra['ip']]
        svc['endpoints'].append(endpoint)
        saved,_,_=d.service_expected(self.a,rows+[extra])
        probabilities=[r[r.index('--probability')+1] for r in saved['KUBE-SVC-TCOU7JCQXEZGVUNU'] if '--probability' in r]
        self.assertEqual(probabilities,['0.33333333349','0.50000000000'])
        for i,r in enumerate(rows):r['interface']=['calia0000000000','calia1000000000','calib0000000000'][i]
        saved,_=d.calico_expected(rows)
        self.assertEqual(saved['cali-from-wl-dispatch'][0],['-A','cali-from-wl-dispatch','-i','calia+','-g','cali-from-wl-dispatch-a'])
        self.assertEqual(len(saved['cali-from-wl-dispatch-a']),3)
        self.assertEqual(saved['cali-from-wl-dispatch-a'][-1][-2:],['-j','DROP'])


if __name__ == '__main__': unittest.main()
