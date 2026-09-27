"""R1 regression: synthetic approvals and clocks; no network or host commands."""
import argparse
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_cp_canary import n, c, FakeBackend, MemoryJournal


class Clock:
    def __init__(self):
        self.wall = dt.datetime(2026, 9, 14, 0, 59, 59, tzinfo=dt.timezone.utc)
        self.elapsed = 100.0
        clock = self

        class DateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.wall.astimezone(tz) if tz else clock.wall.replace(tzinfo=None)
        self.datetime = DateTime

    def advance(self, seconds, wall_seconds=None):
        self.elapsed += seconds
        self.wall += dt.timedelta(seconds=seconds if wall_seconds is None else wall_seconds)


class DiskJournal(MemoryJournal):
    """File-backed test journal; production ownership/fsync checks are separate."""
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.path.touch()

    def save(self, receipt):
        super().save(receipt)
        self.path.write_bytes(n.encoded(receipt))

    def load(self, expected):
        raw = self.path.read_bytes()
        n.need(n.sha(raw) == expected, 'receipt checksum mismatch')
        return json.loads(raw)


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        for target, name, value in ((n.dt, 'datetime', self.clock.datetime),
                                    (n.time, 'monotonic', lambda: self.clock.elapsed)):
            patcher = patch.object(target, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        for target in ('subprocess.run', 'socket.socket'):
            patcher = patch(target, side_effect=AssertionError('unexpected real host/network I/O'))
            patcher.start(); self.addCleanup(patcher.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.journal = DiskJournal(self.root / 'receipt.json')
        self.backend = FakeBackend(self.journal)
        self.backend.identity = lambda: None
        self.backend.cache = type('Cache', (), {'cleanup': lambda self: None})()
        self.auth = json.loads((c.KIT / 'policies/icr-d-net-01-01/cp1-runtime-authorization.template.json').read_bytes())
        self.auth.update(source_commit='b' * 40, mode='apply', input_sha256='a' * 64,
                         review_decision='APPROVED',  # Synthetic fixture, not a real review.
                         review_url='https://github.com/321Team/infra-kubernetes/pull/2#pullrequestreview-0',
                         execution_decision_url='https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-0',
                         protection_review_sha256='d' * 64, rollback_plan_sha256='e' * 64,
                         window_start='2026-09-14T00:00:00+00:00', window_end='2026-09-14T01:00:00+00:00')
        for key in ('backup_available', 'console_recovery_available', 'maintenance_freeze',
                    'coexistence_risk_reviewed', 'no_join_before_runtime_acceptance'):
            self.auth[key] = True
        data = {'hostname': 'k8s-control01', 'canary_inputs_complete': True,
                'snapshot': self.backend.snapshot(), 'topology': self.backend.stable,
                'before_sha256': n.fingerprint(self.backend.state),
                'topology_sha256': n.fingerprint(self.backend.stable), 'created_at': n.now()}
        source = {'source_commit': 'b' * 40, 'source_sha256': c.digest(c.NODE_SOURCE),
                  'transport': 'PASS', 'result': data}
        self.input = self.root / 'inspection.json'
        self.input.write_bytes(n.encoded(source))
        self.auth['input_sha256'] = c.digest(self.input)
        self.auth_path = self.root / 'authorization.json'
        self.auth_path.write_text(json.dumps(self.auth, indent=4) + '\n\n')
        args = argparse.Namespace(mode='apply', input=self.input, input_sha256=c.digest(self.input),
                                  authorization=self.auth_path, authorization_sha256=c.digest(self.auth_path))
        self.request = c.request_for(args, 'b' * 40, '20260914T005959Z-abcdef123456')

    def invoke(self, request=None):
        request = self.request if request is None else request
        def backend_factory(node, window=None):
            self.backend.window = window
            return self.backend
        def journal_factory(run_id, new):
            self.journal.lock = os.open(self.root / 'lock', os.O_RDWR | os.O_CREAT, 0o600)
            return self.journal
        output = io.StringIO()
        with patch.object(n, 'Backend', side_effect=backend_factory), \
                patch.object(n, 'Journal', side_effect=journal_factory), contextlib.redirect_stdout(output):
            n.main(request)
        return json.loads(output.getvalue())

    def bind_auth(self, request, **updates):
        auth = json.loads(request['authorization_json'])
        auth.update(updates)
        request['authorization_json'] = n.encoded(auth).decode()
        request['authorization_sha256'] = n.sha(request['authorization_json'].encode())

    def rollback_request(self, result):
        request = dict(self.request, mode='rollback', receipt_sha256=result['receipt_sha256'], input_sha256='f' * 64)
        self.bind_auth(request, mode='rollback', input_sha256='f' * 64,
                       window_start=self.clock.wall.isoformat(),
                       window_end=(self.clock.wall + dt.timedelta(minutes=10)).isoformat())
        return request

    def test_controller_inspection_freshness_boundaries(self):
        for age in (901, 1800, 1800.001, -0.001):
            with self.subTest(age=age):
                data = json.loads(self.input.read_bytes())
                data['result']['created_at'] = (self.clock.wall - dt.timedelta(seconds=age)).isoformat()
                self.input.write_bytes(n.encoded(data))
                self.auth['input_sha256'] = c.digest(self.input)
                self.auth_path.write_bytes(n.encoded(self.auth))
                args = argparse.Namespace(mode='apply', input=self.input, input_sha256=c.digest(self.input),
                                          authorization=self.auth_path, authorization_sha256=c.digest(self.auth_path))
                if 0 <= age <= 1800:
                    request = c.request_for(args, 'b' * 40, self.request['run_id'])
                    self.assertEqual(request['inspection_created_at'], data['result']['created_at'])
                else:
                    with self.assertRaisesRegex(ValueError, '30 minutes or in future'):
                        c.request_for(args, 'b' * 40, self.request['run_id'])

    def test_remote_accepts_exact_thirty_minute_inspection(self):
        self.request['inspection_created_at'] = (self.clock.wall - dt.timedelta(seconds=1800)).isoformat()
        result = self.invoke()
        self.assertEqual(result['status'], 'RUNTIME_APPLIED_REVIEW_REQUIRED')
        self.assertEqual(len(self.backend.edits), 6)

    def test_remote_rejects_expired_future_and_naive_inspection_without_edits(self):
        candidates = [(self.clock.wall - dt.timedelta(seconds=1800.001)).isoformat(),
                      (self.clock.wall + dt.timedelta(seconds=0.001)).isoformat(),
                      self.clock.wall.replace(tzinfo=None).isoformat()]
        for created_at in candidates:
            with self.subTest(created_at=created_at):
                result = self.invoke(dict(self.request, inspection_created_at=created_at))
                self.assertEqual(result['status'], 'STOP')
                self.assertIn('inspection', result['error'])
                self.assertEqual(self.backend.edits, [])

    def test_inspection_expiring_in_transit_stops_before_mutation(self):
        self.request['inspection_created_at'] = (self.clock.wall - dt.timedelta(seconds=1799.5)).isoformat()
        n.validate_inspection_age(self.request['inspection_created_at'])
        self.clock.advance(0.75)  # The operation window is still open.
        result = self.invoke()
        self.assertEqual(result['status'], 'STOP')
        self.assertIn('30 minutes', result['error'])
        self.assertEqual(self.backend.edits, [])

    def test_thirty_minute_inspection_does_not_allow_state_drift(self):
        self.request['inspection_created_at'] = (self.clock.wall - dt.timedelta(seconds=1800)).isoformat()
        self.backend.state['planes']['runtime']['rules'].append('someone-elses-rule')
        result = self.invoke()
        self.assertEqual(result['status'], 'STOP')
        self.assertEqual(self.backend.edits, [])

    def test_exact_authorization_and_normal_six_runtime_operations(self):
        self.assertEqual(self.request['authorization_json'].encode(), self.auth_path.read_bytes())
        result = self.invoke()
        self.assertEqual(result['status'], 'RUNTIME_APPLIED_REVIEW_REQUIRED')
        self.assertEqual(len(self.backend.edits), 6)
        self.assertTrue(all(p == 'runtime' for p, _, _ in self.backend.edits))
        self.assertEqual(result['receipt']['authorization_sha256'], c.digest(self.auth_path))
        self.assertTrue(result['receipt']['authorization_window']['end_exclusive'])
        self.assertEqual(n.sha(self.journal.path.read_bytes()), result['receipt_sha256'])

    def test_expiry_in_transit_including_exact_end_has_zero_queries_or_writes(self):
        self.clock.advance(1)
        for offset in (0, 1):
            self.clock.advance(offset)
            with self.subTest(offset=offset), patch.object(n, 'Backend') as backend:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    n.main(self.request)
                self.assertEqual(json.loads(output.getvalue())['status'], 'STOP')
                backend.assert_not_called()
        self.assertEqual(self.backend.edits, [])

    def test_mid_apply_expiry_stops_later_writes_and_preserves_receipt(self):
        edit, starts = self.backend.edit, []
        def advancing_edit(*args):
            starts.append(self.clock.wall)
            result = edit(*args)
            self.clock.advance(1)
            return result
        with patch.object(self.backend, 'edit', side_effect=advancing_edit):
            result = self.invoke()
        self.assertEqual(result['status'], 'PARTIAL_APPLY_REVIEW_REQUIRED')
        self.assertEqual(len(starts), 1)  # Old helper started six, four strictly after end.
        self.assertLess(starts[0], dt.datetime.fromisoformat(self.auth['window_end']))
        self.assertEqual(result['receipt'], json.loads(self.journal.path.read_bytes()))
        self.assertIn('outside operation window', result['receipt']['error'])

    def test_expiry_after_durable_intent_prevents_pending_write(self):
        save = self.journal.save
        def expire_at_intent(receipt):
            save(receipt)
            if receipt['operations'] and receipt['operations'][-1]['status'] == 'add_pending':
                self.clock.advance(1)
        with patch.object(self.journal, 'save', side_effect=expire_at_intent):
            result = self.invoke()
        self.assertEqual(result['status'], 'PARTIAL_APPLY_REVIEW_REQUIRED')
        self.assertEqual(self.backend.edits, [])
        self.assertEqual(result['receipt']['operations'][0]['status'], 'add_pending')

    def test_expired_confirmation_retains_result_without_claiming_ownership(self):
        query, edit = self.backend.query, self.backend.edit
        def guarded_query(*args):
            self.backend.window.remaining()
            return query(*args)
        def edit_then_expire(*args):
            result = edit(*args)
            self.clock.advance(1)
            return result
        with patch.object(self.backend, 'query', side_effect=guarded_query), \
                patch.object(self.backend, 'edit', side_effect=edit_then_expire):
            result = self.invoke()
        op = json.loads(self.journal.path.read_bytes())['operations'][0]
        self.assertEqual(result['status'], 'PARTIAL_APPLY_REVIEW_REQUIRED')
        self.assertEqual((op['status'], op['result']['rc']), ('add_uncertain', 0))
        self.assertEqual(len(self.backend.edits), 1)
        self.clock.advance(1)
        result = self.invoke(self.rollback_request(result))
        self.assertEqual(result['status'], 'STOP')
        self.assertIn('ambiguous', result['error'])
        self.assertEqual(len(self.backend.edits), 1)

    def test_tampered_or_misbound_authorization_rejected_before_backend(self):
        candidates = []
        for field, value in (('authorization_json', self.request['authorization_json'] + ' '),
                             ('source_commit', 'c' * 40), ('mode', 'rollback'),
                             ('input_sha256', 'f' * 64), ('node', 'k8s-control02')):
            candidates.append(dict(self.request, **{field: value}))
        for field, value in (('review_decision', 'COMMENTED'), ('backup_available', False),
                             ('execution_plan_sha256', 'f' * 64),
                             ('window_start', '2026-09-14T00:00:00'),
                             ('window_end', '2026-09-14T05:00:00+00:00')):
            request = dict(self.request)
            self.bind_auth(request, **{field: value})
            candidates.append(request)
        for index, request in enumerate(candidates):
            with self.subTest(index=index), patch.object(n, 'Backend') as backend:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    n.main(request)
                self.assertEqual(json.loads(output.getvalue())['status'], 'STOP')
                backend.assert_not_called()

    def test_backward_wall_clock_step_cannot_extend_elapsed_budget(self):
        window = n.authorization_window(self.request)
        self.clock.advance(1, wall_seconds=-1)
        with self.assertRaisesRegex(ValueError, 'outside operation window'):
            window.remaining()

    def test_forward_wall_clock_jump_stops_before_elapsed_budget(self):
        window = n.authorization_window(self.request)
        self.clock.advance(0.1, wall_seconds=2)
        with self.assertRaisesRegex(ValueError, 'outside operation window'):
            window.remaining()

    def test_backend_timeout_is_bounded_and_outcome_remains_uncertain(self):
        backend = n.Backend('k8s-control01', window=n.authorization_window(self.request))
        self.addCleanup(backend.cache.cleanup)
        def timeout(argv, **kwargs):
            self.assertGreater(kwargs['timeout'], 0)
            self.assertLessEqual(kwargs['timeout'], 1)
            self.clock.advance(kwargs['timeout'])
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        with patch.object(n.subprocess, 'run', side_effect=timeout) as run:
            self.assertEqual(backend.edit('runtime', n.RULES[0], 'add')['rc'], 124)
            with self.assertRaisesRegex(ValueError, 'outside operation window'):
                backend.query('runtime', n.RULES[0])
            self.assertEqual(run.call_count, 1)

    def test_separate_rollback_mode_input_window_then_successful_restore(self):
        result = self.invoke()
        self.clock.advance(2)
        invalid = dict(self.request, mode='rollback', receipt_sha256=result['receipt_sha256'])
        self.assertEqual(self.invoke(invalid)['status'], 'STOP')
        self.assertEqual(len(self.backend.edits), 6)
        request = self.rollback_request(result)
        rolled = self.invoke(request)
        self.assertEqual(rolled['status'], 'ROLLED_BACK_REVIEW_REQUIRED')
        self.assertEqual(len(self.backend.edits), 12)
        self.assertEqual(rolled['receipt']['authorization_sha256'], self.request['authorization_sha256'])
        self.assertEqual(rolled['receipt']['rollback_authorization_sha256'], request['authorization_sha256'])

    def test_mid_rollback_expiry_stops_later_removals_and_preserves_receipt(self):
        result = self.invoke()
        request = self.rollback_request(result)
        self.bind_auth(request, window_end=self.auth['window_end'])
        edit = self.backend.edit
        def expire(*args):
            result = edit(*args)
            self.clock.advance(1)
            return result
        with patch.object(self.backend, 'edit', side_effect=expire):
            result = self.invoke(request)
        self.assertEqual(result['status'], 'PARTIAL_ROLLBACK_REVIEW_REQUIRED')
        self.assertEqual(sum(action == 'remove' for _, _, action in self.backend.edits), 1)
        self.assertEqual(result['receipt'], json.loads(self.journal.path.read_bytes()))

    def test_source_mismatch_or_api_unavailable_does_not_remove_rules(self):
        result = self.invoke()
        request = self.rollback_request(result)
        request['source_commit'] = 'c' * 40
        self.bind_auth(request, source_commit='c' * 40)
        self.assertIn('rollback commit differs', self.invoke(request)['error'])
        request = self.rollback_request(result)
        with patch.object(self.backend, 'topology', side_effect=ValueError('API readiness did not pass')):
            self.assertIn('API readiness did not pass', self.invoke(request)['error'])
        self.assertEqual(len(self.backend.edits), 6)


if __name__ == '__main__':
    unittest.main()
