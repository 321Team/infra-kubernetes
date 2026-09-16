"""Real pinned receipt producer + actual-derived native fixture; host/CRI mocked."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_node_firewall as old
import test_cp1_cni_transition as txfixture
import test_cp1_cni_transition_observer as nativefixture
import test_cp1_cni_membership as memberfixture
from test_cp1_cni_observer import object_text
from test_cp1_cni_guard import unit_fixture
import cp1_cni_activation as a
import cp1_cni_membership as m
import cp1_cni_transition as t
import cp1_cni_transition_observer as o
from cp1_cni_membership_io import FirewalldMembershipIO

cli=old.load('managed_prepare_test','scripts/prepare-cp1-cni-managed.py')
CLOCK=copy.deepcopy(nativefixture.CLOCK)


def firewall(state):
    result={'hostname':'k8s-control01','ip':'10.1.93.102','selinux':'Enforcing',
        'default_zone':'public','active_zone':'public','nm_uuid':CLOCK['boot_id'],'nm_zone':'',
        'planes':{},'active_policies':{'active_output':'','configurations':{}}}
    for plane,data in state['inventory'].items():
        zone=copy.deepcopy(data['zones']['public']);rules=zone.pop('rich rules')
        result['planes'][plane]={'fields':zone,'rules':rules}
        result['direct_'+plane]=data['direct']
        result['active_policies']['configurations'][plane]={n:object_text({n:v}) for n,v in data['policies'].items()}
    return result


def capture(state=None):
    state=copy.deepcopy(state or txfixture.baseline())
    record,peers=nativefixture.capture(state)
    for suffix in ('','_after'):
        record['queries']['original/firewall'+suffix]={'rc':0,'stdout':json.dumps(firewall(state)),
            'stderr':'','stdout_truncated':False,'stderr_truncated':False}
    return record,peers


def inputs():
    baseline=txfixture.baseline()
    # Exercise the unchanged producer against a complete modeled snapshot.
    class FullBackend(old.FakeBackend):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.state=firewall(baseline)
            self.state['planes']['runtime']['rules']=copy.deepcopy(self.state['planes']['permanent']['rules'])
            self.state['residual_metadata']=[]
    with patch.object(old,'FakeBackend',FullBackend):original=old.cp1_producer_receipt()
    raw=m.encoded(original);record,peers=capture(baseline)
    request={'schema':'fds.cp1-cni-activation-request.v1','scope':copy.deepcopy(m.SCOPE),
        'run_id':'20260917T000000Z-abcdef012345','boot_id':CLOCK['boot_id'],
        'wall_start':1000,'wall_end':8200,'boot_start':110,'boot_end':7310}
    manifest=a.source_manifest()
    return {'raw_original':raw,'original_sha256':hashlib.sha256(raw).hexdigest(),'capture':record,'peers':peers,
        'request':request,'request_sha256':m.fingerprint(request),'manifest':manifest,
        'source_sha256':m.fingerprint(manifest),'clock':copy.deepcopy(CLOCK)}


def prepared():return a.build(**inputs())


class ActivationInputTests(unittest.TestCase):
    def reject(self,change,match='.+'):
        value=inputs();change(value)
        with self.assertRaisesRegex((ValueError,KeyError),match):a.build(**value)

    def test_original_producer_bytes_link_to_complete_raw_capture_without_mutation(self):
        data=inputs();before=copy.deepcopy(data)
        with patch('subprocess.run',side_effect=AssertionError('no host IO')):result=a.build(**data)
        self.assertEqual(data,before)
        self.assertEqual(result['payload']['original_input_sha256'],data['original_sha256'])
        self.assertEqual(len(result['payload']['plan']),10)
        self.assertEqual(result['lease']['activation_sha256'],m.fingerprint(result['payload']))
        self.assertTrue(all(result['payload'][key] is False for key in a.FALSE_FLAGS))

    def test_raw_original_bytes_not_just_parsed_json_are_pinned(self):
        self.reject(lambda d:d.update(raw_original=d['raw_original']+b' '),'byte hash')

    def test_original_receipt_schema_contract_and_result_are_still_validated(self):
        for key,value in (('source_commit','f'*40),('status','ACCEPTED'),('operations',[])):
            def change(d):
                wrapper=json.loads(d['raw_original']);wrapper['receipt'][key]=value
                wrapper['receipt_sha256']=old.n.fingerprint(wrapper['receipt'])
                d['raw_original']=m.encoded(wrapper);d['original_sha256']=hashlib.sha256(d['raw_original']).hexdigest()
            with self.subTest(key=key):self.reject(change)

    def test_duplicate_original_json_keys_are_not_silently_accepted(self):
        def change(d):
            d['raw_original']=b'{"status":"x",'+d['raw_original'][1:]
            d['original_sha256']=hashlib.sha256(d['raw_original']).hexdigest()
        self.reject(change,'duplicate')

    def test_source_missing_extra_or_modified_dependencies_fail(self):
        for case in ('missing','extra','modified','pin'):
            def change(d):
                if case=='missing':d['manifest'].pop('scripts/cp1_cni_delta.py')
                elif case=='extra':d['manifest']['scripts/unreviewed.py']='a'*64
                else:d['manifest']['scripts/'+('node-firewall.py' if case=='pin' else 'cp1_cni_delta.py')]='f'*64
                d['source_sha256']=m.fingerprint(d['manifest'])
            with self.subTest(case=case):self.reject(change,'dependency')

    def test_independent_manifest_and_request_hashes_cannot_be_self_replaced(self):
        self.reject(lambda d:d.update(source_sha256='f'*64),'manifest binding')
        self.reject(lambda d:d.update(request_sha256='f'*64),'request hash')

    def test_wrong_request_scope_host_boot_or_extra_approval_rejected(self):
        for case in ('scope','boot','approved'):
            def change(d):
                if case=='scope':d['request']['scope']['host']='k8s-control02'
                elif case=='boot':d['request']['boot_id']='a'*8+d['request']['boot_id'][8:]
                else:d['request']['approved']=True
                d['request_sha256']=m.fingerprint(d['request'])
            with self.subTest(case=case):self.reject(change)

    def test_lease_limits_mismatched_dual_clock_future_and_startup_age(self):
        for case in ('long','reserve','unequal','future','stale','bool'):
            def change(d):
                r=d['request']
                if case=='long':r['wall_end']+=1;r['boot_end']+=1
                elif case=='reserve':r['wall_end']=1180;r['boot_end']=290
                elif case=='unequal':r['boot_end']+=1
                elif case=='future':r['wall_start']+=5;r['wall_end']+=5
                elif case=='bool':r['boot_start']=True
                else:r['wall_start']-=30;r['wall_end']-=30;r['boot_start']-=30;r['boot_end']-=30
                d['request_sha256']=m.fingerprint(r)
            with self.subTest(case=case):self.reject(change)

    def test_missing_original_projection_never_falls_back_to_six_rules_only(self):
        self.reject(lambda d:d['capture']['queries'].pop('original/firewall'))

    def test_non_public_interface_classification_cannot_pass_initial_reload_gate(self):
        for interface in ('cali+','cali00000000000','c*','ens160','en+','*'):
            state=txfixture.baseline()
            for plane in state['inventory'].values():
                zone=copy.deepcopy(plane['zones']['public']);zone['interfaces']=interface;zone['rich rules']=[]
                plane['zones']['internal']=zone
            with self.subTest(interface=interface),self.assertRaisesRegex(ValueError,'non-public zone'):
                t.validate_baseline(state)

    def test_receipt_after_must_equal_all_live_original_fields(self):
        def change(d):
            for suffix in ('','_after'):
                q=d['capture']['queries']['original/firewall'+suffix]
                fw=json.loads(q['stdout']);fw['nm_zone']='public';q['stdout']=json.dumps(fw)
        self.reject(change,'no longer equals original')

    def test_duplicate_receipt_capture_identity_or_public_projection_race_fails(self):
        for key,value in (('nm_uuid','a'*36),('hostname','k8s-control02'),('direct_runtime','new-rule'),('nm_zone','trusted')):
            def change(d):
                for suffix in ('','_after'):
                    q=d['capture']['queries']['original/firewall'+suffix];fw=json.loads(q['stdout'])
                    fw[key]=value;q['stdout']=json.dumps(fw)
            with self.subTest(key=key):self.reject(change)

    def test_policy_projection_cannot_hide_a_list_info_race(self):
        def change(d):
            q=d['capture']['queries']['original/firewall'];fw=json.loads(q['stdout'])
            fw['active_policies']['configurations']['runtime']['foreign']='foreign\n  target: ACCEPT\n  rich rules:\n'
            q['stdout']=json.dumps(fw)
        self.reject(change,'policy projection raced')

    def test_full_native_daemon_and_peer_evidence_still_required(self):
        for case in ('native','peer','capture','api'):
            def change(d):
                if case=='native':d['capture']['queries']['native']['stdout']='{"nftables":[]}'
                elif case=='peer':d['peers']['raw_after']['sandbox_peers'][0]['iflink']+=1
                elif case=='api':d['capture']['queries']['api/pods']['rc']=1
                else:d['capture']['complete']=False
            with self.subTest(case=case):self.reject(change)

    def test_bound_output_plan_cannot_change_even_when_payload_hash_is_recomputed(self):
        for case in ('plan','document','after','approval'):
            v=prepared();p=v['payload']
            if case=='plan':p['plan'][0]['argv']=['--runtime-to-permanent'];p['plan_sha256']=m.fingerprint(p['plan'])
            elif case=='document':p['documents']['policy.xml']='<policy target="ACCEPT"/>'
            elif case=='after':p['expected_empty_active']['inventory']['runtime']['zones']['public']['services']='ssh cockpit'
            else:p['authorizes_apply']=True
            v['lease']['activation_sha256']=m.fingerprint(p)
            with self.subTest(case=case),self.assertRaises(ValueError):a.validate(v,m.fingerprint(p))

    def test_immutable_activation_hash_and_lease_replay_rejected(self):
        for case in ('digest','renew','schema'):
            v=prepared();expected=v['lease']['activation_sha256']
            if case=='digest':expected='f'*64
            elif case=='renew':v['lease']['boot_end']+=1;v['lease']['wall_end']+=1
            else:v['projection_schema']='fds.cp1-cni-pod-delta.v1'
            with self.subTest(case=case),self.assertRaises(ValueError):a.validate(v,expected)

    def test_handoff_reobserves_baseline_and_cannot_renew_a_stale_proposal(self):
        v=prepared();expected=v['lease']['activation_sha256'];r,p=capture()
        state=a.transition_state(v,expected,r,p,CLOCK)
        self.assertEqual(state['phase'],'PREPARING');self.assertFalse(state['full_recovery_complete'])
        state['baseline']['inventory']['runtime']['direct']='foreign'
        badr,badp=capture(state['baseline'])
        with self.assertRaisesRegex(ValueError,'baseline changed'):a.transition_state(v,expected,badr,badp,CLOCK)
        with self.assertRaisesRegex(ValueError,'too late'):
            a.transition_state(v,expected,r,p,dict(CLOCK,wall=1030,boottime=140))


class ActivationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.activation=prepared();self.lease=self.activation['lease'];self.expected=self.lease['activation_sha256']
        self.state=copy.deepcopy(self.activation['payload']['expected_empty_active']);self.units=unit_fixture(self.lease)

    def observe(self,state=None,units=None,clock=None):
        r,p=capture(state or self.state)
        return a.snapshot_from_records(r,p,self.activation,self.expected,units or self.units,clock or CLOCK)

    def test_empty_active_handoff_seeds_membership_with_guard_already_armed(self):
        actual=self.observe();state=m.initial_state(self.lease,actual,CLOCK)
        seeded=m.seed_observation(state,actual['eligible_observation'],actual,CLOCK)
        self.assertTrue(actual['guard_armed']);self.assertTrue(seeded['latest_observation']['members'])
        self.assertEqual(seeded['operations'],[]);self.assertFalse(seeded['authorizes_cp2_change'])

    def test_only_exact_owned_runtime_entries_and_enable_flag_are_dynamic(self):
        self.state['inventory']['runtime']['ipsets'][t.NAME]['entries']='10.244.246.80/32,cali00000000000'
        self.assertEqual(self.observe()['native_entries'],['10.244.246.80/32,cali00000000000'])
        self.state['inventory']['runtime']['policies'][t.NAME]['disable']='yes'
        self.assertFalse(self.observe()['policy_enabled'])

    def test_foreign_changes_or_permanent_membership_are_never_normalized(self):
        for case in ('foreign','permanent','owner','shape','nm_zone'):
            state=copy.deepcopy(self.state)
            if case=='foreign':state['inventory']['runtime']['zones']['public']['services']='ssh cockpit'
            elif case=='permanent':state['inventory']['permanent']['ipsets'][t.NAME]['entries']='10.244.246.80/32,cali00000000000'
            elif case=='owner':state['descriptions']['policy']='foreign-run'
            elif case=='shape':state['inventory']['runtime']['policies'][t.NAME]['target']='ACCEPT'
            r,p=capture(state)
            if case=='nm_zone':
                for suffix in ('','_after'):
                    q=r['queries']['original/firewall'+suffix];fw=json.loads(q['stdout']);fw['nm_zone']='public';q['stdout']=json.dumps(fw)
            with self.subTest(case=case),self.assertRaises(ValueError):
                a.snapshot_from_records(r,p,self.activation,self.expected,self.units,CLOCK)

    def test_unarmed_missing_or_foreign_guard_cannot_use_bootstrap_bypass(self):
        for case in ('links','timer','dropin'):
            units=copy.deepcopy(self.units)
            if case=='links':units['links']={}
            else:
                row=next(v for k,v in units['properties'].items() if k.endswith('.timer'))
                if case=='timer':row['ActiveState']='inactive'
                else:row['DropInPaths']='/etc/foreign.conf'
            with self.subTest(case=case),self.assertRaises(ValueError):self.observe(units=units)

    def test_old_whole_capture_age_is_preserved_when_peer_sample_is_newer(self):
        value=self.observe()['eligible_observation'];r,p=capture(self.state)
        self.assertEqual(value['wall'],min(r['wall'],p['wall']))
        self.assertEqual(value['boottime'],min(r['boottime'],p['boottime']))

    def test_expiry_boot_change_and_missing_six_fail(self):
        for case in ('expire','boot','six'):
            clock=copy.deepcopy(CLOCK);state=copy.deepcopy(self.state)
            if case=='expire':clock['wall']=self.lease['wall_end']
            elif case=='boot':clock['boot_id']='a'*8+clock['boot_id'][8:]
            else:state['inventory']['runtime']['zones']['public']['rich rules'].remove(t.six_rules()[0])
            with self.subTest(case=case),self.assertRaises(ValueError):self.observe(state=state,clock=clock)

    def test_original_controller_still_rejects_changed_firewall_after_activation(self):
        fw=firewall(self.state);stable={'firewall':fw,'protection':{'fixture':True}}
        with self.assertRaisesRegex(ValueError,'no longer present'):
            old.c.cp1_state_gate({'stable':stable,'sha256':old.n.fingerprint(stable)},receipt=json.loads(inputs()['raw_original'])['receipt'])

    def test_producer_transition_raw_observer_membership_and_recovery_compose(self):
        j=memberfixture.MemoryJournal();model=txfixture.IO(j)
        model.live=copy.deepcopy(self.activation['payload']['baseline']);model.lease=self.lease
        before,peers=capture(model.live)
        tx=t.Transaction(a.transition_state(self.activation,self.expected,before,peers,CLOCK),model,j);tx.activate_empty()
        self.assertEqual(model.live,self.state)
        self.state=model.live
        outer=self
        class Observer:
            def __call__(self):return outer.observe(model.live)
            def membership(self):return self()['eligible_observation']
        observer=Observer();calls=[]
        def run(argv,timeout):
            pending=memberjournal.saved['pending'];self.assertIsNotNone(pending)
            values=set(model.live['inventory']['runtime']['ipsets'][t.NAME]['entries'].split())
            if argv[2].startswith('--add-entry='):values.add(argv[2].split('=',1)[1])
            elif argv[2].startswith('--remove-entry='):values.remove(argv[2].split('=',1)[1])
            else:raise AssertionError(argv)
            model.live['inventory']['runtime']['ipsets'][t.NAME]['entries']=' '.join(sorted(values))
            calls.append(argv);return copy.deepcopy(memberfixture.OK)
        adapter=FirewalldMembershipIO(self.lease,observer,run,lambda:CLOCK['boot_id'],
            wall=lambda:CLOCK['wall'],boottime=lambda:CLOCK['boottime'])
        actual=observer();state=m.seed_observation(m.initial_state(self.lease,actual,CLOCK),actual['eligible_observation'],actual,CLOCK)
        memberjournal=memberfixture.MemoryJournal();memberjournal.save(state)
        tx.handoff_membership()
        manager=m.Manager(state,adapter,memberjournal);result=manager.reconcile(actual['eligible_observation'])
        self.assertGreater(len(calls),0);self.assertTrue(result['generations'])
        tx.recover(membership_state=result)
        self.assertEqual(model.live,self.activation['payload']['baseline']);self.assertFalse(tx.state['full_recovery_complete'])


class PreparationEntryTests(unittest.TestCase):
    def evidence(self,d):
        return {'schema':'fds.cp1-cni-activation-evidence.v1','mode':'READ_ONLY','complete':True,'errors':[],
                'authorizes_apply':False,**{k:d[k] for k in ('capture','peers','clock')}}

    def test_offline_cli_runs_under_isolated_python_and_retains_false_authority(self):
        d=inputs();files={'original':d['raw_original'],'request':m.encoded(d['request']),
                         'manifest':m.encoded(d['manifest']),'evidence':m.encoded(self.evidence(d))}
        with tempfile.TemporaryDirectory() as folder:
            args=[sys.executable,'-I','-B',str(a.ROOT/'scripts/prepare-cp1-cni-managed.py'),'check']
            for name,raw in files.items():
                path=Path(folder)/name;path.write_bytes(raw);args+=['--'+name,str(path)]
            args+=['--original-sha256',d['original_sha256'],'--request-sha256',d['request_sha256'],
                   '--source-sha256',d['source_sha256'],'--evidence-sha256',hashlib.sha256(files['evidence']).hexdigest()]
            p=subprocess.run(args,text=True,capture_output=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr+p.stdout[-2000:]);result=json.loads(p.stdout)
            self.assertFalse(result['authorizes_apply']);self.assertIn('activation',result)
            args[-1]='f'*64;p=subprocess.run(args,text=True,capture_output=True,timeout=15)
            self.assertEqual(p.returncode,2);self.assertEqual(json.loads(p.stdout)['status'],'STOP')

    def test_local_capture_shares_one_deadline_and_requires_receipt_link(self):
        d=inputs();node=old.n
        with (patch.object(cli,'collect',return_value=d['capture']) as collect,
              patch.object(cli,'peer_capture',return_value=d['peers']) as peer,
              patch.object(cli.os,'geteuid',return_value=0)):
            result=cli.local_evidence(node,monotonic=lambda:100,clock=lambda:d['clock'])
        self.assertTrue(result['complete']);self.assertEqual(collect.call_args.kwargs['deadline'],129)
        self.assertTrue(collect.call_args.kwargs['receipt_link']);self.assertEqual(peer.call_args.args[1],129)

    def test_failed_local_capture_retains_diagnostics_and_never_produces_activation(self):
        d=inputs();d['capture']['complete']=False
        with patch.object(cli,'collect',return_value=d['capture']),patch.object(cli,'peer_capture') as peer,patch.object(cli.os,'geteuid',return_value=0):
            result=cli.local_evidence(old.n,monotonic=lambda:100)
        self.assertFalse(result['complete']);self.assertIn('capture',result);peer.assert_not_called()
        with self.assertRaisesRegex(ValueError,'evidence incomplete'):
            cli.prepare(d['raw_original'],d['original_sha256'],d['request'],d['request_sha256'],d['manifest'],d['source_sha256'],result)

    def test_combined_capture_deadline_is_not_renewed_after_peer_collection(self):
        d=inputs();times=iter((100,130))
        with patch.object(cli,'collect',return_value=d['capture']),patch.object(cli,'peer_capture',return_value=d['peers']),patch.object(cli.os,'geteuid',return_value=0):
            result=cli.local_evidence(old.n,monotonic=lambda:next(times),clock=lambda:d['clock'])
        self.assertFalse(result['complete']);self.assertIn('deadline',result['errors'][0]['message'])

    def test_non_root_collect_cannot_issue_any_command(self):
        with patch.object(cli.os,'geteuid',return_value=1000),patch.object(cli,'collect') as collect:
            with self.assertRaisesRegex(ValueError,'sudo/root'):cli.local_evidence(old.n)
            collect.assert_not_called()


if __name__=='__main__':unittest.main()
