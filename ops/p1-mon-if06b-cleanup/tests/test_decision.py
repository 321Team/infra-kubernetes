"""SYNTHETIC, offline tests. No cluster, credentials, sockets or subprocesses."""
import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('decision', Path(__file__).resolve().parents[1] / 'decision.py')
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


class Decisions(unittest.TestCase):
    def setUp(self):
        self.binding = dict(session_id='synthetic-session', cluster_uid='synthetic-cluster',
                            host_boot_id='synthetic-boot', guardian_epoch='synthetic-epoch')
        self.body = dict(apiVersion=d.API, kind='GlobalNetworkPolicy',
                         metadata=dict(name=d.NAME, uid='synthetic-uid', resourceVersion='opaque:rv-a'),
                         spec=dict(selector='synthetic == "test"', types=['Egress']))
        # Test-only values do not define runtime lease or cleanup budgets.
        self.args = dict(binding=self.binding, http_status=201, create_body=self.body,
                         reviewed_spec_sha256=d.spec_hash(self.body['spec']), created_mono=10,
                         expires_mono=20, cleanup_deadline_mono=30, observation_max_age=2)
        self.receipt = d.owned_receipt(**self.args)
        self.obs = dict(binding=copy.deepcopy(self.binding), requested_name=d.NAME,
                        observed_mono=20, http_status=200, body=copy.deepcopy(self.body))

    def decide(self, **changes):
        args = dict(receipt=self.receipt, binding=self.binding, observation=self.obs,
                    now_mono=20, trigger='expiry')
        args.update(changes)
        return d.decide(**args)

    def test_confirmed_create_only(self):
        for status in (None, 200, 202, 409, 403, '201', True):
            with self.subTest(status=status), self.assertRaises(ValueError):
                d.owned_receipt(**dict(self.args, http_status=status))

    def test_create_wrong_object_or_spec_rejected(self):
        for part, key, value in [('metadata', 'name', 'other'), ('metadata', 'namespace', 'default'),
                                 ('metadata', 'uid', ''), ('metadata', 'finalizers', ['unexpected']),
                                 ('metadata', 'deletionTimestamp', 'synthetic'), ('spec', 'types', ['Ingress'])]:
            body = copy.deepcopy(self.body)
            body[part][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                d.owned_receipt(**dict(self.args, create_body=body))

    def test_expiry_one_intent_exact_uid_and_fresh_opaque_rv(self):
        self.obs['body']['metadata']['resourceVersion'] = 'opaque:rv-b'
        before = copy.deepcopy((self.receipt, self.binding, self.obs))
        result = self.decide()
        self.assertEqual(result['action'], 'DELETE_INTENT')
        self.assertEqual(result['path'], d.PATH)
        self.assertEqual(result['body'], dict(apiVersion='v1', kind='DeleteOptions',
                          preconditions=dict(uid='synthetic-uid', resourceVersion='opaque:rv-b')))
        self.assertEqual(result['next_receipt']['phase'], 'DELETE_INTENT')
        self.assertEqual((self.receipt, self.binding, self.obs), before)
        result['next_receipt']['binding']['session_id'] = 'changed'
        self.assertEqual(self.receipt['binding'], self.binding)

    def test_wait_and_abort_or_complete(self):
        self.obs['observed_mono'] = 19
        self.assertEqual(self.decide(now_mono=19)['action'], 'WAIT')
        for trigger in ('abort', 'complete'):
            self.assertEqual(self.decide(now_mono=19, trigger=trigger)['action'], 'DELETE_INTENT')

    def test_deadline_and_stale_or_future_observation(self):
        for now, stamp, expected in [(30,30,'CLEANUP_WINDOW_EXPIRED'), (31,31,'CLEANUP_WINDOW_EXPIRED'),
                                     (20,17,'OBSERVATION_STALE'), (20,21,'OBSERVATION_CLOCK_INVALID'),
                                     (20,9,'OBSERVATION_CLOCK_INVALID')]:
            self.obs['observed_mono'] = stamp
            with self.subTest(now=now, stamp=stamp):
                self.assertEqual(self.decide(now_mono=now), dict(action='HANDOFF', reason=expected))

    def test_generation_changes_never_delete(self):
        for key in self.binding:
            changed = dict(self.binding, **{key:'changed'})
            self.assertEqual(self.decide(binding=changed)['reason'], 'GENERATION_CHANGED')
            self.obs['binding'] = changed
            self.assertEqual(self.decide()['reason'], 'OBSERVATION_TARGET_CHANGED')

    def test_foreign_uid_and_spec_drift_never_delete(self):
        self.obs['body']['metadata']['uid'] = 'replacement'
        self.assertEqual(self.decide()['reason'], 'FOREIGN_UID')
        self.obs['body']['metadata']['uid'] = 'synthetic-uid'
        self.obs['body']['spec']['types'] = ['Ingress']
        self.assertEqual(self.decide()['reason'], 'POLICY_DRIFT')

    def test_lifecycle_handoff_without_force(self):
        for key, value in [('finalizers',['other']), ('deletionTimestamp','synthetic')]:
            self.obs['body']['metadata'] = dict(self.body['metadata'], **{key:value})
            self.assertEqual(self.decide()['reason'], 'UNEXPECTED_DELETION_OR_FINALIZERS')

    def test_errors_not_absence(self):
        for status in (403,401,500,429,0,None,True,'404'):
            self.obs.update(http_status=status, body={})
            with self.subTest(status=status):
                self.assertEqual(self.decide()['action'], 'HANDOFF')

    def notfound(self):
        self.obs.update(http_status=404, body=dict(apiVersion='v1', kind='Status', reason='NotFound',
                        code=404, details=dict(name=d.NAME, group=d.GROUP, kind=d.RESOURCE)))

    def test_explicit_notfound_is_absence_only(self):
        self.notfound()
        self.assertEqual(self.decide(), dict(action='ABSENT', reason='EXPLICIT_NOT_FOUND_BASELINE_STILL_UNVERIFIED'))

    def test_untrusted_404_or_wrong_target_never_success(self):
        for key, value in [('name','other'), ('group','other'), ('kind','other')]:
            self.notfound()
            self.obs['body']['details'][key] = value
            self.assertEqual(self.decide()['action'], 'HANDOFF')
        for key, value in [('code','404'), ('reason','Forbidden'), ('kind','Other'), ('apiVersion','v2')]:
            self.notfound()
            self.obs['body'][key] = value
            self.assertEqual(self.decide()['action'], 'HANDOFF')

    def test_claimed_delete_never_reissued_even_after_timeout(self):
        intent = self.decide()['next_receipt']
        for status, phase in [(200,'DELETE_ACK'), (202,'DELETE_ACK'), (409,'CONFLICT'),
                              (None,'DELETE_UNKNOWN'), (403,'DELETE_UNKNOWN'), (500,'DELETE_UNKNOWN')]:
            receipt = d.record_delete_result(intent,status)
            self.assertEqual(receipt['phase'],phase)
            self.assertEqual(self.decide(receipt=receipt)['action'], 'HANDOFF')
        self.assertEqual(self.decide(receipt=intent)['action'], 'HANDOFF')
        self.notfound()
        self.assertEqual(self.decide(receipt=intent)['action'], 'ABSENT')

    def test_ack_requires_intent(self):
        with self.assertRaises(ValueError):
            d.record_delete_result(self.receipt, 200)

    def test_invalid_budgets_and_clocks_fail_closed(self):
        for key in ('created_mono','expires_mono','cleanup_deadline_mono','observation_max_age'):
            for value in (-1, float('nan'), float('inf'), True, '20', None):
                receipt = dict(self.receipt, **{key:value})
                self.assertEqual(self.decide(receipt=receipt)['action'], 'HANDOFF')
        for value in (-1,float('nan'),float('inf'),True,'20',None,9):
            self.assertEqual(self.decide(now_mono=value)['action'], 'HANDOFF')
        for args in [dict(expires_mono=10),dict(cleanup_deadline_mono=20),dict(observation_max_age=0)]:
            with self.assertRaises(ValueError):
                d.owned_receipt(**dict(self.args, **args))

    def test_bad_receipts_observations_and_trigger_fail_closed(self):
        for receipt in (None,{},dict(self.receipt,unknown=True),dict(self.receipt,phase=[])):
            self.assertEqual(self.decide(receipt=receipt)['action'], 'HANDOFF')
        for obs in (None,{},dict(self.obs,requested_name='other'),dict(self.obs,body=[])):
            self.assertEqual(self.decide(observation=obs)['action'], 'HANDOFF')
        self.assertEqual(self.decide(trigger='force')['action'], 'HANDOFF')


if __name__ == '__main__':
    unittest.main()
