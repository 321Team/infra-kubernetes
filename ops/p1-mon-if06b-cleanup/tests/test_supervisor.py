"""Guardian loop, outcome record and watcher: SYNTHETIC transport, real Linux journal."""
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
import supervisor as s
import transport as t


class PureTests(unittest.TestCase):
    def test_failure_codes_never_carry_exception_text(self):
        self.assertEqual(s.code(c.CoordinatorError('CLUSTER_CHANGED')), 'CLUSTER_CHANGED')
        self.assertEqual(s.code(t.TransportError('CA_PIN_MISMATCH')), 'CA_PIN_MISMATCH')
        self.assertEqual(s.code(c.CoordinatorError('token=PRIVATE')), 'UNEXPECTED_FAILURE')
        self.assertEqual(s.code(FileExistsError('/private/path')), 'SESSION_FILES_EXIST')
        self.assertEqual(s.code(TimeoutError('PRIVATE endpoint')), 'TIMEOUT')
        self.assertEqual(s.code(ConnectionRefusedError('PRIVATE endpoint')), 'OS_ERROR')
        self.assertEqual(s.code(KeyError('PRIVATE')), 'UNEXPECTED_FAILURE')

    def test_explicit_not_found_matches_the_decision_rule(self):
        good = dict(apiVersion='v1', kind='Status', reason='NotFound', code=404,
                    details=dict(name=d.NAME, group=d.GROUP, kind=d.RESOURCE))
        self.assertTrue(s.explicit_not_found(404, good))
        for bad in (dict(good, details=dict(good['details'], name='other')), dict(good, reason='Forbidden'),
                    dict(good, code='404'), {k: v for k, v in good.items() if k != 'details'}, {}):
            self.assertFalse(s.explicit_not_found(404, bad))
        self.assertFalse(s.explicit_not_found(403, good))


class Wire:
    """Synthetic transport with the real one's `last` attribute."""
    def __init__(self):
        self.calls, self.queue = [], []
        self.last = dict(method=None, stage='NONE', http_status=None)

    def __call__(self, **request):
        self.calls.append(request)
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item(request) if callable(item) else item


