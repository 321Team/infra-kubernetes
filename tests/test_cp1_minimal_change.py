"""Failure-oriented transaction/receipt tests; no host mutation and no fake PASS claims."""
import copy
import contextlib
import io as streams
import datetime as dt
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from test_node_firewall import load, FakeBackend, MemoryJournal, cp1_producer_receipt
from test_cp1_cni_trial_analysis import fixture, pack, a

m = load('minimal_test', 'scripts/cp1_minimal_change.py')
e = load('minimal_entry_test', 'scripts/run-cp1-minimal-change.py')
c = load('minimal_consumer_test', 'scripts/cp1_minimal_consumer.py')
CLOCK = dt.datetime.fromisoformat('2026-09-15T01:21:59+00:00')
BUNDLE = m.fingerprint(e.source_files())


def text(name, obj):
    return name + '\n' + '\n'.join('  ' + key + ': ' + value for key, value in obj['fields'].items()) + \
        ('\n  rich rules:' + ('\n' + '\n'.join('    ' + rule for rule in obj['rules']) if obj['rules'] else '') if 'type' not in obj['fields'] else '')


def baseline(policy_state=None):
    observed = json.loads((Path(__file__).parent / 'fixtures/CP1_FCJv61h8_FIREWALL.json').read_text())['firewall']
    original_init = FakeBackend.__init__
    def complete(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        for plane in ('runtime','permanent'):
            self.state['planes'][plane]['fields']=copy.deepcopy(observed['planes'][plane]['fields'])
        self.state.update(ip='10.1.93.102', direct_runtime='', direct_permanent='',
                          active_policies=copy.deepcopy(observed['active_policies'] if policy_state is None else policy_state))
    with patch.object(FakeBackend, '__init__', complete): original = cp1_producer_receipt()
    fw = copy.deepcopy(m.legacy().validate_cp1_receipt(original)['after'])
    objects = {p: {'zones': {'public': copy.deepcopy(fw['planes'][p])},
                     'policies': {name:m.parse_info(raw,name) for name,raw in fw['active_policies']['configurations'][p].items()}, 'ipsets': {}}
               for p in ('runtime', 'permanent')}
    zones=json.loads((Path(__file__).parent/'fixtures/CP1_QP5Lan_ZONES.json').read_text())['list_all_zones']
    import re
    for plane,raw in zones.items():
        for block in re.split(r'\n\s*\n',raw.strip()):
            name=block.split()[0]
            if name!='public':objects[plane]['zones'][name]=m.parse_info(block,name)
    base = dict(firewall=fw, objects=objects, services={p: {'ssh': 'MOCK-shared-service'} for p in objects},
                protection={'calico': 'MOCK-unchanged'}, boot_id='a'*8+'-'+ 'b'*4+'-'+ 'c'*4+'-'+ 'd'*4+'-'+ 'e'*12,
                version='2.4.3', backend='nftables', ipam={'cidr':m.BLOCK,'affinity':'host:'+m.HOST,'state':'confirmed'})
    return base, original


def authorization(base):
    return {'schema':'fds.cp1-minimal-authorization.v1','execution_approved':True,
            'operator':'GWONUK','inert_permanent_staging_acknowledged':True,'maintenance_freeze':True,'console_recovery_available':True,'backup_verified':True,'protection_reviewed':True,
            'independent_reviewer':'hayeounglee','review_decision':'APPROVED',
            'review_url':'https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-123',
            'reviewed_bundle_sha256':BUNDLE,'before_sha256':m.fingerprint(base),
            'scope':{'hostname':m.HOST,'pod_block':m.BLOCK,'interfaces':'cali+','l4':'ALL','plane':'runtime'},
            'window_start':'2026-09-15T01:20:00+00:00','window_end':'2026-09-15T02:50:00+00:00',
            'reviewed_at':'2026-09-15T01:19:00+00:00'}


class FakeIO:
    def __init__(self, base):
        self.base=copy.deepcopy(base); self.view=copy.deepcopy(base); self.job_id='1'*16
        self.actions=[]; self.armed=False; self.fail=None; self.clock=CLOCK; self.model=m.expected_objects(base)
    def now(self): return self.clock
    def snapshot(self):
        fw=self.view['firewall']
        fw['planes']={p:copy.deepcopy(self.view['objects'][p]['zones']['public']) for p in ('runtime','permanent')}
        def policy_text(plane,name,obj):
            raw=self.base['firewall']['active_policies']['configurations'][plane].get(name)
            return raw if raw is not None and m.parse_info(raw,name)==obj else text(name,obj)
        fw['active_policies']['configurations']={p:{name:policy_text(p,name,obj) for name,obj in self.view['objects'][p]['policies'].items()}
                                               for p in ('runtime','permanent')}
        fw['active_policies']['active_output']=self.base['firewall']['active_policies']['active_output']
        zone=self.view['objects']['runtime']['zones'].get(m.ZONE,{})
        if zone.get('fields',{}).get('interfaces')=='cali+':
            for name,target in ((m.LOCAL,m.ZONE),(m.EGRESS,'ANY')):
                fw['active_policies']['active_output']+='\n'+name+'\n  ingress-zones: '+m.ZONE+'\n  egress-zones: '+target
        return copy.deepcopy(self.view)
    def action(self,name):
        self.actions.append(name)
        if self.fail==len(self.actions): self.fail=None; raise OSError('uncertain mutation')
    def arm(self,withdraw): self.armed=True; self.action('arm')
    def stage(self,kind,name,xml):
        assert self.armed
        self.view['objects']['permanent'][kind][name]=copy.deepcopy(self.model[kind][name])
        if kind == 'zones' and name == m.ZONE:
            self.view['objects']['permanent'][kind][name]['fields']['forward']='yes'
            self.action('stage-create:'+name)
            self.view['objects']['permanent'][kind][name]['fields']['forward']='no'
            self.action('stage-disable-forward:'+name)
        else:
            self.action('stage:'+name)
    def remove_permanent(self,kind,name):
        del self.view['objects']['permanent'][kind][name];self.action('remove:'+name)
    def reload_restore(self,base):
        self.view['objects']['runtime']=copy.deepcopy(self.view['objects']['permanent'])
        self.view['objects']['runtime']['zones']['public']=copy.deepcopy(base['objects']['runtime']['zones']['public'])
        self.action('reload')
    def bind(self,present):
        if present:
            assert all(name not in self.view['objects']['permanent'][kind] for kind,name in m.ORDER)
        self.view['objects']['runtime']['zones'][m.ZONE]['fields']['interfaces']='cali+' if present else '';self.action('bind:'+str(present))
    def disarm(self): self.armed=False;self.action('disarm')


class Transactions(unittest.TestCase):
    def setUp(self):
        self.base,self.original=baseline();self.io=FakeIO(self.base);self.journal=MemoryJournal();self.auth=authorization(self.base)
    def apply(self): return m.apply(self.io,self.journal,self.original,self.auth,BUNDLE)
    def test_apply_is_runtime_only_and_recovery_restores_whole_original(self):
        record=self.apply()
        self.assertTrue(m.validate_view(record['after'],self.base));self.assertFalse(record['cp1_accepted'])
        self.assertEqual(record['after']['objects']['permanent'],self.base['objects']['permanent'])
        m.recover(self.io,self.journal,record)
        self.assertEqual(self.io.snapshot(),self.base);self.assertFalse(self.io.armed)
    def test_uncertain_failure_at_every_mutation_recovered(self):
        for index in range(2,15):
            with self.subTest(index=index):
                self.io=FakeIO(self.base);self.io.fail=index;self.journal=MemoryJournal()
                with self.assertRaises(OSError): self.apply()
                self.assertEqual(self.io.snapshot(),self.base);self.assertFalse(self.io.armed)
    def test_no_timer_means_no_firewall_mutation(self):
        self.io.fail=1
        with self.assertRaises(OSError): self.apply()
        self.assertEqual(self.io.actions,['arm']);self.assertEqual(self.io.snapshot(),self.base)
    def test_unreviewed_before_source_scope_and_window_block_before_arm(self):
        changes=[('execution_approved',False),('review_decision','COMMENTED'),('independent_reviewer','GWONUK'),
                 ('reviewed_bundle_sha256','f'*64),('before_sha256','e'*64),('window_end','2026-09-15T01:25:00+00:00'),
                 ('review_url','https://example.org'),('scope',{'plane':'permanent'})]
        for key,val in changes:
            with self.subTest(key=key),self.assertRaises((ValueError,TypeError)):
                m.apply(self.io,self.journal,self.original,dict(self.auth,**{key:val}),BUNDLE)
        self.assertFalse(self.io.actions)
    def test_pasted_angle_brackets_normalized_only_for_correct_review_url(self):
        self.auth['review_url']='<'+self.auth['review_url']+'>'
        self.assertNotIn('<',self.apply()['review_url'])
    def test_other_runtime_and_service_delta_blocks_before_reload(self):
        for mode in ('object','service'):
            base=copy.deepcopy(self.base)
            if mode=='object': base['objects']['runtime']['zones']['other']={'fields':{},'rules':[]}
            else: base['services']['runtime']['ssh']='MODIFIED'
            with self.subTest(mode=mode),self.assertRaises(ValueError):m.validate_baseline(base,self.original)
    def test_existing_owned_name_blocks_before_arm(self):
        self.io.view['objects']['runtime']['ipsets'][m.SETS[0]]={'fields':{},'rules':[]}
        with self.assertRaises(ValueError):self.apply()
        self.assertEqual(self.io.actions,[])
    def test_missing_owned_dependency_cannot_activate(self):
        original=self.io.reload_restore
        def omit(base):
            original(base);del self.io.view['objects']['runtime']['policies'][m.LOCAL]
        with patch.object(self.io,'reload_restore',side_effect=omit),self.assertRaises(ValueError):self.apply()
        self.assertNotIn('bind:True',self.io.actions)
    def test_foreign_drift_withdraws_owned_binding_without_blind_reload(self):
        record=self.apply();self.io.view['protection']['calico']='CHANGED';actions=len(self.io.actions)
        with self.assertRaises(ValueError):m.recover(self.io,self.journal,record)
        self.assertEqual(self.io.actions[actions:],['bind:False']);self.assertTrue(record['binding_withdrawn_on_drift'])
    def test_owned_policy_drift_is_never_normalized_away(self):
        record=self.apply();self.io.view['objects']['runtime']['policies'][m.LOCAL]['fields']['target']='ACCEPT'
        with self.assertRaises(ValueError):m.recover(self.io,self.journal,record)
        self.assertTrue(self.io.armed)
    def test_recovery_after_reboot_requires_console_review(self):
        record=self.apply();self.io.view['boot_id']='f'*36
        with self.assertRaises(ValueError):m.recover(self.io,self.journal,record)
    def test_slow_prepare_does_not_activate(self):
        original=self.io.remove_permanent
        def slow(kind,name):
            original(kind,name);self.io.clock=m.instant(self.auth['window_end'])-dt.timedelta(seconds=200)
        with patch.object(self.io,'remove_permanent',side_effect=slow),self.assertRaises(ValueError):self.apply()
        self.assertNotIn('bind:True',self.io.actions);self.assertEqual(self.io.snapshot(),self.base)
    def test_second_recovery_is_idempotent(self):
        record=self.apply();m.recover(self.io,self.journal,record);n=len(self.io.actions)
        m.recover(self.io,self.journal,record);self.assertEqual(self.io.actions[n:],['disarm'])
    def test_full_snapshot_unsupported_key_or_object_drift_blocks(self):
        record=self.apply()
        for key in ('protection','services','version','backend','ipam','boot_id'):
            bad=copy.deepcopy(record['after']);bad[key]={'unexpected':True}
            with self.subTest(key=key),self.assertRaises(ValueError):m.validate_view(bad,self.base)
    def test_old_controller_sources_still_byte_identical(self):m.legacy()
    def test_xml_stages_explicit_unbound_forward_then_runtime_model_is_no(self):
        import xml.etree.ElementTree as ET
        docs=m.documents(self.base);zone=ET.fromstring(docs['zones/'+m.ZONE+'.xml'])
        self.assertIsNone(zone.find('interface'));self.assertIsNotNone(zone.find('forward'));self.assertIsNone(zone.find('source'))
        self.assertEqual(m.expected_objects(self.base)['zones'][m.ZONE]['fields']['forward'],'no')
        self.assertEqual(sorted(x.attrib['name'] for x in zone.findall('service')),['dhcpv6-client','ssh'])
        self.assertEqual(len(zone.findall('rule')),7)
        local=ET.fromstring(docs['policies/'+m.LOCAL+'.xml']);r=local.find('rule')
        self.assertEqual(r.find('source').attrib['address'],m.BLOCK);self.assertEqual(r.find('destination').attrib['address'],m.BLOCK)
        self.assertIsNone(r.find('port'))
    def test_transient_forward_yes_is_recovery_only_and_exact(self):
        view=copy.deepcopy(self.base);zone=copy.deepcopy(m.expected_objects(self.base)['zones'][m.ZONE])
        zone['fields']['forward']='yes';view['objects']['permanent']['zones'][m.ZONE]=zone
        with self.assertRaises(ValueError):m.validate_view(view,self.base)
        self.assertFalse(m.validate_view(view,self.base,allow_missing_runtime=True,
                                         allow_transient_permanent_forward=True))
        view['objects']['permanent']['zones'][m.ZONE]['fields']['target']='ACCEPT'
        with self.assertRaises(ValueError):
            m.validate_view(view,self.base,allow_missing_runtime=True,
                            allow_transient_permanent_forward=True)


class PreservedPolicies(unittest.TestCase):
    def setUp(self):
        self.base,self.original=baseline()
        self.policy_state=copy.deepcopy(self.base['firewall']['active_policies'])
    def assert_rejected_before_arm(self,state):
        base,original=baseline(state);io=FakeIO(base)
        with self.assertRaises(ValueError):m.apply(io,MemoryJournal(),original,authorization(base),BUNDLE)
        self.assertEqual(io.actions,[])
    def test_observed_firewall_projection_is_used_by_full_transaction_tests(self):
        observed=json.loads((Path(__file__).parent/'fixtures/CP1_FCJv61h8_FIREWALL.json').read_text())
        self.assertEqual({k:self.base['firewall'][k] for k in observed['firewall']},observed['firewall'])
        self.assertEqual(set(self.base['objects']['runtime']['policies']),m.PRESERVED_DISABLED_POLICIES|{'allow-host-ipv6'})
        m.validate_baseline(self.base,self.original)
    def test_each_gateway_enabled_in_both_planes_is_rejected_before_arm(self):
        for name in m.PRESERVED_DISABLED_POLICIES:
            state=copy.deepcopy(self.policy_state)
            for plane in ('runtime','permanent'):
                state['configurations'][plane][name]=state['configurations'][plane][name].replace('disable: yes','disable: no').replace(' (disabled)','')
            with self.subTest(name=name):self.assert_rejected_before_arm(state)
    def test_missing_disable_flag_in_both_planes_is_rejected(self):
        state=copy.deepcopy(self.policy_state)
        for plane in ('runtime','permanent'):
            name='gateway-lan-to-world';state['configurations'][plane][name]=state['configurations'][plane][name].replace('  disable: yes\n','')
        self.assert_rejected_before_arm(state)
    def test_unknown_disabled_policy_cannot_be_added_to_receipt_to_bypass_review(self):
        state=copy.deepcopy(self.policy_state)
        for plane in ('runtime','permanent'):
            state['configurations'][plane]['gateway-extra']=state['configurations'][plane]['gateway-lan-to-world'].replace('gateway-lan-to-world','gateway-extra')
        self.assert_rejected_before_arm(state)
    def test_known_disabled_policy_marked_active_is_rejected(self):
        state=copy.deepcopy(self.policy_state)
        state['active_output']+='\ngateway-lan-to-world\n  ingress-zones: internal home trusted\n  egress-zones: external public'
        self.assert_rejected_before_arm(state)
    def test_runtime_disabled_header_must_agree_with_flag(self):
        state=copy.deepcopy(self.policy_state);name='gateway-lan-to-world'
        state['configurations']['runtime'][name]=state['configurations']['runtime'][name].replace(' (disabled)',' (active)')
        self.assert_rejected_before_arm(state)
    def test_policy_inventory_must_match_raw_receipt(self):
        base=copy.deepcopy(self.base)
        # Both semantic planes remain equal, but the raw evidence differs.
        for p in ('runtime','permanent'):base['objects'][p]['policies']['gateway-lan-to-world']['fields']['target']='DROP'
        with self.assertRaisesRegex(ValueError,'policy inventory/receipt mismatch'):
            m.validate_baseline(base,self.original)
    def test_runtime_permanent_disabled_definition_delta_is_rejected(self):
        state=copy.deepcopy(self.policy_state);name='gateway-lan-to-world'
        state['configurations']['runtime'][name]=state['configurations']['runtime'][name].replace('priority: 1000','priority: 999')
        self.assert_rejected_before_arm(state)
    def test_original_receipt_equality_remains_required(self):
        base=copy.deepcopy(self.base)
        for plane in ('runtime','permanent'):
            base['firewall']['active_policies']['configurations'][plane]['gateway-lan-to-world']+=' '
        with self.assertRaisesRegex(ValueError,'CP1 differs from original receipt'):m.validate_baseline(base,self.original)
    def test_full_comparison_keeps_each_disabled_policy_in_each_plane(self):
        io=FakeIO(self.base);record=m.apply(io,MemoryJournal(),self.original,authorization(self.base),BUNDLE)
        for name in m.PRESERVED_DISABLED_POLICIES:
            for plane in ('runtime','permanent'):
                bad=copy.deepcopy(record['after']);bad['objects'][plane]['policies'][name]['fields']['disable']='no'
                with self.subTest(name=name,plane=plane),self.assertRaises(ValueError):m.validate_view(bad,self.base)
    def test_changed_disabled_policy_blocks_blind_reload_during_recovery(self):
        io=FakeIO(self.base);journal=MemoryJournal();record=m.apply(io,journal,self.original,authorization(self.base),BUNDLE)
        io.view['objects']['runtime']['policies']['gateway-lan-to-world']['fields']['target']='DROP';offset=len(io.actions)
        with self.assertRaises(ValueError):m.recover(io,journal,record)
        self.assertEqual(io.actions[offset:],['bind:False']);self.assertTrue(io.armed)
        self.assertEqual(io.view['objects']['runtime']['policies']['gateway-lan-to-world']['fields']['target'],'DROP')


class InspectionDiagnostics(unittest.TestCase):
    def setUp(self):self.base,self.original=baseline()
    def inspect(self,base):
        io=FakeIO(base);output=streams.StringIO()
        with contextlib.redirect_stdout(output):
            try:e.inspect_document(io,self.original,BUNDLE)
            except ValueError:pass
        self.assertEqual(io.actions,[])
        return json.loads(output.getvalue())
    def test_successful_read_only_inspection_preserves_contract(self):
        doc=self.inspect(self.base)
        self.assertEqual(doc['schema'],'fds.cp1-minimal-inspection.v1')
        self.assertEqual(doc['before_sha256'],m.fingerprint(doc['before']))
        self.assertFalse(doc['operational_approval'])
    def test_rejected_inspection_keeps_full_snapshot_and_error_separate_from_success(self):
        base=copy.deepcopy(self.base);base['version']='unsupported'
        doc=self.inspect(base)
        self.assertEqual(doc['schema'],'fds.cp1-minimal-inspect-diagnostic.v1')
        self.assertEqual(doc['status'],'STOP_REVIEW_REQUIRED');self.assertEqual(doc['before'],base)
        self.assertIn('unsupported',doc['error']);self.assertFalse(doc['mutation_attempted'])
        self.assertFalse(doc['operational_approval'])
    def test_incomplete_snapshot_raises_without_claiming_full_inspection(self):
        io=FakeIO(self.base);output=streams.StringIO()
        with patch.object(io,'snapshot',side_effect=ValueError('query failed')),contextlib.redirect_stdout(output),self.assertRaisesRegex(ValueError,'query failed'):
            e.inspect_document(io,self.original,BUNDLE)
        self.assertEqual(output.getvalue(),'');self.assertEqual(io.actions,[])
    def test_rejected_diagnostic_cannot_be_consumed_by_apply(self):
        base=copy.deepcopy(self.base);base['version']='unsupported';doc=self.inspect(base)
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            with patch.object(e,'BASE',directory),patch.object(e,'private',return_value=directory),patch.object(e.os,'geteuid',return_value=0), \
                 patch.object(e,'load_checked',side_effect=[self.original,doc,authorization(base)]), \
                 patch.object(e,'IO') as io,patch('sys.argv',['run-cp1-minimal-change.py','apply']), \
                 self.assertRaisesRegex(ValueError,'inspection binding differs'):
                e.main()
            io.assert_not_called()


class Receipts(unittest.TestCase):
    setUp = Transactions.setUp
    apply = Transactions.apply
    def evidence(self):
        record=self.apply();request,wrapper=fixture()
        wrapper['result']['firewall_before']=copy.deepcopy(record['after']['firewall'])
        wrapper['result']['firewall_after']=copy.deepcopy(record['after']['firewall'])
        request['kit_hashes']['expected-firewall.json']=a.sha(a.encoded(record['after']['firewall']))
        wrapper['kit_hashes']=copy.deepcopy(request['kit_hashes'])
        return record,request,wrapper
    def test_receipt_recomputes_raw_five_flows_and_does_not_accept(self):
        record,request,wrapper=self.evidence();clock=CLOCK+dt.timedelta(minutes=2)
        h=m.receipt(record,self.original,request,wrapper,BUNDLE,clock)
        self.assertFalse(h['cp1_accepted']);self.assertFalse(h['authorizes_cp2_change'])
        m.validate_handoff(h,self.original,request,wrapper,BUNDLE,clock)
    def test_forged_summary_does_not_make_handoff(self):
        record,request,wrapper=self.evidence();wrapper['result']['queries'][2]['stdout']='{}'
        with self.assertRaisesRegex(ValueError,'five-flow'):m.receipt(record,self.original,request,wrapper,BUNDLE,CLOCK+dt.timedelta(minutes=2))
    def test_old_after_state_cannot_be_used_for_new_trial(self):
        record,request,wrapper=self.evidence();wrapper['result']['firewall_before']=self.base['firewall']
        with self.assertRaises(ValueError):m.receipt(record,self.original,request,wrapper,BUNDLE,CLOCK+dt.timedelta(minutes=2))
    def test_handoff_body_edited_and_rehashed_is_rejected(self):
        record,request,wrapper=self.evidence();h=m.receipt(record,self.original,request,wrapper,BUNDLE,CLOCK+dt.timedelta(minutes=2))
        h['authorizes_cp2_change']=True
        with self.assertRaises(ValueError):m.validate_handoff(h,self.original,request,wrapper,BUNDLE,CLOCK+dt.timedelta(minutes=2))
    def test_acceptance_requires_new_schema_receipt_source_tests_and_lifetime(self):
        record,request,wrapper=self.evidence();clock=CLOCK+dt.timedelta(minutes=2)
        h=m.receipt(record,self.original,request,wrapper,BUNDLE,clock)
        accept=dict(schema='fds.cp1-minimal-acceptance.v1',decision='ACCEPTED',independent_reviewer='hayeounglee',
                    review_decision='APPROVED',review_url=self.auth['review_url'],reviewed_at=clock.isoformat(),
                    expires_at=record['withdraw_at'],handoff_sha256=m.fingerprint(h),source_bundle_sha256=BUNDLE,
                    checks={x:{'status':'PASS','evidence_sha256':'a'*64} for x in ('positive','negative','regression')})
        m.validate_acceptance(accept,h,clock)
        for key,val in (('schema','fds.cp1-runtime-acceptance.v1'),('decision','PENDING'),('handoff_sha256','f'*64),
                        ('source_bundle_sha256','f'*64),('independent_reviewer','GWONUK'),('expires_at',self.auth['window_end']),('checks',{})):
            with self.subTest(key=key),self.assertRaises(ValueError):m.validate_acceptance(dict(accept,**{key:val}),h,clock)
        with self.assertRaises(ValueError):m.validate_acceptance(accept,h,m.instant(record['withdraw_at']))
    def test_live_gate_rejects_expiry_disarmed_other_job_or_changed_own_set(self):
        record=self.apply();status=dict(status=record['status'],job_id=record['job_id'],source_bundle_sha256=BUNDLE,
                                       current=record['after'],recovery_armed=True,change_receipt_sha256=m.fingerprint(record),withdraw_at=record['withdraw_at'])
        m.live_gate(status,record,900,CLOCK)
        for key,val in (('job_id','2'*16),('source_bundle_sha256','f'*64),('recovery_armed',False),('current',self.base)):
            with self.subTest(key=key),self.assertRaises(ValueError):m.live_gate(dict(status,**{key:val}),record,0,CLOCK)
        with self.assertRaises(ValueError):m.live_gate(status,record,0,m.instant(record['withdraw_at']))
    def test_live_after_gate_checks_entire_new_state(self):
        record=self.apply();stable={'firewall':record['after']['firewall'],'protection':record['after']['protection']}
        ctl=m.legacy();state={'stable':stable,'sha256':ctl.node.fingerprint(stable)}
        status=dict(status=record['status'],job_id=record['job_id'],source_bundle_sha256=BUNDLE,current=record['after'],recovery_armed=True,change_receipt_sha256=m.fingerprint(record),withdraw_at=record['withdraw_at'])
        with patch.object(c,'remote_status',return_value=status),patch.object(c.m,'now',return_value=CLOCK):
            c.before_or_after(ctl,state,state['sha256'],record)
            status['current']=copy.deepcopy(status['current']);status['current']['objects']['runtime']['ipsets'][m.SETS[0]]['fields']['entries']='0.0.0.0/0,ens160'
            with self.assertRaises(ValueError):c.before_or_after(ctl,state,state['sha256'],record)


class LocalIO(unittest.TestCase):
    def test_default_plan_has_no_host_commands(self):
        proc=subprocess.run(['python3','-I','-B',str(e.HERE/'run-cp1-minimal-change.py')],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr);self.assertEqual(json.loads(proc.stdout)['mode'],'PLAN_ONLY')
    def test_input_symlink_digest_and_duplicate_json_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'data';file.write_bytes(b'{"x":1,"x":2}')
            with self.assertRaises(ValueError):e.load_checked(file,m.digest(file.read_bytes()))
            file.write_bytes(b'{}');link=Path(directory)/'link';link.symlink_to(file)
            with self.assertRaises(OSError):e.load_checked(link,m.digest(b'{}'))
            with self.assertRaises(ValueError):e.load_checked(file,'a'*64)
    def test_policy_singular_cli_and_runtime_binding(self):
        io=object.__new__(e.IO)
        with patch.object(io,'command') as cmd:
            io.remove_permanent('policies',m.LOCAL);self.assertIn('--delete-policy='+m.LOCAL,cmd.call_args.args[0])
            io.bind(True);self.assertNotIn('--permanent',cmd.call_args.args[0]);self.assertIn('--add-interface=cali+',cmd.call_args.args[0])
    def test_zone_stage_explicitly_disables_forward_before_return(self):
        io=object.__new__(e.IO);io.directory=Path('/reviewed-job')
        with patch.object(e,'private',return_value=Path('/reviewed-job/xml/zones')), \
             patch.object(e,'atomic'),patch.object(io,'command') as cmd:
            io.stage('zones',m.ZONE,'<zone><forward/></zone>\n')
        calls=[call.args[0] for call in cmd.call_args_list]
        self.assertEqual(len(calls),2)
        self.assertIn('--new-zone-from-file=/reviewed-job/xml/zones/'+m.ZONE+'.xml',calls[0])
        self.assertEqual(calls[1],['firewall-cmd','--permanent','--zone='+m.ZONE,'--remove-forward'])
    def test_parser_rejects_duplicate_unknown_lines_and_canonicalizes_active_header(self):
        obj={'fields':{'services':'dhcpv6-client ssh','interfaces':'cali+'},'rules':['rule family=ipv4 accept']}
        self.assertEqual(m.parse_info(text('demo (active)',obj),'demo'),obj)
        for raw in ('demo\n  services: ssh\n  services: http','other\n  target: ACCEPT','demo\n  unknown'):
            with self.assertRaises(ValueError):m.parse_info(raw,'demo')


class AdapterWorkflow(unittest.TestCase):
    def exercise(self, failure=None, auth_factory=authorization, io_factory=FakeIO):
        import contextlib, io, os
        from test_node_firewall import Gates
        adapter=load('adapter_integration','scripts/run-node-firewall-cni.py');n=adapter.node
        base,original=baseline();fake=io_factory(base);journal=MemoryJournal()
        record=m.apply(fake,journal,original,auth_factory(base),BUNDLE)
        req,wrapper=fixture();wrapper['result']['firewall_before']=copy.deepcopy(record['after']['firewall'])
        wrapper['result']['firewall_after']=copy.deepcopy(record['after']['firewall'])
        req['kit_hashes']['expected-firewall.json']=a.sha(a.encoded(record['after']['firewall']));wrapper['kit_hashes']=copy.deepcopy(req['kit_hashes'])
        clock=CLOCK+dt.timedelta(minutes=2);handoff=m.receipt(record,original,req,wrapper,BUNDLE,clock)
        accept=dict(schema='fds.cp1-minimal-acceptance.v1',decision='ACCEPTED',independent_reviewer='hayeounglee',
            review_decision='APPROVED',review_url=authorization(base)['review_url'],reviewed_at=clock.isoformat(),
            expires_at=record['withdraw_at'],handoff_sha256=m.fingerprint(handoff),source_bundle_sha256=BUNDLE,
            checks={k:dict(status='PASS',evidence_sha256='a'*64) for k in ('positive','negative','regression')})
        if record['schema']=='fds.cp1-minimal-change.v2':
            accept.update(schema='fds.cp1-minimal-acceptance.v2',retention_approved=True,
                          expires_at=record['retained_withdraw_at'])
        target='k8s-control02';backend=FakeBackend(MemoryJournal(),target);before=backend.snapshot();topology=Gates().topology()
        stable={'firewall':record['after']['firewall'],'protection':record['after']['protection']}
        state={'stable':stable,'sha256':n.fingerprint(stable),'status':'CP1_STATE_REVIEW_REQUIRED'}
        inspection=dict(schema='fds.node-firewall-input.v2',phase='runtime',hostname=target,created_at=n.now(),
            source_commit='a'*40,source_sha256=adapter.digest(adapter.NODE_SOURCE),plan_sha256=n.plan_hash(target),policy_sha256=n.policy_hash(),
            snapshot=before,before_sha256=n.fingerprint(before),residual_metadata_sha256=n.fingerprint(before['residual_metadata']),
            topology=topology['stable'],topology_sha256=topology['sha256'],cp1_state=stable,cp1_state_sha256=state['sha256'])
        calls=[];live_calls=[]
        def remote(name,request):
            mode=request['mode'];calls.append((name,mode))
            if mode=='topology': data=dict(topology,status='TOPOLOGY_REVIEW_REQUIRED')
            elif mode=='cp1-state':data=state
            elif mode=='apply':
                proof=n.apply_transaction(backend,backend.journal,request)
                data=dict(status=proof['status'],receipt=proof,receipt_sha256=n.fingerprint(proof))
            elif mode=='inspect':data=dict(status='INSPECTED_REVIEW_REQUIRED',snapshot=backend.snapshot())
            else:raise AssertionError(mode)
            return {'transport':'PASS','result':data}
        def live(ctl,rec):
            live_calls.append(rec['job_id'])
            result=dict(status=rec['status'],job_id=rec['job_id'],source_bundle_sha256=BUNDLE,current=copy.deepcopy(rec['after']),
                        recovery_armed=True,change_receipt_sha256=m.fingerprint(rec),withdraw_at=rec['withdraw_at'])
            if rec['schema']=='fds.cp1-minimal-change.v2':
                result.update(status='ACCEPTED_RETAINED',withdraw_at=rec['retained_withdraw_at'],
                              acceptance_sha256=m.fingerprint(accept),handoff_sha256=m.fingerprint(handoff))
                if failure=='retention-proof':result['acceptance_sha256']='f'*64
            if failure=='before' or failure=='after' and len(live_calls)==2:result['recovery_armed']=False
            return result
        old=Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory);args=['run-node-firewall-cni.py','apply','--node',target]
            def save(name,obj):
                raw=n.encoded(obj);path=directory/(name+'.json');path.write_bytes(raw)
                args.extend(['--'+name,str(path),'--'+name+'-sha256',m.digest(raw)]);return m.digest(raw)
            inspectsha=save('inspection',inspection);originalsha=save('cp1-receipt',original)
            if failure=='acceptance':accept['decision']='PENDING'
            acceptsha=save('cp1-acceptance',accept);save('cp1-handoff',handoff)
            archive=directory/'trial.tar.gz';raw=pack(req,wrapper);archive.write_bytes(raw)
            args.extend(['--cp1-trial',str(archive),'--cp1-trial-sha256',m.digest(raw)])
            t=dt.datetime.now(dt.timezone.utc)
            auth=dict(schema='fds.node-firewall-authorization.v2',phase='runtime',source_commit='a'*40,target=target,mode='apply',
                operator='GWONUK',independent_reviewer='hayeounglee',review_url='https://github.com/321Team/infra-kubernetes/pull/4#issuecomment-123',
                review_decision='APPROVED',window_start=(t-dt.timedelta(minutes=1)).isoformat(),window_end=(t+dt.timedelta(minutes=40)).isoformat(),
                inspection_sha256=inspectsha,cp1_canary_accepted=True,cp1_receipt_input_sha256=originalsha,cp1_acceptance_sha256=acceptsha,
                zone_controls={p:{'forward':'yes','masquerade':'no'} for p in ('runtime','permanent')})
            if failure=='target-window':auth['window_end']=record['retained_withdraw_at']
            for key in ('source_sha256','plan_sha256','policy_sha256','before_sha256','residual_metadata_sha256','cp1_state_sha256'):auth[key]=inspection[key]
            for key in ('maintenance_freeze','console_recovery_available','backup_verified','protection_reviewed','rollback_runbook_reviewed','runtime_only_acknowledged','target_state_reviewed'):auth[key]=True
            save('authorization',auth)
            try:
                os.chdir(directory)
                with patch('sys.argv',args),patch.object(adapter,'verify_source',return_value='a'*40), \
                     patch.object(adapter,'preflight',return_value={}),patch.object(adapter,'ControllerLock'), \
                     patch.object(adapter,'remote_request',side_effect=remote),patch.object(adapter.cni,'remote_status',side_effect=live), \
                     patch.object(adapter.cni.m,'now',return_value=clock),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as out:
                    adapter.main()
                result=json.loads(next(directory.glob('FDS_Node_Firewall_*/RESULT.json')).read_bytes())
            finally:os.chdir(old)
        return out.exception.code,result,calls,live_calls
    def test_real_adapter_flow_requires_new_acceptance_and_both_live_state_checks(self):
        rc,result,calls,live=self.exercise()
        self.assertEqual(rc,0,result);self.assertEqual(len(live),2)
        self.assertEqual([v for v in calls if v[1]=='apply'],[('k8s-control02','apply')])
    def test_bad_new_acceptance_blocks_target_before_any_apply(self):
        rc,result,calls,live=self.exercise('acceptance');self.assertEqual(rc,2,result)
        self.assertFalse(any(mode=='apply' for _,mode in calls));self.assertFalse(live)
    def test_before_disarm_blocks_target_and_after_disarm_blocks_success(self):
        for when in ('before','after'):
            with self.subTest(when=when):
                rc,result,calls,live=self.exercise(when);self.assertEqual(rc,2,result)
                self.assertEqual(sum(mode=='apply' for _,mode in calls),0 if when=='before' else 1)
                self.assertEqual(result['status'],'STOP_REVIEW_REQUIRED')


