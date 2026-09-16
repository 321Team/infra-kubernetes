"""Whole observer integration with captured rules + explicitly mocked CRI."""
import copy
import json
import shlex
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_cp1_cni_delta import fixture as dataplane, unready_dns
from test_cp1_cni_membership import raw_fixture, BOOT, m
from test_cp1_cni_membership import MemoryJournal, ModelIO
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


def unarmed(units):
    result = copy.deepcopy(units); result['links'] = {}
    for props in result['properties'].values():
        props.update(ActiveState='inactive', SubState='dead', MainPID='0', Job='', UnitFileState='static')
    return result


class BootstrapIntegrationTests(unittest.TestCase):
    def test_raw_peer_whole_state_and_loaded_definitions_can_seed_without_active_guard(self):
        r,a,u,c = bundle()
        actual = o.snapshot_from_records(r,a,unarmed(u),CLOCK,membership_capture=c,bootstrap=True)
        state = m.initial_state(a['lease'],actual,CLOCK)
        seeded = m.seed_observation(state,actual['eligible_observation'],actual,CLOCK)
        self.assertEqual(seeded['latest_observation']['members'],c['members'])
        self.assertFalse(actual['guard_armed']); self.assertIsNone(actual['guard_deadline'])
        with self.assertRaisesRegex(ValueError,'guard unavailable'):m.snapshot(actual,a['lease'])
        self.assertFalse(seeded['authorizes_cp2_change'])

    def test_bootstrap_mode_cannot_substitute_for_regular_or_withdrawal_observer(self):
        r,a,u,c = bundle()
        with self.assertRaises(ValueError):o.snapshot_from_records(r,a,unarmed(u),CLOCK,membership_capture=c)
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            o.snapshot_from_records(r,a,unarmed(u),CLOCK,membership_capture=c,bootstrap=True,withdrawal=True)
        with self.assertRaises(ValueError):o.snapshot_from_records(r,a,unarmed(u),CLOCK,membership_capture=c,bootstrap=1)

    def test_bootstrap_rejects_running_work_pending_jobs_enabled_units_and_dropins(self):
        r,a,u,c = bundle()
        for key,value in (('ActiveState','active'),('MainPID','123'),('Job','33/start'),
                          ('UnitFileState','enabled'),('DropInPaths','/run/foreign.conf')):
            candidate=unarmed(u); name=next(n for n in candidate['properties'] if n.endswith('-work.service'))
            candidate['properties'][name][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                o.snapshot_from_records(r,a,candidate,CLOCK,membership_capture=c,bootstrap=True)

    def test_bootstrap_rejects_missing_raw_peer_legacy_projection_stale_and_changed_foreign_state(self):
        for case in ('peer','legacy','stale','foreign'):
            r,a,u,c=bundle()
            if case=='peer':c=None
            elif case=='legacy':a.pop('projection_schema')
            elif case=='stale':r['wall']=900
            else:r['queries']['runtime/direct_after']['stdout']='foreign rule'
            with self.subTest(case=case),self.assertRaises(ValueError):
                o.snapshot_from_records(r,a,unarmed(u),CLOCK,membership_capture=c,bootstrap=True)

    def test_nonempty_grant_cannot_enter_bootstrap_even_with_a_matching_fixed_hash(self):
        r,a,u,c=bundle();item=m.entry(c['members'][0])
        for suffix in ('','_after'):
            text=r['queries']['runtime/ipsets/'+o.NAME+suffix]['stdout']
            r['queries']['runtime/ipsets/'+o.NAME+suffix]['stdout']=text.replace('  entries: ','  entries: '+item)
            raw=json.loads(r['queries']['native'+suffix]['stdout'])
            owned=next(v['set'] for v in raw['nftables'] if v.get('set',{}).get('name')==o.NAME)
            owned['elem']=[{'concat':[{'prefix':{'addr':c['members'][0]['ip'],'len':32}},c['members'][0]['interface']]}]
            r['queries']['native'+suffix]['stdout']=json.dumps(raw)
        with self.assertRaisesRegex(ValueError,'empty enabled'):
            o.snapshot_from_records(r,a,unarmed(u),CLOCK,membership_capture=c,bootstrap=True)

    def test_bootstrap_observation_cannot_authorize_add_until_full_guard_proof(self):
        r,a,u,c=bundle()
        actual=o.snapshot_from_records(r,a,unarmed(u),CLOCK,membership_capture=c,bootstrap=True)
        state=m.seed_observation(m.initial_state(a['lease'],actual,CLOCK),actual['eligible_observation'],actual,CLOCK)
        journal=MemoryJournal();io=ModelIO(a['lease'],journal);io.now=copy.deepcopy(CLOCK)
        io.live=copy.deepcopy(actual);io.member_rows=copy.deepcopy(c['members'])
        manager=m.Manager(state,io,journal);journal.save(state)
        with self.assertRaisesRegex(ValueError,'guard unavailable'):manager.reconcile(actual['eligible_observation'])
        self.assertEqual(io.calls,[])
        self.assertEqual(manager.state['phase'],'WITHDRAWING')
        with self.assertRaisesRegex(ValueError,'withdrawal is sticky'):
            manager.reconcile(actual['eligible_observation'])

    def test_correct_bootstrap_then_guard_order_commits_first_generation(self):
        r,a,u,c=bundle()
        actual=o.snapshot_from_records(r,a,unarmed(u),CLOCK,membership_capture=c,bootstrap=True)
        state=m.seed_observation(m.initial_state(a['lease'],actual,CLOCK),actual['eligible_observation'],actual,CLOCK)
        journal=MemoryJournal();io=ModelIO(a['lease'],journal);io.now=copy.deepcopy(CLOCK)
        io.member_rows=copy.deepcopy(c['members'])
        manager=m.Manager(state,io,journal);journal.save(state)
        # Same lease; the ordinary whole-state observer must now attest the
        # enabled independent guard before any actual membership can change.
        active=o.snapshot_from_records(r,a,u,CLOCK,membership_capture=c)
        io.live=copy.deepcopy(active)
        result=manager.reconcile(active['eligible_observation'])
        self.assertEqual(len(result['expected_entries']),3)
        self.assertEqual(result['lease'],a['lease'])
        m.validate_generation_chain(result)

    def test_live_bootstrap_cannot_prime_active_membership_cache(self):
        r,a,u,c=bundle();units=unarmed(u)
        node=SimpleNamespace(__file__=str(Path(o.__file__).with_name('node-firewall.py')))
        instance=o.LocalObserver(node,a,lambda _:b'',lambda _:'',lambda:CLOCK)
        # Only read sources/records are replaced; the real whole-state verifier
        # and loaded-unit checks still execute against the reference capture.
        def module_exec(module):module.collect=lambda _:c
        spec=SimpleNamespace(loader=SimpleNamespace(exec_module=module_exec))
        with patch.object(o,'collect',return_value=r),patch.object(o,'query_units',return_value=units) as query,\
             patch.object(o.importlib.util,'spec_from_file_location',return_value=spec),\
             patch.object(o.importlib.util,'module_from_spec',return_value=SimpleNamespace()):
            result=instance.bootstrap()
        self.assertFalse(result['guard_armed']);self.assertIsNone(instance.last_verified)
        self.assertFalse(query.call_args.kwargs['include_links'])
        with self.assertRaisesRegex(ValueError,'fresh whole state'):instance.membership()

    def test_bootstrap_seed_never_extends_absolute_or_observation_deadline(self):
        r,a,u,c=bundle();actual=o.snapshot_from_records(r,a,unarmed(u),CLOCK,membership_capture=c,bootstrap=True)
        state=m.seed_observation(m.initial_state(a['lease'],actual,CLOCK),actual['eligible_observation'],actual,CLOCK)
        later={**CLOCK,'wall':CLOCK['wall']+30,'boottime':CLOCK['boottime']+30}
        self.assertEqual(m.guard_action(state,later),'RECOVER')
        self.assertEqual(state['lease'],a['lease'])


if __name__=='__main__':unittest.main()
