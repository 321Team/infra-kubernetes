"""Whole observer integration with captured rules + explicitly mocked CRI."""
import copy
import json
import shlex
import unittest
from test_cp1_cni_delta import fixture as dataplane, unready_dns
from test_cp1_cni_membership import raw_fixture, BOOT, m
from test_cp1_cni_observer import fixture, native, o
from test_cp1_cni_guard import unit_fixture
import cp1_cni_delta as d

CLOCK={'boot_id':BOOT,'wall':1002,'boottime':112,'monotonic':110}


def save_text(tables):
    lines=[]
    for name,t in tables.items():
        lines.append('*'+name)
        lines.extend(':'+c+' '+p+' [0:0]' for c,p in t['chains'].items())
        lines.extend('[0:0] '+shlex.join(r) for r in t['rules'])
        lines.append('COMMIT')
    return '\n'.join(lines)+'\n'


def bundle():
    record,activation,_=fixture();v,api,rows=dataplane()
    # Public/foreign captured objects stay intact; install modeled owned rules.
    full=copy.deepcopy(v['native'])
    full['nftables'] += [x for x in native()['nftables'] if o.NAME in json.dumps(x)]
    compat={'iptables_save':save_text(v['xtables']['ip']),'ip6tables_save':save_text(v['xtables']['ip6']),
            'ipsets':'\n'.join('create '+n+' '+' '.join(x['definition'])+'\n'+
                      '\n'.join('add '+n+' '+e for e in x['entries']) for n,x in v['kernel_ipsets'].items())}
    for suffix in ('','_after'):
        record['queries']['native'+suffix]['stdout']=json.dumps(full)
        for k,value in {**compat,**{'api/'+k:json.dumps(value) for k,value in api.items()}}.items():
            record['queries'][k+suffix]={'rc':0,'stdout':value,'stderr':'','stdout_truncated':False,'stderr_truncated':False}
    nf=o.native_view(full,'ACTIVE',compatibility=compat)['fixed']
    projected=d.project({'native':{'nftables':nf['nftables']},'xtables':nf['xtables'],
                         'kernel_ipsets':nf['kernel_ipsets'],'opaque_expressions':0},api,rows)
    activation['fixed']['native']={'nftables':projected.pop('native')['nftables'],**projected,'projection_schema':d.PROJECTION}
    activation['projection_schema']=d.PROJECTION
    activation['lease']['fixed_state_sha256']=m.fingerprint(activation['fixed'])
    raw=raw_fixture();raw['pods']=copy.deepcopy(api['pods']);raw['links']=[];raw['routes']=[];raw['sandbox_peers']=[]
    raw['pods'].setdefault('metadata',{})['resourceVersion']='fixture-list-rv'
    for r in rows:
        raw['links'].append({'ifname':r['interface'],'ifindex':r['ifindex'],'link_index':r['peer_ifindex'],
                             'linkinfo':{'info_kind':'veth'},'flags':['UP']})
        raw['routes'].append({'dst':r['ip'],'dev':r['interface'],'scope':'link'})
        raw['sandbox_peers'].append({'pod_uid':r['uid'],'pod':r['pod'],'namespace':r['namespace'],'state':'SANDBOX_READY',
            'sandbox_id':r['sandbox_id'],'host_ifindex':r['ifindex'],'ifindex':r['peer_ifindex'],'iflink':r['ifindex'],'ipv4':[r['ip']],
            'pid_start_before':100,'pid_start_after':100,'netns_before':200,'netns_after':200})
    capture={'schema':'fds.cp1-cni-membership-raw.v1','mode':'READ_ONLY','complete':True,'errors':[],
             'authorizes_apply':False,'boot_id':BOOT,'wall':1001,'ended_wall':1002,'boottime':111,'ended_boottime':112,
             'raw':raw,'raw_after':copy.deepcopy(raw),'members':m.members(rows)}
    return record,activation,unit_fixture(activation['lease']),capture


class DeltaObserverTests(unittest.TestCase):
    def test_zero_endpoint_reject_extension_requires_save_payload_and_native_target(self):
        from cp1_cni_dataplane import covered
        v,a,rows=dataplane();unready_dns(v,a)
        sets='\n'.join('create '+n+' '+' '.join(x['definition'])+'\n'+
              '\n'.join('add '+n+' '+e for e in x['entries']) for n,x in v['kernel_ipsets'].items())
        proof=covered(v['native'],save_text(v['xtables']['ip']),save_text(v['xtables']['ip6']),sets)
        d.project(proof,a,rows)
        rule=next(x['rule'] for x in v['native']['nftables'] if {'xt':{'type':'target','name':'REJECT'}} in x.get('rule',{}).get('expr',[]))
        rule['expr'][-1]={'accept':None}
        with self.assertRaisesRegex(ValueError,'target differs'):
            covered(v['native'],save_text(v['xtables']['ip']),save_text(v['xtables']['ip6']),sets)

    def test_projection_requires_raw_peer_and_returns_bound_observation(self):
        r,a,u,c=bundle()
        result=o.snapshot_from_records(r,a,u,CLOCK,membership_capture=c)
        self.assertTrue(result['owned']);self.assertEqual(result['eligible_observation']['members'],c['members'])
        self.assertEqual(result['fixed_state_sha256'],a['lease']['fixed_state_sha256'])

    def test_no_peer_fake_completion_boot_stale_and_mismatched_peer_block(self):
        for case in ('absent','incomplete','stale','boot','peer','old-namespace'):
            with self.subTest(case=case):
                r,a,u,c=bundle()
                if case=='absent':c=None
                elif case=='incomplete':c['raw']={}
                elif case=='stale':c['wall']=900
                elif case=='boot':c['boot_id']='87654321-1234-1234-1234-123456789abc'
                elif case=='peer':c['raw_after']['sandbox_peers'][0]['iflink']+=1
                else:c['raw_after']['sandbox_peers'][0].update(netns_before=999,netns_after=999)
                with self.assertRaises((ValueError,KeyError)):
                    o.snapshot_from_records(r,a,u,CLOCK,membership_capture=c)

    def test_same_raw_members_but_service_target_changed_cannot_match_activation(self):
        r,a,u,c=bundle()
        for suffix in ('','_after'):
            key='api/endpointslices'+suffix;v=json.loads(r['queries'][key]['stdout'])
            e=next(e for s in v['items'] for e in s['endpoints'] if 'targetRef' in e)
            e['targetRef']['uid']='foreign'
            r['queries'][key]['stdout']=json.dumps(v)
        with self.assertRaisesRegex(ValueError,'verified local Pod'):
            o.snapshot_from_records(r,a,u,CLOCK,membership_capture=c)

    def test_legacy_activation_cannot_silently_select_new_projection(self):
        r,a,u,c=bundle();a.pop('projection_schema')
        with self.assertRaises(o.StateMismatch):o.snapshot_from_records(r,a,u,CLOCK,membership_capture=c)

    def test_unknown_projection_blocks_without_fallback(self):
        r,a,u,c=bundle();a['projection_schema']='future-permissive'
        with self.assertRaisesRegex(ValueError,'unknown activation'):
            o.snapshot_from_records(r,a,u,CLOCK,membership_capture=c)


if __name__=='__main__':unittest.main()