class EntryBoundary(unittest.TestCase):
    def test_independent_timer_uses_frozen_job_and_is_verified_before_mutation(self):
        from types import SimpleNamespace
        io=object.__new__(e.IO);io.job_id='a'*16;io.unit='fds-cp1-minimal-'+io.job_id
        io.directory=Path('/var/lib/fds/cp1-minimal')/io.job_id
        with patch.object(io,'now',return_value=CLOCK),patch.object(io,'command') as command,patch.object(io,'timer_active',return_value=True):
            io.arm((CLOCK+dt.timedelta(seconds=900)).isoformat());argv=command.call_args.args[0]
            self.assertIn('--on-active=900s',argv);self.assertIn('--property=TimeoutStartSec=180s',argv)
            self.assertEqual(argv[-3:],['recover','--job',io.job_id]);self.assertNotIn('enable',argv)
        with patch.object(io,'now',return_value=CLOCK),patch.object(io,'command'),patch.object(io,'timer_active',return_value=False),self.assertRaises(ValueError):
            io.arm((CLOCK+dt.timedelta(seconds=900)).isoformat())
    def test_reload_restores_only_missing_known_public_rules(self):
        base,_=baseline();io=object.__new__(e.IO);io.n=m.legacy().node
        public=copy.deepcopy(base['firewall']['planes']['runtime']);public['rules']=public['rules'][:1]
        responses=['','', 'public',text('public',public)]+['']*6
        with patch.object(io,'command',side_effect=responses) as cmd:
            io.reload_restore(base)
            adds=[call.args[0][-1] for call in cmd.call_args_list if call.args[0][-1].startswith('--add-rich-rule=')]
            self.assertEqual(len(adds),6);self.assertFalse(any('--permanent' in call.args[0] for call in cmd.call_args_list))
        public['rules'].append('rule family=ipv4 accept')
        with patch.object(io,'command',side_effect=['','', 'public',text('public',public)]),self.assertRaises(ValueError):io.reload_restore(base)
    def test_mutation_warning_and_truncated_response_are_never_success(self):
        from types import SimpleNamespace
        io=object.__new__(e.IO);io.directory=None
        for mutation in ({'rc':1},{'stderr':'ALREADY_ENABLED'},{'stdout_truncated':True},{'rc':124}):
            result=dict(rc=0,stdout='success',stderr='');result.update(mutation)
            io.n=SimpleNamespace(command=lambda *a,**k:result)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):io.command(['firewall-cmd','--reload'])
    def test_source_freeze_includes_analyzer_and_immutable_validators(self):
        files=e.source_files()
        self.assertEqual(set(files),set(e.FILES));self.assertEqual(files['node-firewall.py'],m.PINS['node-firewall.py'])
    def test_handoff_cannot_be_issued_before_raw_trial_ends(self):
        test=Receipts();test.setUp();record,request,wrapper=test.evidence()
        with self.assertRaisesRegex(ValueError,'predate'):m.receipt(record,test.original,request,wrapper,BUNDLE,CLOCK)

    def test_semantically_passing_peer_outside_reviewed_block_cannot_issue_receipt(self):
        test=Receipts();test.setUp();record,request,wrapper=test.evidence()
        wrapper=json.loads(json.dumps(wrapper).replace('10.244.246.80','10.244.246.128'))
        self.assertEqual(a.analyze(request,wrapper)['passed_flows'],5)
        with self.assertRaisesRegex(ValueError,'outside reviewed /26'):
            m.receipt(record,test.original,request,wrapper,BUNDLE,CLOCK+dt.timedelta(minutes=2))
