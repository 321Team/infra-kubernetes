"""Independent guard covers definition mutations before membership exists."""
import copy
import unittest
from test_cp1_cni_transition import setup, Crash
from test_cp1_cni_guard import unit_fixture
import cp1_cni_guard as g
import cp1_cni_transition as t


def fixture():
    tx,io,j=setup();io.lease.update(wall_start=1000,wall_end=8200,boot_start=110,boot_end=7310)
    state=t.initial(io.lease,io.live);j.save(state);tx=t.Transaction(state,io,j)
    return tx,io,j


def check(state,clock,units=None):
    units=copy.deepcopy(units or unit_fixture(state['lease']));calls=[]
    units['properties'][g.names(state['lease']['run_id'])['timer']]['NextElapseUSecMonotonic']=str(int((clock['monotonic']+5)*1000000))
    def command(argv,timeout):calls.append(argv);return {'rc':0,'stdout':'','stderr':'','truncated':False}
    result=g.supervise_preparation(state,clock,units,command,lambda x:calls.append('persist'))
    return result,calls


class PreparationGuardTests(unittest.TestCase):
    def test_guard_is_required_before_first_firewall_mutation(self):
        tx,io,j=fixture();clock={**io.clock(),'monotonic':110}
        result,calls=check(j.saved,clock);self.assertEqual(result['action'],'WATCH');self.assertEqual(calls,[])
        self.assertEqual(io.calls,[])
        units=unit_fixture(io.lease);units['links']={}
        result,calls=check(j.saved,clock,units)
        self.assertEqual(result['action'],'RECOVER');self.assertEqual(calls[0],'persist')

    def test_every_exact_pending_before_or_after_prefix_is_bounded_watch(self):
        for index in range(10):
            for failure in ('before','after'):
                tx,io,j=fixture();io.fail_at=index;io.failure=failure
                with self.assertRaises(Crash):tx.activate_empty()
                with self.subTest(index=index,failure=failure):
                    result,calls=check(j.saved,{**io.clock(),'monotonic':110})
                    self.assertEqual(result['action'],'WATCH');self.assertEqual(calls,[])

    def test_stale_pending_stopped_worker_and_boot_change_dispatch_recovery(self):
        for case in ('stale','stopped','boot'):
            tx,io,j=fixture();io.fail_at=2;io.failure='after'
            with self.assertRaises(Crash):tx.activate_empty()
            clock={**io.clock(),'monotonic':110};units=unit_fixture(io.lease)
            if case=='stale':clock['wall']+=30;clock['boottime']+=30
            elif case=='stopped':units['properties'][g.names(io.lease['run_id'])['work']]['MainPID']='0'
            else:clock['boot_id']='a'*8+clock['boot_id'][8:]
            with self.subTest(case=case):
                result,calls=check(j.saved,clock,units);self.assertEqual(result['action'],'RECOVER')
                self.assertEqual(calls[-1][-1],g.names(io.lease['run_id'])['recover'])

    def test_fresh_progress_cannot_extend_absolute_preparation_window(self):
        tx,io,j=fixture();io.now.update(wall=1149,boottime=259)
        io.fail_at=0;io.failure='after'
        with self.assertRaises(Crash):tx.activate_empty()
        result,_=check(j.saved,{**io.clock(),'monotonic':259});self.assertEqual(result['action'],'WATCH')
        result,_=check(j.saved,{**io.clock(),'wall':1150,'boottime':260,'monotonic':260})
        self.assertEqual(result['action'],'RECOVER')

    def test_empty_activation_requires_full_verified_plan_and_only_then_handoff(self):
        tx,io,j=fixture();tx.activate_empty();clock={**io.clock(),'monotonic':110}
        self.assertEqual(check(j.saved,clock)[0]['action'],'WATCH')
        tx.handoff_membership()
        self.assertEqual(check(j.saved,clock)[0]['action'],'RECOVER')
        # Caller must now read and supervise the real membership state.

    def test_unknown_expected_pending_result_and_clock_cannot_keep_guard_watching(self):
        for case in ('expected','pending','result','clock','phase'):
            tx,io,j=fixture();io.fail_at=1;io.failure='after'
            with self.assertRaises(Crash):tx.activate_empty()
            state=copy.deepcopy(j.saved)
            if case=='expected':state['expected']['fixed_sha256']='f'*64
            elif case=='pending':state['pending']['argv']=['--runtime-to-permanent']
            elif case=='result':state['operations'][0]['result']['stdout']='warning'
            elif case=='clock':state['pending']['clock']['wall']=999
            else:state['phase']='EMPTY_ACTIVE'
            with self.subTest(case=case):self.assertEqual(check(state,{**io.clock(),'monotonic':110})[0]['action'],'RECOVER')

    def test_foreign_loaded_unit_never_dispatches_any_unit(self):
        tx,io,j=fixture();units=unit_fixture(io.lease)
        units['properties'][g.names(io.lease['run_id'])['recover']]['FragmentPath']='/foreign'
        with self.assertRaises(ValueError):check(j.saved,{**io.clock(),'monotonic':110},units)


if __name__=='__main__':unittest.main()