@unittest.skipUnless(sys.platform == 'linux', 'Linux real journal integration')
class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path, self.proof = Path(self.temp.name) / 'journal', Path(self.temp.name) / 'evidence'
        self.fd, self.proof_fd = self.private(self.path), self.private(self.proof)
        self.binding = dict(session_id='SYNTHETIC-session', cluster_uid='SYNTHETIC-cluster',
                            host_boot_id='SYNTHETIC-boot', guardian_epoch='SYNTHETIC-epoch')
        self.manifest = dict(apiVersion=d.API, kind='GlobalNetworkPolicy', metadata=dict(name=d.NAME),
                             spec=dict(selector='synthetic == "test"', types=['Egress']))
        self.body = copy.deepcopy(self.manifest)
        self.body['metadata'].update(uid='SYNTHETIC-uid', resourceVersion='opaque-original')
        self.not_found = dict(apiVersion='v1', kind='Status', reason='NotFound', code=404,
                              details=dict(name=d.NAME, group=d.GROUP, kind=d.RESOURCE))
        self.namespace = dict(apiVersion='v1', kind='Namespace',
                              metadata=dict(name='kube-system', uid='SYNTHETIC-cluster'))
        self.wire = Wire()
        self.calls, self.queue = self.wire.calls, self.wire.queue
        self.now, self.waits, self.pauses, self.trigger = 100, [], [], None

    def private(self, path):
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        return fd

    def response(self, status, body):
        return status, json.dumps(body).encode()

    def wait(self, timeout):
        self.waits.append(timeout)
        if isinstance(self.trigger, BaseException):
            raise self.trigger
        if self.trigger is None:
            self.now += timeout
        return self.trigger

    def pause(self, seconds):
        self.pauses.append(seconds)
        self.now += seconds

    def rows(self):
        return [json.loads(line) for line in (self.proof / s.STEPS).read_bytes().splitlines()]

    def events(self):
        return [json.loads(line)['event'] for line in (self.path / j.JOURNAL).read_bytes().splitlines()]

    def methods(self):
        return [r['method'] for r in self.calls]

    def guard(self, proof_fd=None, **changes):
        evidence = changes.pop('evidence', None) or s.Evidence(
            self.proof_fd if proof_fd is None else proof_fd, journal_fd=self.fd, clock=lambda: self.now)
        self.addCleanup(evidence.close)
        kwargs = dict(directory_fd=self.fd, evidence=evidence, binding=self.binding, manifest=self.manifest,
                      reviewed_manifest_sha256=c.manifest_hash(self.manifest),
                      reviewed_spec_sha256=d.spec_hash(self.body['spec']), lease=10, cleanup_window=20,
                      observation_max_age=2, request_timeout=3, readback_delay=1, clock=lambda: self.now,
                      binding_probe=lambda: copy.deepcopy(self.binding), transport=self.wire,
                      trigger_wait=self.wait, pause=self.pause)
        kwargs.update(changes)
        return s.guard(**kwargs)

    def normal(self, delete=None):
        self.queue.extend([self.response(201, self.body), self.response(200, self.body),
                           delete or self.response(200, {'kind': 'Status'}), self.response(404, self.not_found)])

    def test_lease_expiry_reclaims_once_and_reads_back(self):
        self.normal()
        final = self.guard()
        self.assertEqual(final['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
        self.assertFalse(final['baseline_verified'])
        self.assertFalse(final['automatic_replay_allowed'])
        self.assertTrue(final['evidence_complete'])
        self.assertEqual(self.methods(), ['POST', 'GET', 'DELETE', 'GET'])
        self.assertEqual(self.calls[2]['body']['preconditions'],
                         dict(uid='SYNTHETIC-uid', resourceVersion='opaque-original'))
        self.assertEqual((self.waits, self.pauses), ([10], [1]))
        self.assertEqual([r['step'] for r in self.rows()],
                         ['ARMING', 'ARMED', 'CREATE', 'TRIGGER', 'CLEANUP', 'READBACK', 'FINAL'])
        self.assertEqual(self.rows()[3]['trigger'], 'expiry')
        self.assertEqual(self.rows()[2]['uid'], 'SYNTHETIC-uid')
        self.assertIs(self.rows()[-1]['evidence_complete_before_write'], True)
        self.assertTrue(all('evidence_complete' not in row for row in self.rows()))  # Never self-attested.
        self.assertEqual(self.events(), ['CREATE_INTENT', 'OWNED', 'DELETE_INTENT', 'DELETE_RESULT', 'ABSENT'])
        watched = s.watch(self.fd, poll=1, until_mono=0, clock=lambda: self.now, pause=self.pause)
        self.assertEqual((watched['state'], watched['handoff_required']), ('ABSENT_BASELINE_UNVERIFIED', False))

    def test_operator_triggers_reclaim_before_expiry(self):
        for trigger in ('abort', 'complete'):
            with self.subTest(trigger=trigger):
                self.setUp()
                self.trigger = trigger
                self.normal()
                self.assertEqual(self.guard()['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
                self.assertEqual(self.rows()[3]['trigger'], trigger)
                self.assertLess(self.now, 110)
                self.assertEqual(self.methods(), ['POST', 'GET', 'DELETE', 'GET'])

    def test_broken_wait_reclaims_instead_of_outliving_the_lease(self):
        self.trigger = RuntimeError('PRIVATE')
        self.normal()
        self.assertEqual(self.guard()['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
        self.assertEqual(self.rows()[3]['trigger'], 'abort')

    def test_early_wakeup_does_not_read_or_delete_before_expiry(self):
        def early(timeout):
            self.waits.append(timeout)
            self.now += timeout / 2 if len(self.waits) == 1 else timeout
            return None
        self.normal()
        self.assertEqual(self.guard(trigger_wait=early)['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
        self.assertEqual(self.waits, [10, 5])
        self.assertEqual(self.methods().count('GET'), 2)

    def test_changed_resource_version_is_the_delete_precondition(self):
        newer = copy.deepcopy(self.body)
        newer['metadata']['resourceVersion'] = 'opaque-new'
        self.queue.extend([self.response(201, self.body), self.response(200, newer),
                           self.response(202, {'kind': 'Status'}), self.response(404, self.not_found)])
        self.guard()
        self.assertEqual(self.calls[2]['body']['preconditions'],
                         dict(uid='SYNTHETIC-uid', resourceVersion='opaque-new'))

    def test_forbidden_or_conflicting_delete_is_handoff_without_resend(self):
        for status, phase in ((403, 'DELETE_UNKNOWN'), (409, 'CONFLICT')):
            with self.subTest(status=status):
                self.setUp()
                self.queue.extend([self.response(201, self.body), self.response(200, self.body),
                                   self.response(status, {'kind': 'Status'}), self.response(200, self.body)])
                final = self.guard()
                self.assertEqual((final['disposition'], final['reason']),
                                 ('HANDOFF', 'DELETE_ALREADY_CLAIMED_OR_OUTCOME_UNRESOLVED'))
                self.assertTrue(final['terminal_recorded'])
                self.assertEqual(self.methods(), ['POST', 'GET', 'DELETE', 'GET'])
                self.assertEqual(self.rows()[4]['http_status'], status)
                watched = s.watch(self.fd, poll=1, until_mono=0, clock=lambda: self.now, pause=self.pause)
                self.assertEqual((watched['state'], watched['phase'], watched['uid']),
                                 ('HANDOFF', phase, 'SYNTHETIC-uid'))

    def test_delete_ack_is_not_absence(self):
        self.queue.extend([self.response(201, self.body), self.response(200, self.body),
                           self.response(200, {'kind': 'Status'}), self.response(503, {'kind': 'Status'})])
        final = self.guard()
        self.assertEqual((final['disposition'], final['reason']), ('HANDOFF', 'API_READ_FAILED'))
        self.assertEqual(self.methods().count('DELETE'), 1)

    def test_lost_delete_response_is_uncertain_and_terminal(self):
        self.queue.extend([self.response(201, self.body), self.response(200, self.body),
                           TimeoutError('PRIVATE endpoint')])
        self.wire.last = dict(method='DELETE', stage='READ', http_status=None)
        final = self.guard()
        self.assertEqual((final['disposition'], final['reason']), ('HANDOFF', 'TRANSPORT_UNCERTAIN'))
        self.assertEqual(final['transport_last'], dict(method='DELETE', stage='READ', http_status=None))
        self.assertEqual(self.methods(), ['POST', 'GET', 'DELETE'])
        self.assertEqual(self.events()[-2:], ['DELETE_RESULT', 'HANDOFF'])
        self.assertNotIn(b'PRIVATE', (self.proof / s.STEPS).read_bytes())

    def test_foreign_uid_or_drift_never_deletes(self):
        for field, value, reason in (('uid', 'foreign', 'FOREIGN_UID'), ('spec', None, 'POLICY_DRIFT')):
            with self.subTest(reason=reason):
                self.setUp()
                changed = copy.deepcopy(self.body)
                if field == 'uid':
                    changed['metadata']['uid'] = value
                else:
                    changed['spec']['selector'] = 'changed'
                self.queue.extend([self.response(201, self.body), self.response(200, changed)])
                self.assertEqual(self.guard()['reason'], reason)
                self.assertEqual(self.methods(), ['POST', 'GET'])

    def test_explicit_not_found_before_delete_is_absent_without_delete(self):
        self.queue.extend([self.response(201, self.body), self.response(404, self.not_found)])
        self.assertEqual(self.guard()['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
        self.assertEqual(self.methods(), ['POST', 'GET'])

    def test_not_sent_cause_separates_generation_from_time(self):
        def expire():
            self.now = 110
        final = self.guard(on_armed=expire)
        self.assertEqual((final['reason'], final['not_sent_cause']), ('CREATE_NOT_SENT', 'REQUEST_WINDOW_EXPIRED'))
        self.assertEqual(self.calls, [])
        self.setUp()
        final = self.guard(on_armed=lambda: self.binding.update(host_boot_id='rebooted'))
        self.assertEqual((final['reason'], final['not_sent_cause']), ('CREATE_NOT_SENT', 'GENERATION_CHANGED'))
        self.assertEqual(self.events(), ['CREATE_INTENT', 'HANDOFF'])
        self.assertNotIn(b'GENERATION_CHANGED', (self.path / j.JOURNAL).read_bytes())  # Return value only.

    def test_generation_change_after_intent_reports_delete_not_sent_cause(self):
        self.queue.extend([self.response(201, self.body), self.response(200, self.body)])
        sync = os.fsync

        def change(fd):
            sync(fd)
            if (self.path / j.JOURNAL).exists() and self.events()[-1] == 'DELETE_INTENT':
                self.binding['guardian_epoch'] = 'changed'
        with patch.object(os, 'fsync', side_effect=change):
            final = self.guard()
        self.assertEqual((final['reason'], final['not_sent_cause']), ('DELETE_NOT_SENT', 'GENERATION_CHANGED'))
        self.assertEqual(self.methods(), ['POST', 'GET'])
        self.assertEqual(self.rows()[4]['not_sent_cause'], 'GENERATION_CHANGED')

    def _fail_sync_of(self, *paths):
        inodes, sync = {os.stat(p).st_ino for p in paths}, os.fsync

        def selective(fd):
            if os.fstat(fd).st_ino in inodes:
                raise OSError('PRIVATE disk failure')
            sync(fd)
        return patch.object(os, 'fsync', side_effect=selective)

    def test_unrecorded_handoff_is_preserved_outside_the_journal(self):
        self.queue.extend([self.response(201, self.body), self.response(200, self.body)])
        state = {}

        def created(request):
            state['patch'] = self._fail_sync_of(self.path / j.JOURNAL)
            state['patch'].start()
            self.addCleanup(state['patch'].stop)
            return self.response(200, self.body)
        self.queue[1] = created
        final = self.guard()
        state['patch'].stop()
        self.assertEqual(final['disposition'], 'HANDOFF')
        self.assertFalse(final['terminal_recorded'])
        self.assertTrue(final['evidence_complete'])
        self.assertEqual(self.methods(), ['POST', 'GET'])  # Intent was not durable: no DELETE.
        self.assertEqual(self.rows()[-1]['terminal_recorded'], False)
        self.assertNotIn('HANDOFF', self.events())
        self.assertNotIn(b'PRIVATE', (self.proof / s.STEPS).read_bytes())

    def _break_final(self, mode):
        """Fail only the FINAL row of the evidence file: 'write', 'short' or 'fsync'."""
        write, sync, inode, hit = os.write, os.fsync, os.stat(self.proof / s.STEPS).st_ino, []

        def fake_write(fd, data):
            if b'"step":"FINAL"' in bytes(data) and os.fstat(fd).st_ino == inode:
                hit.append(mode)
                if mode == 'write':
                    raise OSError('PRIVATE disk failure')
                if mode == 'short':
                    return write(fd, bytes(data)[:-7])
            return write(fd, data)

        def fake_sync(fd):
            if mode == 'fsync' and hit and os.fstat(fd).st_ino == inode:
                raise OSError('PRIVATE disk failure')
            sync(fd)
        return patch.object(os, 'write', side_effect=fake_write), patch.object(os, 'fsync', side_effect=fake_sync), hit

    def test_final_row_cannot_vouch_for_its_own_failed_write(self):
        for mode in ('write', 'short', 'fsync'):
            with self.subTest(mode=mode):
                self.setUp()
                self.normal()
                read_fd, write_fd = os.pipe()
                self.addCleanup(os.close, read_fd)
                evidence = s.Evidence(self.proof_fd, journal_fd=self.fd, clock=lambda: self.now, stream_fd=write_fd)
                writes, syncs, hit = self._break_final(mode)
                with writes, syncs:
                    final = self.guard(evidence=evidence)
                evidence.close()
                streamed = [json.loads(line) for line in os.read(read_fd, 65536).splitlines()]
                self.assertEqual(hit, [mode])
                # The reclaim itself is untouched and judged separately from the evidence.
                self.assertEqual(final['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
                self.assertEqual(self.events()[-1], 'ABSENT')
                self.assertEqual(self.methods(), ['POST', 'GET', 'DELETE', 'GET'])
                self.assertIs(final['evidence_complete_before_write'], True)
                self.assertIs(final['evidence_complete'], False)
                self.assertEqual(streamed[-1], final)  # The external copy reports this row's failure.
                self.assertTrue(all(row['evidence_complete'] is True for row in streamed[:-1]))
                stored = (self.proof / s.STEPS).read_bytes()
                self.assertNotIn(b'"evidence_complete":', stored)
                self.assertNotIn(b'PRIVATE', stored)
                if mode == 'write':
                    self.assertNotIn(b'"step":"FINAL"', stored)
                elif mode == 'short':
                    self.assertFalse(stored.endswith(b'\n'))
                    with self.assertRaises(ValueError):
                        json.loads(stored.splitlines()[-1])
                else:
                    self.assertIs(json.loads(stored.splitlines()[-1])['evidence_complete_before_write'], True)

    def test_failure_before_final_is_carried_into_the_final_row(self):
        self.normal()
        evidence = s.Evidence(self.proof_fd, journal_fd=self.fd, clock=lambda: self.now)
        first = evidence.write('PROBE')
        with self._fail_sync_of(self.proof / s.STEPS):
            second = evidence.write('PROBE')
        final = self.guard(evidence=evidence)
        self.assertEqual((first['evidence_complete'], second['evidence_complete']), (True, False))
        self.assertEqual((final['evidence_complete_before_write'], final['evidence_complete']), (False, False))
        self.assertIs(self.rows()[-1]['evidence_complete_before_write'], False)  # Later successes do not restore it.
        self.assertEqual(final['disposition'], 'ABSENT_BASELINE_UNVERIFIED')

    def test_evidence_failure_does_not_stop_the_reclaim(self):
        self.normal()
        evidence = s.Evidence(self.proof_fd, journal_fd=self.fd, clock=lambda: self.now)
        self.addCleanup(evidence.close)
        with self._fail_sync_of(self.proof / s.STEPS):
            final = self.guard(evidence=evidence)
        self.assertEqual(final['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
        self.assertFalse(final['evidence_complete'])
        self.assertEqual(self.events()[-1], 'ABSENT')

    def test_late_or_drifting_create_response_is_diagnostic_only(self):
        def late(_):
            self.now = 104  # Past the request deadline 103, before lease expiry.
            return self.response(201, self.body)
        drift = copy.deepcopy(self.body)
        drift['spec']['selector'] = 'unexpected'
        for item, reason in ((late, 'REQUEST_WINDOW_EXPIRED'), (self.response(201, drift), 'CREATE_SPEC_DRIFT')):
            with self.subTest(reason=reason):
                self.setUp()
                self.queue.append(item)
                final = self.guard()
                self.assertEqual((final['disposition'], final['reason']), ('HANDOFF', reason))
                self.assertEqual(final['unowned_create_response'],
                                 dict(http_status=201, uid='SYNTHETIC-uid', resource_version='opaque-original',
                                      received_mono=self.now, use='DIAGNOSTIC_ONLY_NOT_OWNERSHIP'))
                self.assertEqual(self.methods(), ['POST'])  # Never adopted, read back or deleted.
                self.assertEqual(self.events(), ['CREATE_INTENT', 'HANDOFF'])
                watched = s.watch(self.fd, poll=1, until_mono=0, clock=lambda: self.now, pause=self.pause)
                self.assertIsNone(watched['uid'])

    def test_conflict_or_lost_create_has_no_identity_to_report(self):
        for item in (self.response(409, {'kind': 'Status', 'reason': 'AlreadyExists'}), TimeoutError('PRIVATE')):
            with self.subTest(item=type(item)):
                self.setUp()
                self.queue.append(item)
                final = self.guard()
                self.assertEqual(final['disposition'], 'HANDOFF')
                uid = (final['unowned_create_response'] or {}).get('uid')
                self.assertIsNone(uid)
                self.assertEqual(self.methods(), ['POST'])

    def test_used_session_directory_is_never_restarted(self):
        self.normal()
        self.guard()
        again = self.private(Path(self.temp.name) / 'evidence-2')
        final = self.guard(proof_fd=again)
        self.assertEqual((final['disposition'], final['reason'], final['create_sent']),
                         ('NOT_STARTED', 'SESSION_FILES_EXIST', False))
        self.assertEqual(len(self.calls), 4)

    def test_budgets_must_fit_and_have_no_defaults(self):
        for index, changes in enumerate((dict(cleanup_window=10), dict(lease=3), dict(request_timeout=0),
                                         dict(readback_delay=-1), dict(observation_max_age=None))):
            with self.subTest(changes=changes):
                proof = self.private(Path(self.temp.name) / ('evidence-b%d' % index))
                final = self.guard(proof_fd=proof, **changes)
                self.assertEqual((final['disposition'], final['reason']), ('NOT_STARTED', 'INPUT_INVALID'))
        self.assertFalse((self.path / j.JOURNAL).exists())
        self.assertEqual(self.calls, [])

    def test_evidence_must_not_share_the_journal_directory(self):
        with self.assertRaisesRegex(j.JournalError, 'EVIDENCE_DIRECTORY_NOT_INDEPENDENT'):
            s.Evidence(self.fd, journal_fd=self.fd, clock=lambda: self.now)
        with self.assertRaises(FileExistsError):
            s.Evidence(self.proof_fd, journal_fd=self.fd, clock=lambda: self.now).close()
            s.Evidence(self.proof_fd, journal_fd=self.fd, clock=lambda: self.now)

    def test_watch_reports_a_writer_that_left_without_a_terminal_record(self):
        empty = s.watch(self.fd, poll=1, until_mono=0, clock=lambda: self.now, pause=self.pause)
        self.assertEqual((empty['state'], empty['handoff_required']), ('NO_SESSION_FILES', True))
        self.queue.append(self.response(201, self.body))
        obj = c.Coordinator(self.fd, binding=self.binding, manifest=self.manifest,
                            reviewed_manifest_sha256=c.manifest_hash(self.manifest),
                            reviewed_spec_sha256=d.spec_hash(self.body['spec']), expires_mono=110,
                            cleanup_deadline_mono=130, observation_max_age=2, request_timeout=3,
                            clock=lambda: self.now, binding_probe=lambda: copy.deepcopy(self.binding),
                            transport=self.wire)
        obj.create()
        held = s.watch(self.fd, poll=5, until_mono=112, clock=lambda: self.now, pause=self.pause)
        self.assertEqual((held['state'], held['handoff_required']), ('WRITER_STILL_HOLDING_AFTER_WINDOW', True))
        self.assertEqual(self.pauses, [5, 5, 5])
        obj.close()  # Stands in for a guardian that died while it owned the canary.
        gone = s.watch(self.fd, poll=1, until_mono=0, clock=lambda: self.now, pause=self.pause)
        self.assertEqual((gone['state'], gone['handoff_required'], gone['last_event'], gone['uid'], gone['phase']),
                         ('WRITER_GONE_WITHOUT_TERMINAL', True, 'OWNED', 'SYNTHETIC-uid', 'OWNED'))
        self.assertFalse(gone['automatic_replay_allowed'])
        self.assertEqual(self.methods(), ['POST'])
        with open(self.path / j.JOURNAL, 'ab') as handle:
            handle.write(b'{"seq":9}\n')
        broken = s.watch(self.fd, poll=1, until_mono=0, clock=lambda: self.now, pause=self.pause)
        self.assertEqual((broken['state'], broken['handoff_required']), ('HISTORY_UNREADABLE', True))

    def test_start_gate_requires_reviewed_cluster_and_explicit_absence(self):
        def gate(*items):
            self.calls.clear()
            self.queue[:] = [self.response(*item) for item in items]
            s.check_target(self.wire, expected_cluster_uid='SYNTHETIC-cluster', clock=lambda: self.now, request_timeout=3)
        gate((200, self.namespace), (404, self.not_found))
        self.assertEqual([(r['method'], r['path'], r['deadline_mono']) for r in self.calls],
                         [('GET', t.NAMESPACE, 103), ('GET', d.PATH, 103)])
        other = copy.deepcopy(self.namespace)
        other['metadata']['uid'] = 'another-cluster'
        wrong = copy.deepcopy(self.not_found)
        wrong['details']['name'] = 'other'
        for items, reason in ((((200, other),), 'CLUSTER_CHANGED'), (((403, {'kind': 'Status'}),), 'CLUSTER_READ_FAILED'),
                              (((200, self.namespace), (200, self.body)), 'CANARY_ALREADY_PRESENT'),
                              (((200, self.namespace), (404, wrong)), 'CANARY_ABSENCE_UNVERIFIED'),
                              (((200, self.namespace), (403, {'kind': 'Status'})), 'CANARY_ABSENCE_UNVERIFIED')):
            with self.subTest(reason=reason), self.assertRaisesRegex(c.CoordinatorError, '^%s$' % reason):
                gate(*items)

    def preflight(self, *items, pin=None):
        self.calls.clear()
        self.queue[:] = [self.response(*item) for item in items]
        return s.preflight(self.wire, expected_cluster_uid='SYNTHETIC-cluster', manifest=self.manifest,
                           reviewed_spec_sha256=pin or d.spec_hash(self.body['spec']),
                           clock=lambda: self.now, request_timeout=3)

    def test_preflight_persists_nothing_and_pins_the_defaulted_spec(self):
        allowed, denied = {'status': {'allowed': True}}, {'status': {'allowed': False}}
        dry = copy.deepcopy(self.body)
        del dry['metadata']['resourceVersion']
        start = [(200, self.namespace), (404, self.not_found)]
        result = self.preflight(*start, (201, allowed), (201, allowed), (201, allowed), (201, dry))
        self.assertTrue(result['passed'])
        self.assertEqual(result['defaulted_spec_sha256'], d.spec_hash(self.body['spec']))
        self.assertEqual([(r['method'], r['path']) for r in self.calls],
                         [('GET', t.NAMESPACE), ('GET', d.PATH)] + [('POST', t.ACCESS_REVIEW)] * 3 + [('POST', t.DRY_RUN)])
        verbs = [r['body']['spec']['resourceAttributes'] for r in self.calls[2:5]]
        self.assertEqual([(v['verb'], v.get('name')) for v in verbs],
                         [('create', None), ('get', d.NAME), ('delete', d.NAME)])
        result = self.preflight(*start, (201, allowed), (201, allowed), (201, denied), (201, dry))
        self.assertEqual((result['passed'], result['checks']['access_delete']), (False, 'FAIL'))
        defaulted = copy.deepcopy(dry)
        defaulted['spec']['tier'] = 'default'
        result = self.preflight(*start, (201, allowed), (201, allowed), (201, allowed), (201, defaulted))
        self.assertEqual((result['passed'], result['checks']['dry_run_spec_pin']), (False, 'FAIL'))
        self.assertEqual(result['defaulted_spec_sha256'], d.spec_hash(defaulted['spec']))
        other = copy.deepcopy(self.namespace)
        other['metadata']['uid'] = 'another-cluster'
        result = self.preflight((200, other))
        self.assertEqual(result['checks'], dict(cluster_and_absence='CLUSTER_CHANGED', access_create='NOT_RUN',
                                                access_get='NOT_RUN', access_delete='NOT_RUN',
                                                dry_run_spec_pin='NOT_RUN'))
        self.assertEqual(len(self.calls), 1)  # No POST reaches an unverified cluster.


if __name__ == '__main__':
    unittest.main()
