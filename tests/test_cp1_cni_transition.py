"""Command-model crash/recovery tests. No target changes or systemd execution."""
import copy
import unittest
from test_cp1_cni_membership import lease, MemoryJournal, Crash, m
import cp1_cni_transition as t

OK={'rc':0,'stdout':'success\n','stderr':'','truncated':False}


def baseline():
    zone={'target':'default','interfaces':'ens160','sources':'','services':'ssh',
          'rich rules':['rule family=ipv4 source address=10.1.93.109/32 port port=6443 protocol=tcp accept']}
    plane={'zones':{'public':zone},'ipsets':{},'policies':{},'direct':''}
    value={'inventory':{p:copy.deepcopy(plane) for p in ('runtime','permanent')},'descriptions':{},'fixed_sha256':'a'*64}
    value['inventory']['permanent']['zones']['public']['interfaces']=''
    value['inventory']['runtime']['zones']['public']['rich rules']=sorted(zone['rich rules']+t.six_rules())
    return value


class IO:
    def __init__(self,journal):
        self.journal=journal;self.live=baseline();self.lease=lease()
        self.lease['policy_sha256']=t.fingerprint(t.documents(self.lease['run_id']))
        self.now={'boot_id':self.lease['boot_id'],'wall':1000,'boottime':110}
        self.calls=[];self.failure=None;self.fail_at=None;self.guard_alive=True;self.external_ok=True
        self.result=copy.deepcopy(OK);self.disable_attempts=0;self.stopped=True

    def snapshot(self):return copy.deepcopy(self.live)
    def clock(self):return copy.deepcopy(self.now)
    def guard(self,cleanup):
        if not cleanup:
            m.remaining(self.lease,self.clock(),m.RESERVE);m.need(self.guard_alive,'guard not armed')
        else:m.need(self.stopped,'worker cgroup still populated')
    def management(self):m.need(self.external_ok,'fresh management proof absent')
    def command(self,args,*,cleanup=False):
        intent=self.journal.saved['pending'];assert intent['argv']==args
        assert intent['cleanup'] is cleanup and intent['before']==self.live
        index=len(self.calls);self.calls.append(args[:])
        fail=(index==self.fail_at)
        if fail and self.failure=='before':raise Crash()
        r,p=self.live['inventory']['runtime'],self.live['inventory']['permanent']
        # Independent command semantics; never assign journal.pending.after.
        if args==['--reload']:
            self.live['inventory']['runtime']=copy.deepcopy(p)
            self.live['inventory']['runtime']['zones']['public']['interfaces']='ens160'
        elif args[0]=='--permanent':
            op=args[1]
            if op.startswith('--new-ipset-from-file='):
                m.need(t.NAME not in p['ipsets'],'existing set');p['ipsets'][t.NAME]=t.set_definition()
                self.live['descriptions']['ipset']=self.lease['run_id']
            elif op.startswith('--new-policy-from-file='):
                m.need(t.NAME not in p['policies'],'existing policy');p['policies'][t.NAME]=t.policy()
                self.live['descriptions']['policy']=self.lease['run_id']
            elif op=='--delete-policy='+t.NAME:
                del p['policies'][t.NAME];del self.live['descriptions']['policy']
            elif op=='--delete-ipset='+t.NAME:
                del p['ipsets'][t.NAME];del self.live['descriptions']['ipset']
            else:raise AssertionError(args)
        elif args[0]=='--zone=public':
            prefix='--add-rich-rule=';assert args[1].startswith(prefix)
            rule=args[1][len(prefix):];assert rule in t.six_rules()
            r['zones']['public']['rich rules']=sorted(set(r['zones']['public']['rich rules'])|{rule})
        elif args[0]=='--policy='+t.NAME:
            assert args[1] in ('--remove-disable','--add-disable')
            r['policies'][t.NAME]['disable']='no' if args[1]=='--remove-disable' else 'yes'
        else:raise AssertionError(args)
        if fail and self.failure=='after':raise Crash()
        if fail and self.failure=='timeout':raise TimeoutError('unknown completion')
        if fail and self.failure=='drift':r['zones']['public']['services']+=' cockpit'
        return copy.deepcopy(self.result)

    def emergency_disable(self):
        self.disable_attempts+=1
        r=self.live['inventory']['runtime'];p=self.live['inventory']['permanent']
        if t.NAME not in r['policies']:return
        # Model of separately observed exact ownership, never blind name match.
        own=r['policies'][t.NAME]
        t.policy_definition(own,own['disable']=='yes')
        m.need(self.live['descriptions'].get('policy')==self.lease['run_id'] and
               self.live['descriptions'].get('ipset')==self.lease['run_id'] and
               p['policies'].get(t.NAME)==t.policy(), 'narrow ownership unavailable')
        own['disable']='yes'


