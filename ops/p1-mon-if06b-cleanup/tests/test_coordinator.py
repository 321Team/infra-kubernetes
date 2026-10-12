"""SYNTHETIC transport faults and Linux journal integration. No network/API."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coordinator as c
import decision as d
import journal as j


class DecoderTests(unittest.TestCase):
    def test_status_and_body_are_separate(self):
        self.assertEqual(c.decode_response((403, b'{"reason":"Forbidden"}')), (403, {'reason': 'Forbidden'}))

    def test_invalid_response_is_fixed_error(self):
        cases = [(True, b'{}'), (600, b'{}'), (200, '{}'), (200, b''), (200, b'[]'),
                 (200, b'\xff'), (200, b'{"token":"SECRET",'),
                 (200, b'{"a":1,"a":2}'), (200, b'{"a":NaN}'),
                 (200, b' ' * (c.MAX_RESPONSE + 1)), [200, b'{}']]
        for response in cases:
            with self.subTest(response_type=type(response)), self.assertRaisesRegex(c.CoordinatorError, '^RESPONSE_INVALID$'):
                c.decode_response(response)

    def test_legacy_and_new_terminal_schemas(self):
        self.assertIsNone(j._terminal({}))
        record = dict(reason='FOREIGN_UID', observed_mono=20, http_status=200)
        self.assertEqual(j._terminal(record), record)
        for bad in [dict(record, reason='secret response'), dict(record, observed_mono=True),
                    dict(record, http_status=True), dict(record, raw='private')]:
            with self.assertRaisesRegex(j.JournalError, 'TERMINAL_DIAGNOSTIC_INVALID'):
                j._terminal(bad)

    def test_diagnostic_does_not_persist_untrusted_message(self):
        self.assertEqual(j._diagnostic('secret response', {'observed_mono': float('nan'), 'http_status': True}),
                         dict(reason='INPUT_INVALID', observed_mono=None, http_status=None))


@unittest.skipUnless(sys.platform == 'linux', 'Linux real journal integration')
class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        os.chmod(self.path, 0o700)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.binding = dict(session_id='SYNTHETIC-session', cluster_uid='SYNTHETIC-cluster',
                            host_boot_id='SYNTHETIC-boot', guardian_epoch='SYNTHETIC-epoch')
        self.manifest = dict(apiVersion=d.API, kind='GlobalNetworkPolicy', metadata=dict(name=d.NAME),
                             spec=dict(selector='synthetic == "test"', types=['Egress']))
        self.body = copy.deepcopy(self.manifest)
        self.body['metadata'].update(uid='SYNTHETIC-uid', resourceVersion='opaque-original')
        self.not_found = dict(apiVersion='v1', kind='Status', reason='NotFound', code=404,
                             details=dict(name=d.NAME, group=d.GROUP, kind=d.RESOURCE))
        self.now = 10
        self.calls, self.queue, self.seen_events = [], [], []
        self.fsyncs = []

    def events(self):
        return [json.loads(row) for row in (self.path / j.JOURNAL).read_bytes().splitlines()]

    def response(self, status, body):
        return status, json.dumps(body).encode()

    def transport(self, **request):
        self.calls.append(request)
        self.seen_events.append([r['event'] for r in self.events()])
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(request)
        return item

    def make(self, **changes):
        kwargs = dict(binding=self.binding, manifest=self.manifest,
                      reviewed_manifest_sha256=c.manifest_hash(self.manifest),
                      reviewed_spec_sha256=d.spec_hash(self.body['spec']), expires_mono=20,
                      cleanup_deadline_mono=30, observation_max_age=2, request_timeout=3,
                      clock=lambda: self.now, binding_probe=lambda: copy.deepcopy(self.binding),
                      transport=self.transport)
        kwargs.update(changes)
        obj = c.Coordinator(self.fd, **kwargs)
        self.addCleanup(obj.close)
        return obj

    def owned(self):
        obj = self.make()
        self.queue.append(self.response(201, self.body))
        self.assertEqual(obj.create()['action'], 'OWNED')
        self.now = 20
        return obj

    def history(self, obj):
        obj.close()
        return j.inspect_history(self.fd)

    def test_intent_is_synced_before_each_write_and_readback_is_separate(self):
        sync = os.fsync
        def record(fd):
            self.fsyncs.append(fd)
            sync(fd)
        obj = self.make()
        def created(request):
            self.assertEqual(len(self.fsyncs), 0)  # constructor already completed its fsyncs
            self.assertEqual(self.events()[-1]['event'], 'CREATE_INTENT')
            return self.response(201, self.body)
        self.queue.append(created)
        obj.create()
        self.now = 20
        newer = copy.deepcopy(self.body)
        newer['metadata']['resourceVersion'] = 'opaque-new'
        def deleted(request):
            self.assertEqual(len(self.fsyncs), 2)  # file and directory intent fsync
            self.assertEqual(self.events()[-1]['event'], 'DELETE_INTENT')
            self.assertEqual(request['body']['preconditions'], dict(uid='SYNTHETIC-uid', resourceVersion='opaque-new'))
            return self.response(202, {'kind': 'Status'})
        self.queue.extend([self.response(200, newer), deleted])
        with patch.object(j.os, 'fsync', side_effect=record):
            self.assertEqual(obj.cleanup(trigger='expiry')['action'], 'READBACK_REQUIRED')
        self.queue.append(self.response(404, self.not_found))
        result = obj.cleanup(trigger='expiry')
        self.assertEqual(result['action'], 'ABSENT')
        self.assertFalse(result['baseline_verified'])
        self.assertEqual([x['method'] for x in self.calls], ['POST', 'GET', 'DELETE', 'GET'])
        history = self.history(obj)
        self.assertEqual(history['terminal_diagnostic']['reason'], 'EXPLICIT_NOT_FOUND_BASELINE_STILL_UNVERIFIED')
        self.assertFalse(history['automatic_replay_allowed'])

    def test_failed_create_intent_never_sends_post(self):
        with patch.object(j.os, 'fsync', side_effect=OSError('PRIVATE disk failure')):
            with self.assertRaises(OSError): self.make()
        self.assertEqual(self.calls, [])

    def test_create_timeout_cannot_adopt_existing_object_or_retry(self):
        obj = self.make()
        self.queue.append(TimeoutError('PRIVATE endpoint/token'))
        result = obj.create()
        self.assertEqual(result['reason'], 'TRANSPORT_UNCERTAIN')
        with self.assertRaises(c.CoordinatorError): obj.create()
        with self.assertRaises(c.CoordinatorError): obj.cleanup(trigger='abort')
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(b'PRIVATE', (self.path / j.JOURNAL).read_bytes())

    def test_existing_journal_is_not_reopened(self):
        obj = self.make()
        obj.close()
        with self.assertRaises(FileExistsError): self.make()
        self.assertEqual(self.calls, [])

    def test_invalid_manifest_or_hash_fails_before_journal(self):
        bad = copy.deepcopy(self.manifest)
        bad['metadata']['finalizers'] = ['synthetic']
        with self.assertRaises(c.CoordinatorError): self.make(manifest=bad)
        with self.assertRaises(c.CoordinatorError): self.make(reviewed_manifest_sha256='0'*64)
        self.assertFalse((self.path / j.JOURNAL).exists())

    def test_no_defaults_or_invalid_budget(self):
        for changes in [dict(expires_mono=10), dict(request_timeout=0), dict(observation_max_age=True),
                        dict(cleanup_deadline_mono=19), dict(request_timeout=float('inf'))]:
            with self.subTest(changes=changes), self.assertRaises(c.CoordinatorError): self.make(**changes)
        self.assertFalse((self.path / j.JOURNAL).exists())

    def test_binding_changes_before_create_prevents_send(self):
        obj = self.make()
        self.binding['host_boot_id'] = 'different'
        self.assertEqual(obj.create()['reason'], 'CREATE_NOT_SENT')
        self.assertEqual(self.calls, [])
        self.assertEqual([e['event'] for e in self.events()], ['CREATE_INTENT', 'HANDOFF'])
        self.assertEqual(self.history(obj)['terminal_diagnostic']['reason'], 'CREATE_NOT_SENT')

    def test_clock_reversal_prevents_send(self):
        obj = self.make()
        self.now = 9
        self.assertEqual(obj.create()['reason'], 'CREATE_NOT_SENT')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.history(obj)['terminal_diagnostic']['reason'], 'CREATE_NOT_SENT')

    def test_create_expired_before_send_is_not_sent(self):
        obj = self.make()
        self.now = 20
        self.assertEqual(obj.create()['reason'], 'CREATE_NOT_SENT')
        self.assertEqual(self.calls, [])
        self.assertEqual([e['event'] for e in self.events()], ['CREATE_INTENT', 'HANDOFF'])
        self.assertEqual(self.history(obj)['terminal_diagnostic']['reason'], 'CREATE_NOT_SENT')

    def test_late_create_response_is_not_ownership(self):
        obj = self.make()
        def late(_):
            self.now = 14  # exceeds the explicitly supplied request deadline13
            return self.response(201, self.body)
        self.queue.append(late)
        self.assertEqual(obj.create()['reason'], 'REQUEST_WINDOW_EXPIRED')
        self.assertEqual([r['method'] for r in self.calls], ['POST'])
        self.assertEqual(self.events()[-1]['payload']['reason'], 'REQUEST_WINDOW_EXPIRED')
        self.assertNotIn('OWNED', [e['event'] for e in self.events()])

    def test_create_conflict_is_not_owned_and_reason_is_preserved(self):
        obj = self.make()
        self.queue.append(self.response(409, {'kind': 'Status'}))
        self.assertEqual(obj.create()['reason'], 'CREATE_NOT_CONFIRMED')
        self.assertEqual(self.events()[-1]['event'], 'HANDOFF')

    def test_create_response_spec_drift_is_not_owned_and_reason_is_preserved(self):
        obj = self.make()
        wrong = copy.deepcopy(self.body)
        wrong['spec']['selector'] = 'unexpected'
        self.queue.append(self.response(201, wrong))
        result = obj.create()
        self.assertEqual(result['reason'], 'CREATE_SPEC_DRIFT')
        self.assertTrue(result['handoff_recorded'])
        self.assertEqual(self.events()[-1]['payload']['reason'], 'CREATE_SPEC_DRIFT')
        self.assertNotIn('OWNED', [e['event'] for e in self.events()])

    def test_create_lifecycle_change_is_not_owned(self):
        obj = self.make()
        wrong = copy.deepcopy(self.body)
        wrong['metadata']['finalizers'] = ['synthetic']
        self.queue.append(self.response(201, wrong))
        result = obj.create()
        self.assertEqual(result['reason'], 'CREATE_LIFECYCLE_UNEXPECTED')
        self.assertTrue(result['handoff_recorded'])

    def test_mutation_of_caller_input_does_not_change_sent_manifest(self):
        obj = self.make()
        original = copy.deepcopy(self.manifest)
        self.manifest['spec']['selector'] = 'changed'
        self.queue.append(self.response(201, self.body))
        self.assertEqual(obj.create()['action'], 'OWNED')
        self.assertEqual(self.calls[0]['body'], original)

    def test_before_expiry_waits_without_delete(self):
        obj = self.owned()
        self.now = 19
        self.queue.append(self.response(200, self.body))
        self.assertEqual(obj.cleanup(trigger='expiry')['action'], 'WAIT')
        self.assertEqual([r['method'] for r in self.calls], ['POST', 'GET'])

    def test_abort_can_delete_before_expiry(self):
        obj = self.owned()
        self.now = 19
        self.queue.extend([self.response(200, self.body), self.response(200, {'kind':'Status'})])
        self.assertEqual(obj.cleanup(trigger='abort')['action'], 'READBACK_REQUIRED')

    def test_untrusted_not_found_is_not_absence(self):
        obj = self.owned()
        wrong = copy.deepcopy(self.not_found)
        wrong['details']['name'] = 'other'
        self.queue.append(self.response(404, wrong))
        self.assertEqual(obj.cleanup(trigger='expiry')['reason'], 'NOT_FOUND_TARGET_CHANGED')
        self.assertFalse(any(r['method']=='DELETE' for r in self.calls))

    def test_foreign_uid_records_reason_without_delete(self):
        obj = self.owned()
        foreign = copy.deepcopy(self.body)
        foreign['metadata']['uid'] = 'foreign'
        self.queue.append(self.response(200, foreign))
        self.assertEqual(obj.cleanup(trigger='expiry')['reason'], 'FOREIGN_UID')
        history = self.history(obj)
        self.assertEqual(history['terminal_diagnostic'], dict(reason='FOREIGN_UID', observed_mono=20, http_status=200))

    def test_preintent_read_failure_stays_terminal(self):
        obj = self.owned()
        self.queue.append(self.response(503, {'kind':'Status'}))
        self.assertEqual(obj.cleanup(trigger='expiry')['reason'], 'API_READ_FAILED')
        with self.assertRaises(c.CoordinatorError): obj.cleanup(trigger='expiry')
        self.assertFalse(any(r['method']=='DELETE' for r in self.calls))

    def test_stale_read_cannot_delete(self):
        obj = self.owned()
        def stale(_):
            self.now = 22.5  # within timeout3, outside max observation age2
            return self.response(200, self.body)
        self.queue.append(stale)
        self.assertEqual(obj.cleanup(trigger='expiry')['reason'], 'OBSERVATION_STALE')

    def test_failed_delete_intent_never_sends_delete(self):
        obj = self.owned()
        self.queue.append(self.response(200, self.body))
        with patch.object(j.os, 'fsync', side_effect=OSError('PRIVATE failure')):
            result = obj.cleanup(trigger='expiry')
        self.assertEqual(result['action'], 'HANDOFF')
        self.assertFalse(result['handoff_recorded'])
        self.assertFalse(any(r['method']=='DELETE' for r in self.calls))

    def test_generation_changes_after_intent_before_send(self):
        obj = self.owned()
        self.queue.append(self.response(200, self.body))
        sync = os.fsync
        def change(fd):
            sync(fd)
            self.binding['guardian_epoch'] = 'changed'
        with patch.object(j.os, 'fsync', side_effect=change):
            result = obj.cleanup(trigger='expiry')
        self.assertEqual(result['reason'], 'DELETE_NOT_SENT')
        self.assertFalse(any(r['method']=='DELETE' for r in self.calls))
        self.assertEqual([e['event'] for e in self.events()],
                         ['CREATE_INTENT', 'OWNED', 'DELETE_INTENT', 'HANDOFF'])
        history = self.history(obj)
        self.assertEqual(history['terminal_diagnostic']['reason'], 'DELETE_NOT_SENT')
        self.assertEqual(history['receipt']['phase'], 'DELETE_INTENT')

    def _clock_changes_after_intent(self, now):
        obj = self.owned()
        self.queue.append(self.response(200, self.body))
        original = obj._session.plan_delete
        def after_intent(**kwargs):
            result = original(**kwargs)
            self.now = now
            return result
        with patch.object(obj._session, 'plan_delete', side_effect=after_intent):
            self.assertEqual(obj.cleanup(trigger='expiry')['reason'], 'DELETE_NOT_SENT')
        self.assertEqual([r['method'] for r in self.calls], ['POST', 'GET'])
        self.assertEqual([e['event'] for e in self.events()],
                         ['CREATE_INTENT', 'OWNED', 'DELETE_INTENT', 'HANDOFF'])
        self.assertEqual(self.history(obj)['terminal_diagnostic']['reason'], 'DELETE_NOT_SENT')

    def test_deadline_after_intent_before_send_has_no_delete_result(self):
        self._clock_changes_after_intent(30)

    def test_clock_reversal_after_intent_before_send_has_no_delete_result(self):
        self._clock_changes_after_intent(19)

    def test_late_delete_response_is_uncertain_after_transport(self):
        obj = self.owned()
        def late(_):
            self.now = 24
            return self.response(202, {'kind':'Status'})
        self.queue.extend([self.response(200, self.body), late])
        self.assertEqual(obj.cleanup(trigger='expiry')['reason'], 'REQUEST_WINDOW_EXPIRED')
        self.assertEqual([r['method'] for r in self.calls], ['POST', 'GET', 'DELETE'])
        self.assertEqual(self.events()[-2]['event'], 'DELETE_RESULT')
        self.assertIsNone(self.events()[-2]['payload']['http_status'])
        self.assertEqual(self.history(obj)['receipt']['phase'], 'DELETE_UNKNOWN')

    def test_transport_cannot_claim_that_delete_was_not_sent(self):
        obj = self.owned()
        self.queue.extend([self.response(200, self.body), c.RequestNotSent('DELETE_NOT_SENT')])
        self.assertEqual(obj.cleanup(trigger='expiry')['reason'], 'TRANSPORT_UNCERTAIN')
        self.assertEqual([r['method'] for r in self.calls], ['POST', 'GET', 'DELETE'])
        self.assertEqual(self.events()[-2]['event'], 'DELETE_RESULT')
        self.assertEqual(self.history(obj)['receipt']['phase'], 'DELETE_UNKNOWN')

    def test_timeout_after_delete_is_terminal_without_resend(self):
        obj = self.owned()
        self.queue.extend([self.response(200, self.body), TimeoutError('PRIVATE transport')])
        self.assertEqual(obj.cleanup(trigger='expiry')['action'], 'HANDOFF')
        with self.assertRaises(c.CoordinatorError): obj.cleanup(trigger='expiry')
        self.assertEqual([r['method'] for r in self.calls].count('DELETE'), 1)
        self.assertIn('DELETE_RESULT', [e['event'] for e in self.events()])

    def test_ack_with_present_object_is_handoff_not_resend(self):
        obj = self.owned()
        self.queue.extend([self.response(200, self.body), self.response(202, {'kind':'Status'}),
                           self.response(200, self.body)])
        obj.cleanup(trigger='expiry')
        self.assertEqual(obj.cleanup(trigger='expiry')['reason'], 'DELETE_ALREADY_CLAIMED_OR_OUTCOME_UNRESOLVED')
        self.assertEqual([r['method'] for r in self.calls].count('DELETE'), 1)

    def test_delete_404_is_unknown_until_explicit_readback(self):
        obj = self.owned()
        self.queue.extend([self.response(200, self.body), self.response(404, self.not_found),
                           self.response(404, self.not_found)])
        self.assertEqual(obj.cleanup(trigger='expiry')['action'], 'READBACK_REQUIRED')
        self.assertEqual(obj.cleanup(trigger='expiry')['action'], 'ABSENT')

    def test_deadline_expired_has_no_get_or_delete(self):
        obj = self.owned()
        self.now = 30
        self.assertEqual(obj.cleanup(trigger='expiry')['reason'], 'REQUEST_WINDOW_EXPIRED')
        self.assertEqual(len(self.calls), 1)

    def test_fork_or_concurrent_call_has_no_request(self):
        obj = self.make()
        with patch.object(c.os, 'getpid', return_value=obj._pid+1):
            with self.assertRaises(c.CoordinatorError): obj.create()
        obj._guard.acquire()
        try:
            with self.assertRaises(c.CoordinatorError): obj.create()
        finally: obj._guard.release()
        self.assertEqual(self.calls, [])

    def test_legacy_terminal_record_remains_read_only(self):
        obj = self.owned()
        self.queue.append(self.response(404, self.not_found))
        obj.cleanup(trigger='expiry')
        obj.close()
        rows = self.events()
        rows[-1]['payload'] = {}  # SYNTHETIC PR82 bytes, never rewrite actual records.
        (self.path / j.JOURNAL).write_bytes(b''.join(json.dumps(r).encode()+b'\n' for r in rows))
        history = j.inspect_history(self.fd)
        self.assertIsNone(history['terminal_diagnostic'])
        self.assertFalse(history['automatic_replay_allowed'])


if __name__ == '__main__':
    unittest.main()
