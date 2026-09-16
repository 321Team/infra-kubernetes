"""Adversarial state-machine interleavings; no live network mutation."""
import copy
import unittest
from test_cp1_cni_membership import setup, observed, member, m, OK
import test_cp1_cni_membership as support


class IdentityRaceTests(unittest.TestCase):
    def test_generation_rejects_missing_or_rehashed_wrong_post_identity(self):
        manager,io,j=setup();state=manager.reconcile(observed(io))
        for change in ('missing','identity','clock'):
            with self.subTest(change=change):
                edited=copy.deepcopy(state);op=edited['operations'][0]
                if change=='missing':op.pop('identity_after')
                elif change=='identity':op['identity_after']['members'][0]['uid']='wrong'
                else:op['verified_clock']['wall']=999
                edited['generations'][0]['operations_sha256']=m.fingerprint(edited['operations'])
                with self.assertRaises(ValueError):m.validate_generation_chain(edited)

    def test_large_batch_renews_only_verified_observation_never_lease(self):
        manager,io,j=setup();desired=observed(io,[member(i) for i in range(3)]);original=io.change
        old_lease=copy.deepcopy(manager.lease)
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs);io.advance(15)
            self.assertEqual(m.guard_action(j.saved,io.clock()),'WATCH')
            return result
        io.change=delayed
        state=manager.reconcile(desired)
        self.assertEqual(len(state['expected_entries']),3)
        self.assertEqual(state['lease'],old_lease)
        self.assertEqual(state['latest_observation']['wall'],1045)
        m.validate_generation_chain(state)

    def test_one_operation_exceeding_stale_budget_still_withdraws(self):
        manager,io,j=setup();desired=observed(io);original=io.change
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs);io.advance(31);return result
        io.change=delayed
        with self.assertRaisesRegex(ValueError,'stale/future'):manager.reconcile_or_withdraw(desired)
        self.assertFalse(io.live['policy_enabled'])

    def test_identity_changed_after_observation_blocks_first_add(self):
        for key,value in [('uid','reused'),('sandbox_id','f'*64),('ifindex',999),('peer_ifindex',999)]:
            with self.subTest(key=key):
                manager,io,j=setup();desired=observed(io)
                io.member_rows[0][key]=value
                with self.assertRaisesRegex(ValueError,'eligible identity changed'):
                    manager.reconcile_or_withdraw(desired)
                self.assertFalse(any(action=='add' for action,_ in io.calls))
                self.assertFalse(io.live['policy_enabled'])

    def test_identity_changed_during_command_withdraws_and_never_commits_generation(self):
        manager,io,j=setup();desired=observed(io);original=io.change
        def change(*args,**kwargs):
            result=original(*args,**kwargs);io.member_rows[0]['sandbox_id']='f'*64;return result
        io.change=change
        with self.assertRaisesRegex(ValueError,'eligible identity changed'):
            manager.reconcile_or_withdraw(desired)
        self.assertEqual(manager.state['generations'],[])
        self.assertEqual(io.live['native_entries'],[])
        self.assertFalse(io.live['policy_enabled'])
        self.assertTrue(manager.state['withdrawn_pending'])

    def test_unchanged_membership_still_requires_a_fresh_identity_read(self):
        manager,io,j=setup();manager.reconcile(observed(io));desired=observed(io)
        io.member_rows=[]
        with self.assertRaisesRegex(ValueError,'eligible identity changed'):
            manager.reconcile_or_withdraw(desired)
        self.assertFalse(io.live['policy_enabled'])

    def test_api_failure_during_identity_check_withdraws(self):
        manager,io,j=setup();desired=observed(io)
        def outage(_):raise TimeoutError('API/CRI unavailable')
        io.check_members=outage
        with self.assertRaises(TimeoutError):manager.reconcile_or_withdraw(desired)
        self.assertEqual(io.live['native_entries'],[])
        self.assertFalse(io.live['policy_enabled'])

    def test_command_adapter_rechecks_identity_after_whole_state_read(self):
        helper=support.AdapterTests();calls=[]
        adapter=helper.make(lambda *a,**kw:calls.append(a) or OK)
        desired=observed(helper.io)
        def raced():
            helper.io.member_rows[0]['uid']='new';return helper.io.snapshot()
        adapter.observer=raced
        with self.assertRaisesRegex(ValueError,'eligible identity changed'):
            adapter.change('add',m.entry(member()),deadline={'wall':1030,'boottime':140},expected_observation=desired)
        self.assertEqual(calls,[])

    def test_adapter_without_identity_collector_cannot_add(self):
        helper=support.AdapterTests();calls=[];adapter=helper.make(lambda *a,**kw:calls.append(a) or OK)
        adapter.membership_observer=None
        with self.assertRaisesRegex(ValueError,'membership observer required'):
            adapter.change('add',m.entry(member()),deadline={'wall':1030,'boottime':140},expected_observation=observed(helper.io))
        self.assertEqual(calls,[])


class BootstrapTests(unittest.TestCase):
    def test_empty_bootstrap_can_seed_before_guard_but_cannot_add(self):
        manager,io,j=setup();io.live['guard_armed']=False
        state=m.initial_state(manager.lease,io.snapshot(),io.clock())
        self.assertEqual(m.guard_action(state,io.clock()),'RECOVER')
        state=m.seed_observation(state,observed(io),io.snapshot(),io.clock())
        j.save(state);manager=m.Manager(state,io,j)
        self.assertEqual(m.guard_action(state,io.clock()),'WATCH')
        with self.assertRaisesRegex(ValueError,'guard unavailable'):manager.reconcile(observed(io))
        self.assertFalse(io.calls)

    def test_bootstrap_does_not_reset_stale_timer_or_lease(self):
        manager,io,j=setup();desired=observed(io)
        state=m.seed_observation(manager.state,desired,io.snapshot(),io.clock())
        self.assertEqual(state['lease'],manager.lease)
        with self.assertRaises(ValueError):m.seed_observation(state,desired,io.snapshot(),io.clock())
        io.advance(m.STALE)
        self.assertEqual(m.guard_action(state,io.clock()),'RECOVER')

    def test_bootstrap_cannot_hide_nonempty_or_unowned_grants(self):
        for edit in (lambda s:s.update(native_entries=[m.entry(member())],daemon_entries=[m.entry(member())]),
                     lambda s:s.update(owned=False),lambda s:s.update(policy_enabled=False)):
            manager,io,j=setup();edit(io.live)
            with self.assertRaises(ValueError):m.seed_observation(manager.state,observed(io),io.snapshot(),io.clock())

    def test_guard_does_not_accept_a_timestamp_only_heartbeat(self):
        manager,io,j=setup()
        manager.state['latest_observation']={'wall':1000,'boottime':110}
        self.assertEqual(m.guard_action(manager.state,io.clock()),'RECOVER')


if __name__=='__main__':unittest.main()