def setup():
    journal=MemoryJournal();io=IO(journal);state=t.initial(io.lease,io.live);journal.save(state)
    return t.Transaction(state,io,journal),io,journal


class TransitionTests(unittest.TestCase):
    def test_empty_activation_restores_six_and_permanent_stays_disabled_empty(self):
        tx,io,j=setup();state=tx.activate_empty();r=io.live['inventory']['runtime'];p=io.live['inventory']['permanent']
        self.assertEqual(state['phase'],'EMPTY_ACTIVE');self.assertEqual(len(io.calls),10)
        self.assertEqual(r['ipsets'][t.NAME]['entries'],'');self.assertEqual(p['policies'][t.NAME],t.policy())
        self.assertEqual(r['policies'][t.NAME],t.policy(False))
        self.assertTrue(set(t.six_rules())<=set(r['zones']['public']['rich rules']))
        self.assertFalse(state['authorizes_apply']);self.assertFalse(state['full_recovery_complete'])

    def test_full_firewall_roundtrip_preserves_original_but_not_claim_management_units_done(self):
        tx,io,j=setup();before=io.snapshot();tx.activate_empty();state=tx.recover()
        self.assertEqual(io.snapshot(),before);self.assertTrue(state['firewall_restored'])
        self.assertFalse(state['full_recovery_complete']);self.assertEqual(state['phase'],'FIREWALL_RESTORED')
        self.assertFalse(any('runtime-to-permanent' in ' '.join(a) for a in io.calls))

    def test_power_loss_before_or_after_each_activation_command_recovers(self):
        for step in range(10):
            for failure in ('before','after'):
                with self.subTest(step=step,failure=failure):
                    tx,io,j=setup();io.fail_at=step;io.failure=failure
                    with self.assertRaises(Crash):tx.activate_empty()
                    self.assertIsNotNone(j.saved['pending']);io.fail_at=None
                    tx=t.Transaction(j.saved,io,j);state=tx.recover()
                    self.assertTrue(state['firewall_restored']);self.assertEqual(io.snapshot(),baseline())

    def test_reboot_after_each_activation_prefix_restores_six_without_new_grant(self):
        for step in range(10):
            with self.subTest(step=step):
                tx,io,j=setup();io.fail_at=step;io.failure='after'
                with self.assertRaises(Crash):tx.activate_empty()
                io.live=t.reloaded(io.live);io.now['boot_id']='a'*8+io.now['boot_id'][8:];io.now['boottime']=5
                io.fail_at=None;old=len(io.calls)
                state=t.Transaction(j.saved,io,j).recover()
                self.assertTrue(state['firewall_restored']);self.assertEqual(io.snapshot(),baseline())
                self.assertFalse(any('--remove-disable' in a for a in io.calls[old:]))

    def test_power_loss_at_every_recovery_command_resumes_known_prefix(self):
        ref,io,j=setup();ref.activate_empty();n=len(io.calls);ref.recover();count=len(io.calls)-n
        self.assertEqual(count,10)
        for step in range(count):
            for failure in ('before','after'):
                with self.subTest(step=step,failure=failure):
                    tx,io,j=setup();tx.activate_empty();io.fail_at=len(io.calls)+step;io.failure=failure
                    with self.assertRaises(Crash):tx.recover()
                    io.fail_at=None;state=t.Transaction(j.saved,io,j).recover()
                    self.assertEqual(io.snapshot(),baseline());self.assertTrue(state['firewall_restored'])

    def test_reboot_at_every_recovery_command_does_not_adopt_unknown_state(self):
        for step in range(10):
            with self.subTest(step=step):
                tx,io,j=setup();tx.activate_empty();io.fail_at=len(io.calls)+step;io.failure='after'
                with self.assertRaises(Crash):tx.recover()
                io.live=t.reloaded(io.live);io.now['boot_id']='a'*8+io.now['boot_id'][8:];io.now['boottime']=5
                io.fail_at=None;state=t.Transaction(j.saved,io,j).recover()
                self.assertEqual(io.snapshot(),baseline());self.assertTrue(state['firewall_restored'])

    def test_foreign_runtime_or_permanent_drift_only_disables_owned_grant(self):
        for plane in ('runtime','permanent'):
            tx,io,j=setup();tx.activate_empty();n=len(io.calls)
            io.live['inventory'][plane]['zones']['public']['services']+=' cockpit'
            with self.subTest(plane=plane),self.assertRaises(ValueError):tx.recover()
            self.assertEqual(len(io.calls),n);self.assertIn('cockpit',io.live['inventory'][plane]['zones']['public']['services'])
            self.assertEqual(io.live['inventory']['runtime']['policies'][t.NAME]['disable'],'yes')
            self.assertEqual(io.disable_attempts,1);self.assertFalse(j.saved['firewall_restored'])

    def test_unknown_native_or_protection_hash_stops_reload(self):
        tx,io,j=setup();tx.activate_empty();n=len(io.calls);io.live['fixed_sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'foreign drift'):tx.recover()
        self.assertEqual(len(io.calls),n);self.assertEqual(io.disable_attempts,1)

    def test_foreign_owner_is_not_disabled_or_deleted(self):
        tx,io,j=setup();tx.activate_empty();io.live['descriptions']['policy']='foreign';before=io.snapshot();n=len(io.calls)
        with self.assertRaisesRegex(ValueError,'ownership'):tx.recover()
        self.assertEqual(io.live,before);self.assertEqual(len(io.calls),n)

    def test_api_failure_blocks_broad_recovery_but_attempts_narrow_disable(self):
        tx,io,j=setup();tx.activate_empty();n=len(io.calls)
        io.snapshot=lambda:(_ for _ in ()).throw(TimeoutError('API missing'))
        with self.assertRaises(TimeoutError):tx.recover()
        self.assertEqual(len(io.calls),n);self.assertEqual(io.disable_attempts,1)
        self.assertEqual(io.live['inventory']['runtime']['policies'][t.NAME]['disable'],'yes')

    def test_unknown_addition_of_runtime_element_never_becomes_owned_history(self):
        tx,io,j=setup();tx.activate_empty();io.live['inventory']['runtime']['ipsets'][t.NAME]['entries']='10.244.246.80/32,cali00000000000'
        n=len(io.calls)
        with self.assertRaises(ValueError):tx.recover()
        self.assertEqual(len(io.calls),n);self.assertEqual(io.disable_attempts,1)

    def test_missing_membership_journal_still_attempts_disable(self):
        tx,io,j=setup();tx.activate_empty();tx.handoff_membership();n=len(io.calls)
        with self.assertRaisesRegex(ValueError,'membership journal'):tx.recover()
        self.assertEqual(len(io.calls),n);self.assertEqual(io.disable_attempts,1)

    def test_linked_membership_pending_before_or_after_is_recoverable(self):
        for after in (False,True):
            tx,io,j=setup();tx.activate_empty();tx.handoff_membership()
            from test_cp1_cni_membership import setup as membership_setup,member
            manager,mi,mj=membership_setup();state=manager.state;state['lease']=copy.deepcopy(io.lease)
            item=m.entry(member());state['pending']={'before':[],'after':[item]}
            io.live['inventory']['runtime']['ipsets'][t.NAME]['entries']=item if after else ''
            recovered=tx.recover(membership_state=state)
            self.assertEqual(io.snapshot(),baseline());self.assertTrue(recovered['firewall_restored'])

    def test_expiry_cannot_activate_but_does_not_prevent_recovery(self):
        tx,io,j=setup();io.now['wall']=io.lease['wall_end']
        with self.assertRaisesRegex(ValueError,'exhausted'):tx.activate_empty()
        self.assertEqual(io.calls,[]);self.assertTrue(tx.recover()['firewall_restored'])

    def test_handoff_marker_survives_failed_recovery_and_cannot_resume_without_membership(self):
        tx,io,j=setup();tx.activate_empty();tx.handoff_membership()
        with self.assertRaises(ValueError):tx.recover()
        self.assertTrue(j.saved['membership_handed_off'])
        with self.assertRaisesRegex(ValueError,'membership journal'):
            t.Transaction(j.saved,io,j).recover()

    def test_membership_recovery_interrupted_after_every_command_resumes_after_objects_removed(self):
        from test_cp1_cni_membership import setup as membership_setup
        for step in range(10):
            tx,io,j=setup();tx.activate_empty();tx.handoff_membership()
            state=membership_setup()[0].state;state['lease']=copy.deepcopy(io.lease)
            io.fail_at=len(io.calls)+step;io.failure='after'
            with self.subTest(step=step):
                with self.assertRaises(Crash):tx.recover(membership_state=state)
                io.fail_at=None
                result=t.Transaction(j.saved,io,j).recover(membership_state=state)
                self.assertTrue(result['firewall_restored']);self.assertEqual(io.snapshot(),baseline())

    def test_disk_failure_still_narrowly_withdraws_but_never_claims_recovery(self):
        tx,io,j=setup();tx.activate_empty()
        def fail(_):raise OSError('disk full')
        j.save=fail
        with self.assertRaises(OSError):tx.recover()
        self.assertEqual(io.disable_attempts,1)
        self.assertFalse(j.saved['firewall_restored'])

    def test_malformed_command_types_are_not_success(self):
        for key,value in (('rc',False),('stdout',None),('stderr',[]),('truncated',0)):
            result={**OK,key:value}
            with self.subTest(key=key):self.assertFalse(t.clean_result(result))

    def test_guard_loss_or_management_probe_failure_prevents_enable(self):
        tx,io,j=setup();io.guard_alive=False
        with self.assertRaises(ValueError):tx.activate_empty()
        self.assertEqual(io.calls,[])
        tx,io,j=setup();io.external_ok=False
        with self.assertRaisesRegex(ValueError,'management'):tx.activate_empty()
        self.assertEqual(io.live['inventory']['runtime']['policies'][t.NAME]['disable'],'yes')
        self.assertNotIn(['--policy='+t.NAME,'--remove-disable'],io.calls)

    def test_unknown_command_completion_is_recovery_only_even_after_observed_success(self):
        for bad in ({**OK,'rc':False},{**OK,'stderr':'ALREADY_ENABLED'},{**OK,'truncated':True}):
            tx,io,j=setup();io.result=bad
            with self.assertRaisesRegex(ValueError,'uncertain'):tx.activate_empty()
            with self.assertRaisesRegex(ValueError,'unverified'):t.Transaction(j.saved,io,j).activate_empty()
            io.result=OK;self.assertTrue(t.Transaction(j.saved,io,j).recover()['firewall_restored'])

    def test_timeout_after_mutation_never_repeats_new_object_creation(self):
        tx,io,j=setup();io.fail_at=0;io.failure='timeout'
        with self.assertRaises(TimeoutError):tx.activate_empty()
        io.fail_at=None;again=t.Transaction(j.saved,io,j)
        with self.assertRaisesRegex(ValueError,'unverified'):again.activate_empty()
        self.assertEqual(len(io.calls),1);self.assertTrue(again.recover()['firewall_restored'])

    def test_fsync_failure_blocks_first_external_mutation(self):
        tx,io,j=setup();j.fail=True
        with self.assertRaises(OSError):tx.activate_empty()
        self.assertEqual(io.calls,[])

    def test_foreign_runtime_difference_original_rule_loss_and_owned_collision_block_plan(self):
        for case in ('foreign','six','collision'):
            value=baseline()
            if case=='foreign':value['inventory']['runtime']['direct']='foreign'
            elif case=='six':value['inventory']['runtime']['zones']['public']['rich rules'].remove(t.six_rules()[0])
            else:value['inventory']['permanent']['ipsets'][t.NAME]=t.set_definition()
            with self.subTest(case=case),self.assertRaises(ValueError):t.steps(value,lease()['run_id'])

    def test_recovery_requires_worker_cgroup_stop_before_mutation(self):
        tx,io,j=setup();tx.activate_empty();io.stopped=False;n=len(io.calls)
        with self.assertRaisesRegex(ValueError,'worker cgroup'):tx.recover()
        self.assertEqual(len(io.calls),n);self.assertFalse(j.saved['firewall_restored'])

    def test_restored_transaction_cannot_reactivate_or_grant_acceptance(self):
        tx,io,j=setup();tx.activate_empty();tx.recover()
        with self.assertRaisesRegex(ValueError,'cannot restart'):tx.activate_empty()
        bad=copy.deepcopy(j.saved);bad['full_recovery_complete']=True
        with self.assertRaisesRegex(ValueError,'cannot grant'):t.Transaction(bad,io,j)


if __name__=='__main__':unittest.main()
