"""Guardian CLI as real processes against the SYNTHETIC loopback TLS API (Linux only).

Covers what a library test cannot: the guardian outliving its launcher, an
operator signal, and a guardian killed while it owns the canary.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_api as f
import coordinator as c
import decision as d
import guardian as g
import journal as j
import supervisor as s
import transport as t

GUARDIAN = Path(__file__).resolve().parents[1] / 'guardian.py'
PROBE = Path(__file__).resolve().parent / 'signal_probe.py'
STORM = ('SIGUSR2', 'SIGUSR1', 'SIGTERM', 'SIGINT', 'SIGHUP')
RECLAIM = [('GET', t.NAMESPACE), ('GET', d.PATH), ('POST', c.COLLECTION), ('GET', d.PATH),
           ('DELETE', d.PATH), ('GET', d.PATH)]


@unittest.skipUnless(sys.platform == 'linux', 'POSIX signals delivered to a child process')
class TriggerTests(unittest.TestCase):
    """Each probe is a child with a timeout: a deadlock fails the test instead of hanging it."""
    def probe(self, *args, timeout=20):
        done = subprocess.run([sys.executable, '-B', str(PROBE), *args], capture_output=True, timeout=timeout)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        return json.loads(done.stdout)

    def lines(self, mode, signame, accept, least):
        count = 0
        for index in range(16):
            out = self.probe(mode, signame, str(index))
            if not out['fired']:
                break
            count += 1
            self.assertIn(out['value'], accept, out)
            self.assertLess(out['elapsed'], 2, out)
        self.assertGreaterEqual(count, least)  # The injection really walked the function.

    def test_probe_detects_the_old_event_based_deadlock(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.probe('legacy', timeout=3)

    def test_signal_at_every_line_of_wait_returns_its_trigger(self):
        for signame, expected in g.Triggers.SIGNALS:
            with self.subTest(signal=signame):
                self.lines('wait', signame, (expected,), 3)

    def test_second_signal_inside_the_handler_cannot_block_or_raise(self):
        self.lines('nested', 'SIGUSR1', ('abort', 'complete'), 5)
        self.lines('nested', 'SIGTERM', ('abort', 'complete'), 5)

    def test_timeout_returns_nothing_and_a_late_signal_is_still_seen(self):
        out = self.probe('expiry')
        self.assertIsNone(out['value'])
        self.assertTrue(0.15 <= out['elapsed'] < 2, out)
        self.assertEqual(out['late'], 'abort')

    def test_signal_storm_keeps_the_first_trigger_and_close_releases_cleanly(self):
        out = self.probe('storm')
        self.assertEqual((out['value'], out['after_close']), ('complete', 'complete'))
        self.assertLess(out['elapsed'], 2)
        self.assertEqual(out['written_after_close'], 0)  # Nothing lands in a reused descriptor.

    def test_lost_descriptors_never_raise_from_the_handler_and_end_in_reclaim(self):
        out = self.probe('broken')
        self.assertEqual((out['value'], out['await']), ('abort', 'abort'))


@unittest.skipUnless(f.OPENSSL and sys.platform == 'linux', 'Linux processes, journal and throwaway PKI')
class GuardianProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pki_dir = tempfile.TemporaryDirectory()
        cls.pki = f.make_pki(cls.pki_dir.name)

    @classmethod
    def tearDownClass(cls):
        cls.pki_dir.cleanup()

    def setUp(self):
        self.api = f.FakeApi(self.pki)
        self.addCleanup(self.api.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root, self.pids = Path(self.temp.name), []
        self.addCleanup(self.reap)
        for name in ('journal', 'evidence'):
            (self.root / name).mkdir(mode=0o700)
            os.chmod(self.root / name, 0o700)
        (self.root / 'token').write_text('SYNTHETIC-not-a-credential\n')
        os.chmod(self.root / 'token', 0o600)
        self.manifest = dict(apiVersion=d.API, kind='GlobalNetworkPolicy', metadata=dict(name=d.NAME),
                             spec=dict(selector='synthetic == "test"', types=['Egress']))
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest))
        ca = self.pki / 'ca.crt'
        self.cfg = dict(
            schema=1, session_id='SYNTHETIC-session', expected_cluster_uid=f.CLUSTER_UID,
            api=dict(connect_ip='127.0.0.1', port=self.api.port, tls_server_name='127.0.0.1',
                     ca_file=str(ca), ca_sha256=hashlib.sha256(ca.read_bytes()).hexdigest()),
            credential=dict(kind='bearer_token_file', token_file=str(self.root / 'token')),
            manifest_file=str(self.root / 'manifest.json'),
            reviewed_manifest_sha256=c.manifest_hash(self.manifest),
            reviewed_spec_sha256=d.spec_hash(self.manifest['spec']),
            journal_dir=str(self.root / 'journal'), evidence_dir=str(self.root / 'evidence'),
            budgets=dict(lease_seconds=1.5, cleanup_window_seconds=5, observation_max_age_seconds=1,
                         request_timeout_seconds=1, readback_delay_seconds=0.1))

    def reap(self):
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def config(self, **budgets):
        self.cfg['budgets'].update(budgets)
        path = self.root / 'config.json'
        path.write_text(json.dumps(self.cfg))
        return str(path)

    def cli(self, *args):
        done = subprocess.run([sys.executable, '-B', str(GUARDIAN), *args], capture_output=True, timeout=60)
        return done.returncode, [json.loads(line) for line in done.stdout.splitlines()]

    def until(self, check, seconds=20):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            value = check()
            if value:
                return value
            time.sleep(0.05)
        self.fail('condition was not reached in time')

    def rows(self, name=s.STEPS):
        path = self.root / 'evidence' / name
        whole = path.read_bytes().split(b'\n')[:-1] if path.exists() else []  # Skip a row still being written.
        return [json.loads(line) for line in whole]

    def final(self, name=s.STEPS):
        return next((r for r in self.rows(name) if r['step'] == 'FINAL'), None)

    def events(self):
        return [json.loads(line)['event'] for line in (self.root / 'journal' / j.JOURNAL).read_bytes().splitlines()]

    def launch(self, *args):
        """Detached start: returns once the launcher has exited."""
        code, lines = self.cli(*args)
        self.assertEqual((code, lines[-1]['step']), (0, 'ARMED'), lines)
        row = next(r for r in lines if 'guardian_epoch' in r or 'binding' in r)
        epoch = row.get('guardian_epoch') or row['binding']['guardian_epoch']
        pid = int(epoch.split('-')[0])
        self.pids.append(pid)
        self.assertNotEqual(os.getsid(pid), os.getsid(0))  # It left the launching session.
        return pid

    def test_validate_reads_local_inputs_and_sends_nothing(self):
        code, lines = self.cli('validate', '--config', self.config())
        self.assertEqual((code, lines[0]['passed'], lines[0]['network_requests']), (0, True, 0))
        self.assertEqual(lines[0]['computed_manifest_sha256'], self.cfg['reviewed_manifest_sha256'])
        self.cfg['reviewed_manifest_sha256'] = '0' * 64
        code, lines = self.cli('validate', '--config', self.config())
        self.assertEqual((code, lines[0]['manifest_sha256_matches']), (5, False))
        self.cfg['reviewed_manifest_sha256'] = c.manifest_hash(self.manifest)
        code, lines = self.cli('validate', '--config', self.config(cleanup_window_seconds=3))
        self.assertEqual((code, lines[0]['budgets_consistent']), (5, False))
        self.cfg['evidence_dir'] = self.cfg['journal_dir']
        code, lines = self.cli('validate', '--config', self.config(cleanup_window_seconds=5))
        self.assertEqual((code, lines[0]['directories_independent']), (5, False))
        self.assertEqual(self.api.requests, [])

    def test_input_file_is_strict_and_has_no_default_budget(self):
        path = self.config()
        for change in (lambda: self.cfg['budgets'].pop('lease_seconds'), lambda: self.cfg.update(extra=1),
                       lambda: self.cfg['api'].update(connect_ip='localhost')):
            saved = json.dumps(self.cfg)
            change()
            code, lines = self.cli('run', '--foreground', '--config', self.config())
            self.assertIn(code, (2, 4))
            self.assertIn(lines[-1]['reason'], ('INPUT_INVALID', 'ENDPOINT_INVALID'))
            self.cfg = json.loads(saved)
        os.chmod(path, 0o666)
        code, lines = self.cli('run', '--foreground', '--config', path)
        self.assertEqual((code, lines[-1]['reason']), (2, 'FILE_INVALID'))
        self.assertEqual(self.api.requests, [])
        self.assertEqual(os.listdir(self.root / 'journal'), [])

    def test_preflight_checks_access_and_spec_pin_without_persisting(self):
        code, lines = self.cli('preflight', '--config', self.config())
        self.assertEqual((code, lines[0]['passed'], lines[0]['persisted_writes']), (0, True, 0))
        self.assertEqual(self.api.methods(), [('GET', t.NAMESPACE), ('GET', d.PATH)] +
                         [('POST', t.ACCESS_REVIEW)] * 3 + [('POST', t.DRY_RUN)])
        self.assertIsNone(self.api.object)
        self.api.allowed, self.api.spec_default = False, dict(tier='default')
        code, lines = self.cli('preflight', '--config', self.config())
        self.assertEqual(code, 5)
        self.assertEqual(lines[0]['checks'], dict(cluster_and_absence='PASS', access_create='FAIL',
                                                  access_get='FAIL', access_delete='FAIL', dry_run_spec_pin='FAIL'))
        self.assertEqual(lines[0]['defaulted_spec_sha256'],
                         d.spec_hash(dict(self.manifest['spec'], tier='default')))
        self.assertEqual(os.listdir(self.root / 'journal'), [])

    def test_foreground_session_reclaims_after_the_lease(self):
        code, lines = self.cli('run', '--foreground', '--config', self.config())
        self.assertEqual(code, 0, lines)
        self.assertEqual([r['step'] for r in lines],
                         ['ARMING', 'ARMED', 'CREATE', 'TRIGGER', 'CLEANUP', 'READBACK', 'FINAL'])
        self.assertEqual((lines[-1]['disposition'], lines[-1]['baseline_verified']),
                         ('ABSENT_BASELINE_UNVERIFIED', False))
        self.assertEqual(lines[-1]['transport_last'], dict(method='GET', stage='DONE', http_status=404))
        self.assertEqual(self.api.methods(), RECLAIM)
        self.assertIsNone(self.api.object)
        self.assertEqual(json.loads(self.api.requests[4]['body'])['preconditions'],
                         dict(uid=lines[2]['uid'], resourceVersion=lines[2]['resource_version']))
        self.assertGreaterEqual(lines[3]['mono'], lines[0]['expires_mono'])
        self.assertEqual(self.events(), ['CREATE_INTENT', 'OWNED', 'DELETE_INTENT', 'DELETE_RESULT', 'ABSENT'])
        # The stream copy alone carries the outcome of each row's own write and fsync.
        self.assertTrue(all(row.pop('evidence_complete') is True for row in lines))
        self.assertEqual(self.rows(), lines)
        self.assertIs(self.rows()[-1]['evidence_complete_before_write'], True)
        self.assertNotIn(b'SYNTHETIC-not-a-credential',
                         (self.root / 'evidence' / s.STEPS).read_bytes() + (self.root / 'journal' / j.JOURNAL).read_bytes())

    def test_detached_guardian_reclaims_after_its_launcher_exited(self):
        self.launch('run', '--config', self.config(lease_seconds=4))
        self.assertIsNone(self.final())  # The launcher/observer is gone while the lease still runs.
        final = self.until(self.final)
        self.assertEqual(final['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
        self.assertEqual(self.api.methods(), RECLAIM)
        self.assertIsNone(self.api.object)

    def test_operator_signal_reclaims_early_and_hangup_is_ignored(self):
        pid = self.launch('run', '--config', self.config(lease_seconds=30))
        self.until(lambda: self.api.object)
        os.kill(pid, signal.SIGHUP)
        time.sleep(0.5)
        self.assertEqual(self.api.methods()[-1], ('POST', c.COLLECTION))  # Still owned, nothing deleted.
        os.kill(pid, signal.SIGUSR1)
        final = self.until(self.final)
        self.assertEqual(final['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
        self.assertEqual(next(r for r in self.rows() if r['step'] == 'TRIGGER')['trigger'], 'abort')
        self.assertEqual(self.api.methods(), RECLAIM)

    def storm(self, pid, start_mono=None):
        while start_mono is not None and time.clock_gettime(time.CLOCK_BOOTTIME) < start_mono:
            time.sleep(0.0005)
        sent = 0
        while self.final() is None and sent < 20000:
            try:
                os.kill(pid, getattr(signal, STORM[sent % len(STORM)]))
            except ProcessLookupError:
                break
            sent += 1
            time.sleep(0.002)
        final = self.until(self.final)
        self.assertGreater(sent, 5)
        self.assertEqual(final['disposition'], 'ABSENT_BASELINE_UNVERIFIED')
        self.assertEqual(self.api.methods(), RECLAIM)  # One POST, one DELETE, one readback.
        self.assertIsNone(self.api.object)
        self.assertEqual(self.events(), ['CREATE_INTENT', 'OWNED', 'DELETE_INTENT', 'DELETE_RESULT', 'ABSENT'])
        return next(r for r in self.rows() if r['step'] == 'TRIGGER')['trigger']

    def test_repeated_signals_through_reclaim_and_exit_delete_exactly_once(self):
        pid = self.launch('run', '--config', self.config(lease_seconds=30))
        self.until(lambda: self.api.object)
        self.assertIn(self.storm(pid), ('abort', 'complete'))

    def test_signals_racing_the_lease_expiry_delete_exactly_once(self):
        pid = self.launch('run', '--config', self.config(lease_seconds=1.5))
        expires = next(r for r in self.rows() if r['step'] == 'ARMING')['expires_mono']
        self.assertIn(self.storm(pid, expires - 0.004), ('abort', 'complete', 'expiry'))

    def test_killed_guardian_leaves_a_handoff_and_is_never_replayed(self):
        pid = self.launch('run', '--config', self.config(lease_seconds=30))
        self.until(lambda: any(r['step'] == 'CREATE' for r in self.rows()))
        created = self.api.object
        os.kill(pid, signal.SIGKILL)
        code, lines = self.cli('watch', '--journal-dir', self.cfg['journal_dir'],
                               '--poll-seconds', '0.1', '--window-seconds', '10')
        self.assertEqual(code, 3)
        self.assertEqual((lines[0]['state'], lines[0]['handoff_required'], lines[0]['last_event'], lines[0]['uid']),
                         ('WRITER_GONE_WITHOUT_TERMINAL', True, 'OWNED', created['metadata']['uid']))
        self.assertFalse(lines[0]['automatic_replay_allowed'])
        self.assertIsNone(self.final())
        self.assertIsNotNone(self.api.object)  # The canary remains: this is a takeover, not a reclaim.
        self.assertEqual(self.api.methods(), RECLAIM[:3])
        code, lines = self.cli('run', '--foreground', '--config', self.config())
        self.assertEqual((code, lines[-1]['reason']), (4, 'CANARY_ALREADY_PRESENT'))
        self.api.object = None  # As if the takeover had removed it by hand.
        code, lines = self.cli('run', '--foreground', '--config', self.config())
        self.assertEqual((code, lines[-1]['reason']), (4, 'SESSION_FILES_EXIST'))
        self.assertEqual([m for m, _ in self.api.methods()].count('POST'), 1)
        self.assertEqual(self.events(), ['CREATE_INTENT', 'OWNED'])

    def test_watch_follows_a_live_session_to_its_end(self):
        self.launch('run', '--config', self.config(lease_seconds=2))
        code, lines = self.cli('watch', '--journal-dir', self.cfg['journal_dir'],
                               '--poll-seconds', '0.1', '--window-seconds', '20')
        self.assertEqual((code, lines[0]['state'], lines[0]['phase']), (0, 'ABSENT_BASELINE_UNVERIFIED', 'DELETE_ACK'))

    def test_forbidden_delete_ends_in_recorded_handoff(self):
        deny = lambda h, _b: f.reply(h, 403, dict(apiVersion='v1', kind='Status', reason='Forbidden', code=403))
        self.api.script.extend([self.api.default] * 4 + [deny])
        code, lines = self.cli('run', '--foreground', '--config', self.config())
        self.assertEqual((code, lines[-1]['disposition'], lines[-1]['terminal_recorded']), (3, 'HANDOFF', True))
        self.assertEqual(lines[-1]['reason'], 'DELETE_ALREADY_CLAIMED_OR_OUTCOME_UNRESOLVED')
        self.assertEqual(self.api.methods(), RECLAIM)
        self.assertIsNotNone(self.api.object)

    def test_lost_delete_response_is_not_resent(self):
        def stall(handler, _body):
            time.sleep(3)
        self.api.script.extend([self.api.default] * 4 + [stall])
        code, lines = self.cli('run', '--foreground', '--config', self.config())
        self.assertEqual((code, lines[-1]['reason']), (3, 'TRANSPORT_UNCERTAIN'))
        self.assertEqual(lines[-1]['transport_last'], dict(method='DELETE', stage='READ', http_status=None))
        self.assertEqual(self.api.methods(), RECLAIM[:5])
        self.assertEqual(self.events()[-2:], ['DELETE_RESULT', 'HANDOFF'])

    def test_nothing_is_created_for_an_unverified_or_unreachable_cluster(self):
        self.cfg['expected_cluster_uid'] = 'another-cluster'
        code, lines = self.cli('run', '--config', self.config())  # Detached: the launcher relays the refusal.
        self.assertEqual((code, lines[-1]['step'], lines[-1]['reason']), (4, 'NOT_STARTED', 'CLUSTER_CHANGED'))
        self.cfg['expected_cluster_uid'] = f.CLUSTER_UID
        self.cfg['api']['ca_file'] = str(self.pki / 'other-ca.crt')
        self.cfg['api']['ca_sha256'] = hashlib.sha256((self.pki / 'other-ca.crt').read_bytes()).hexdigest()
        code, lines = self.cli('run', '--foreground', '--config', self.config())
        self.assertEqual((code, lines[-1]['reason']), (4, 'OS_ERROR'))
        self.assertEqual(self.api.methods(), [('GET', t.NAMESPACE)])
        self.assertEqual(os.listdir(self.root / 'journal') + os.listdir(self.root / 'evidence'), [])

    def test_survival_check_uses_the_same_detached_start_without_any_api(self):
        pid = self.launch('survival-check', '--evidence-dir', self.cfg['evidence_dir'],
                          '--seconds', '1', '--interval-seconds', '0.2')
        final = self.until(lambda: self.final('survival.jsonl'))
        self.assertEqual(final['disposition'], 'SURVIVED_REQUESTED_DURATION')
        rows = self.rows('survival.jsonl')
        self.assertEqual(int(rows[0]['guardian_epoch'].split('-')[0]), pid)
        self.assertGreaterEqual(sum(r['step'] == 'BEAT' for r in rows), 3)
        self.assertEqual(self.api.requests, [])


if __name__ == '__main__':
    unittest.main()
