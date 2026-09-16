"""Bounded IO composition using actual-derived records, mocked local calls."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_cp1_cni_transition import setup, baseline, OK
from test_cp1_cni_transition_observer import capture, CLOCK
from test_cp1_cni_recovery import management
import cp1_cni_transition_io as i


def adapter():
    tx,model,j=setup();calls=[];mono=[100.]
    node=SimpleNamespace(__file__=str(Path(__file__).resolve().parents[1]/'scripts/node-firewall.py'))
    def runner(args,**kwargs):calls.append((args,kwargs));return copy.deepcopy(OK)
    value=i.LocalTransitionIO(node,model.lease,baseline(),lambda:copy.deepcopy(CLOCK),runner,
        lambda:calls.append('guard'),lambda:calls.append('quiesced'),
        lambda challenge,timeout:management(challenge,CLOCK),lambda:calls.append('emergency'),
        deadline=249,monotonic=lambda:mono[0])
    return value,calls,mono


class TransitionIOTests(unittest.TestCase):
    def test_bounded_source_pinned_full_and_peer_capture_compose_verified_snapshot(self):
        value,calls,mono=adapter();r,peer=capture()
        with patch.object(i,'collect',return_value=r) as whole,patch.object(i,'peer_capture',return_value=peer) as peers:
            actual=value.snapshot()
        self.assertEqual(actual['inventory'],baseline()['inventory'])
        self.assertEqual(whole.call_args.kwargs['deadline'],129)
        self.assertEqual(peers.call_args.args[1],129)
        self.assertTrue(whole.call_args.kwargs['compatibility']);self.assertTrue(whole.call_args.kwargs['delta_api'])
        self.assertEqual(value.evidence['verified_snapshot'],actual)

    def test_read_deadline_does_not_renew_with_slow_whole_collection(self):
        value,calls,mono=adapter();r,peer=capture();passed=[]
        def slow(*args,**kwargs):mono[0]=128;passed.append(kwargs['deadline']);return r
        def peers(node,deadline):passed.append(deadline);raise ValueError('peer budget exhausted')
        with patch.object(i,'collect',side_effect=slow),patch.object(i,'peer_capture',side_effect=peers):
            with self.assertRaisesRegex(ValueError,'peer budget'):value.snapshot()
        self.assertEqual(passed,[129,129]);self.assertEqual(value.evidence,{'capture':r})

    def test_no_cached_success_after_failed_read(self):
        value,calls,mono=adapter();r,peer=capture()
        with patch.object(i,'collect',return_value=r),patch.object(i,'peer_capture',return_value=peer):value.snapshot()
        bad=copy.deepcopy(r);bad['complete']=False
        with patch.object(i,'collect',return_value=bad),patch.object(i,'peer_capture',return_value=peer):
            with self.assertRaises(ValueError):value.snapshot()
        self.assertNotIn('verified_snapshot',value.evidence)

    def test_command_allowlist_cleanup_cannot_enable_or_add_member(self):
        for args,cleanup in ((['--policy='+i.NAME,'--remove-disable'],True),
                             (['--ipset='+i.NAME,'--add-entry=10.244.246.80/32,cali00000000000'],False),
                             (['--runtime-to-permanent'],True),(['--permanent','--delete-zone=public'],True)):
            value,calls,mono=adapter()
            with self.subTest(args=args),self.assertRaisesRegex(ValueError,'unreviewed'):
                value.command(args,cleanup=cleanup)
            self.assertEqual(calls,[])

    def test_approved_cleanup_checks_quiescence_and_passes_remaining_timeout(self):
        value,calls,mono=adapter();mono[0]=247
        result=value.command(['--policy='+i.NAME,'--add-disable'],cleanup=True)
        self.assertEqual(result,OK);self.assertEqual(calls[0],'quiesced')
        self.assertEqual(calls[1],(['firewall-cmd','--policy='+i.NAME,'--add-disable'],{'timeout':2}))

    def test_hard_deadline_exhaustion_prevents_both_grant_and_cleanup(self):
        value,calls,mono=adapter();mono[0]=249
        for cleanup in (False,True):
            with self.subTest(cleanup=cleanup),self.assertRaisesRegex(ValueError,'hard deadline'):
                value.command(['--reload'],cleanup=cleanup)
        self.assertEqual(calls,[])

    def test_live_guard_or_work_exit_checks_cannot_be_bypassed(self):
        for cleanup in (False,True):
            value,calls,mono=adapter()
            def fail():raise ValueError('boundary failed')
            if cleanup:value.quiesced=fail
            else:value.live_guard=fail
            with self.subTest(cleanup=cleanup),self.assertRaisesRegex(ValueError,'boundary'):
                value.command(['--reload'],cleanup=cleanup)
            self.assertEqual(calls,[])

    def test_management_uses_new_challenge_and_late_reply_blocks_enable(self):
        value,calls,mono=adapter();a=value.management();b=value.management()
        self.assertNotEqual(a['challenge'],b['challenge'])
        def late(challenge,timeout):mono[0]=250;return management(challenge,CLOCK)
        value.external_management=late
        with self.assertRaisesRegex(ValueError,'hard deadline'):value.management()

    def test_invalid_or_boolean_budget_and_source_mismatch_rejected(self):
        value,calls,mono=adapter()
        for deadline in (True,float('inf'),251,99):
            with self.subTest(deadline=deadline),self.assertRaises(ValueError):
                i.LocalTransitionIO(value.node,value.lease,baseline(),value.clock,value.runner,value.live_guard,
                    value.quiesced,value.external_management,value.emergency_disable,deadline=deadline,monotonic=lambda:100)
        with patch.object(i,'NODE_SHA','0'*64),self.assertRaisesRegex(ValueError,'source'):
            adapter()


if __name__=='__main__':unittest.main()
