"""Workday lifecycle tests. All firewall/systemd edges are mocks, never Runtime evidence."""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_cp1_minimal_change import (m, e, c, a, BUNDLE, CLOCK, FakeIO,
                                     baseline, authorization, fixture, pack, MemoryJournal)


def work_authorization(base):
    result = authorization(base)
    result.update(schema='fds.cp1-minimal-authorization.v2', retention_approved=True,
                  window_end='2026-09-15T04:00:00+00:00', retention_until='2026-09-15T09:00:00+00:00',
                  recovery_margin_seconds=180)
    return result


class WorkIO(FakeIO):
    def __init__(self, base):
        super().__init__(base)
        self.early = self.hard = False
        self.directory = None

    def arm(self, withdraw, retained=None):
        self.retained_withdraw_at = retained
        if retained:
            self.hard = True
            self.action('arm:retention')
        self.early = self.armed = True
        self.action('arm:initial')

    def timer_active(self, retained=False):
        return self.hard and (retained or self.early)

    def disarm(self):
        self.early = self.hard = self.armed = False
        self.action('disarm')


class WorkFixture(unittest.TestCase):
    def setUp(self):
        self.base, self.original = baseline()
        self.auth = work_authorization(self.base)
        self.io = WorkIO(self.base)
        self.journal = MemoryJournal()

    def apply(self):
        return m.apply(self.io, self.journal, self.original, self.auth, BUNDLE)

    def evidence(self):
        record = self.apply()
        request, wrapper = fixture()
        wrapper['result']['firewall_before'] = copy.deepcopy(record['after']['firewall'])
        wrapper['result']['firewall_after'] = copy.deepcopy(record['after']['firewall'])
        request['kit_hashes']['expected-firewall.json'] = a.sha(a.encoded(record['after']['firewall']))
        wrapper['kit_hashes'] = copy.deepcopy(request['kit_hashes'])
        self.io.clock = CLOCK + dt.timedelta(minutes=2)
        handoff = m.receipt(record, self.original, request, wrapper, BUNDLE, self.io.clock)
        acceptance = dict(schema='fds.cp1-minimal-acceptance.v2', decision='ACCEPTED',
                          independent_reviewer='hayeounglee', review_decision='APPROVED',
                          review_url=self.auth['review_url'], reviewed_at=self.io.clock.isoformat(),
                          retention_approved=True, expires_at=record['retained_withdraw_at'],
                          handoff_sha256=m.fingerprint(handoff), source_bundle_sha256=BUNDLE,
                          checks={key:dict(status='PASS', evidence_sha256='a'*64)
                                  for key in ('positive', 'negative', 'regression')})
        proof = m.retention_proof(record, handoff, acceptance, self.io.clock)
        return record, request, wrapper, handoff, acceptance, proof


class WorkWindow(WorkFixture):
    def test_more_than_two_hours_is_explicit_and_bounded_to_one_staffed_day(self):
        record = self.apply()
        self.assertEqual(record['schema'], 'fds.cp1-minimal-change.v2')
        self.assertEqual(record['withdraw_at'], '2026-09-15T03:57:00+00:00')
        self.assertEqual(record['retained_withdraw_at'], '2026-09-15T08:57:00+00:00')
        self.assertEqual(record['after']['objects']['permanent'], self.base['objects']['permanent'])

    def test_workday_window_rejects_bad_time_margin_and_missing_retention_approval(self):
        bad = [('retention_approved', False), ('retention_until', None),
               ('retention_until', '2026-09-16T01:00:00+00:00'),
               ('retention_until', self.auth['window_end']), ('retention_until', '2026-09-15T04:01:00'),
               ('recovery_margin_seconds', True), ('recovery_margin_seconds', 179),
               ('recovery_margin_seconds', -1), ('recovery_margin_seconds', 180.0),
               ('window_end', '2026-09-15T01:30:00+00:00'), ('window_start', '2026-09-15T03:00:00+00:00')]
        for key, value in bad:
            with self.subTest(key=key, value=value), self.assertRaises((ValueError, TypeError)):
                m.check_authorization(dict(self.auth, **{key:value}), self.base, BUNDLE, CLOCK)
        self.assertEqual(self.io.actions, [])

    def test_weekend_is_rejected_even_with_explicit_extended_times(self):
        auth = {k:v.replace('2026-09-15', '2026-09-19') if isinstance(v,str) else v for k,v in self.auth.items()}
        with self.assertRaisesRegex(ValueError, 'weekday'):
            m.check_authorization(auth, self.base, BUNDLE, CLOCK.replace(day=19))

    def test_v1_two_hour_cap_is_not_silently_reinterpreted(self):
        auth = authorization(self.base)
        auth['window_end'] = self.auth['window_end']
        with self.assertRaisesRegex(ValueError, 'unbounded'):
            m.check_authorization(auth, self.base, BUNDLE, CLOCK)

    def test_larger_reviewed_recovery_margin_moves_both_deadlines(self):
        self.auth['recovery_margin_seconds'] = 600
        record = self.apply()
        self.assertEqual(record['withdraw_at'], '2026-09-15T03:50:00+00:00')
        self.assertEqual(record['retained_withdraw_at'], '2026-09-15T08:50:00+00:00')

    def test_both_timers_precede_every_firewall_mutation(self):
        self.apply()
        self.assertEqual(self.io.actions[:2], ['arm:retention', 'arm:initial'])
        self.assertTrue(self.io.hard and self.io.early)

    def test_partial_timer_setup_failure_cleans_timers_without_firewall_mutation(self):
        for index in (1, 2):
            with self.subTest(index=index):
                self.io = WorkIO(self.base); self.io.fail = index; self.journal = MemoryJournal()
                with self.assertRaises(OSError): self.apply()
                self.assertFalse(any(x.startswith('stage') for x in self.io.actions))
                self.assertEqual(self.io.snapshot(), self.base)
                self.assertFalse(self.io.hard or self.io.early)
                self.assertTrue(self.journal.saved[-1]['cleanup_complete'])

    def test_timer_cleanup_failure_is_not_reported_as_restored(self):
        self.io.fail = 1
        with patch.object(self.io, 'disarm', side_effect=OSError('uncertain stop')), self.assertRaises(OSError):
            self.apply()
        self.assertEqual(self.journal.saved[-1]['status'], 'FAILED_REVIEW_REQUIRED')
        self.assertNotIn('cleanup_complete', self.journal.saved[-1])

    def test_v2_mutation_failures_recover_both_deadlines_and_whole_state(self):
        for index in range(3, 16):
            with self.subTest(index=index):
                self.io = WorkIO(self.base); self.io.fail = index; self.journal = MemoryJournal()
                with self.assertRaises(OSError): self.apply()
                self.assertEqual(self.io.snapshot(), self.base)
                self.assertFalse(self.io.hard or self.io.early)

    def test_unaccepted_job_recovers_at_first_deadline(self):
        record = self.apply(); self.io.clock = m.instant(record['withdraw_at'])
        result = e.deadline_action(self.io, self.journal, record, None)
        self.assertEqual(result['status'], 'RESTORED_REVIEW_REQUIRED')
        self.assertEqual(self.io.snapshot(), self.base)
        self.assertFalse(self.io.hard or self.io.early)

    def test_deadline_command_cannot_recover_early(self):
        record = self.apply(); count = len(self.io.actions)
        with self.assertRaisesRegex(ValueError, 'not arrived'):
            e.deadline_action(self.io, self.journal, record, None)
        self.assertEqual(len(self.io.actions), count)

    def test_accepted_job_skips_first_recovery_without_rearming_or_mutation(self):
        record, _, _, handoff, acceptance, proof = self.evidence()
        self.io.clock = m.instant(record['withdraw_at']); count = len(self.io.actions)
        result = e.deadline_action(self.io, self.journal, record, proof)
        self.assertEqual(result['status'], 'ACCEPTED_RETAINED')
        self.assertEqual(len(self.io.actions), count)
        self.assertTrue(self.io.hard)
        self.assertEqual(self.io.snapshot(), record['after'])

    def test_final_deadline_always_recovers_even_after_acceptance(self):
        record, _, _, _, _, proof = self.evidence()
        self.io.clock = m.instant(record['retained_withdraw_at'])
        result = e.deadline_action(self.io, self.journal, record, proof)
        self.assertEqual(result['status'], 'RESTORED_REVIEW_REQUIRED')
        self.assertFalse(self.io.hard or self.io.early)

    def test_missing_hard_timer_never_claims_retained_success(self):
        record, _, _, _, _, proof = self.evidence()
        self.io.clock = m.instant(record['withdraw_at']); self.io.hard = False
        result = e.deadline_action(self.io, self.journal, record, proof)
        self.assertEqual(result['status'], 'RESTORED_REVIEW_REQUIRED')

    def test_retained_recovery_preserves_foreign_drift_boundary(self):
        record, _, _, _, _, proof = self.evidence()
        self.io.clock = m.instant(record['retained_withdraw_at'])
        self.io.view['protection']['calico'] = 'CHANGED'; count = len(self.io.actions)
        with self.assertRaises(ValueError):e.deadline_action(self.io, self.journal, record, proof)
        self.assertEqual(self.io.actions[count:], ['bind:False'])
        self.assertNotIn('cleanup_complete', record)

    def test_acceptance_cannot_be_created_after_first_deadline(self):
        record, _, _, handoff, acceptance, _ = self.evidence()
        with self.assertRaisesRegex(ValueError, 'expired trial'):
            m.retention_proof(record, handoff, acceptance, m.instant(record['withdraw_at']))
        late = dict(acceptance, reviewed_at=record['withdraw_at'])
        with self.assertRaisesRegex(ValueError, 'first deadline'):
            m.validate_acceptance(late, handoff, m.instant(record['withdraw_at']))

    def test_v1_acceptance_cannot_authorize_v2_retention(self):
        _, _, _, handoff, acceptance, _ = self.evidence()
        with self.assertRaisesRegex(ValueError, 'version mismatch'):
            m.validate_acceptance(dict(acceptance, schema='fds.cp1-minimal-acceptance.v1'), handoff, self.io.clock)

    def test_exact_retention_proof_replay_and_tamper_rejection(self):
        record, _, _, handoff, acceptance, proof = self.evidence()
        self.assertEqual(m.validate_retention(proof, record, handoff, acceptance), proof)
        for key,value in [('job_id','a'*16),('acceptance_sha256','f'*64),('handoff_sha256','f'*64),
                          ('withdraw_at',self.auth['retention_until']),('accepted_at',record['withdraw_at'])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.validate_retention(dict(proof, **{key:value}), record, handoff, acceptance)

    def test_expired_handoff_requires_retention_acceptance_and_still_expires(self):
        record, request, wrapper, handoff, acceptance, _ = self.evidence()
        later = m.instant(record['withdraw_at']) + dt.timedelta(seconds=1)
        with self.assertRaises(ValueError):m.validate_handoff(handoff, self.original, request, wrapper, BUNDLE, later)
        self.assertEqual(m.validate_handoff(handoff, self.original, request, wrapper, BUNDLE, later, acceptance),record)
        with self.assertRaises(ValueError):
            m.validate_handoff(handoff, self.original, request, wrapper, BUNDLE, m.instant(record['retained_withdraw_at']), acceptance)

    def test_consumer_recomputes_actual_trial_for_v2_after_first_deadline(self):
        record, request, wrapper, handoff, acceptance, _ = self.evidence()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)/'trial.tar.gz'; raw=pack(request,wrapper); archive.write_bytes(raw)
            later = m.instant(record['withdraw_at']) + dt.timedelta(seconds=1)
            self.assertEqual(c.validate(self.original,handoff,acceptance,archive,m.digest(raw),later),record)

    def test_live_retention_requires_matching_root_acceptance_and_hard_timer(self):
        record, _, _, _, acceptance, proof = self.evidence()
        with patch.object(e, 'load_retention', return_value=proof):status=e.live_status(self.io, record)
        m.live_gate(status, record, clock=self.io.clock, acceptance=acceptance)
        for key,value in [('acceptance_sha256','f'*64),('handoff_sha256','f'*64),('recovery_armed',False),
                          ('status','ACTIVE_REVIEW_REQUIRED'),('current',self.base)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                m.live_gate(dict(status, **{key:value}),record,clock=self.io.clock,acceptance=acceptance)

    def test_target_window_end_replaces_arbitrary_fifteen_minute_gate(self):
        record, _, _, _, acceptance, proof = self.evidence()
        with patch.object(e, 'load_retention', return_value=proof):status=e.live_status(self.io, record)
        m.live_gate(status,record,clock=self.io.clock,acceptance=acceptance,required_until=self.auth['window_end'])
        for end in (record['retained_withdraw_at'],self.auth['retention_until'],self.io.clock.isoformat()):
            with self.subTest(end=end),self.assertRaisesRegex(ValueError,'target work window'):
                m.live_gate(status,record,clock=self.io.clock,acceptance=acceptance,required_until=end)

    def test_status_does_not_start_long_locked_snapshot_near_recovery(self):
        record = self.apply(); self.io.clock=m.instant(record['withdraw_at'])-dt.timedelta(seconds=360)
        with patch.object(e,'load_retention',return_value=None),patch.object(self.io,'snapshot') as snapshot:
            with self.assertRaisesRegex(ValueError,'locked status'):e.live_status(self.io,record)
            snapshot.assert_not_called()


class RetentionCommit(WorkFixture):
    def setUp(self):
        super().setUp()
        # Production files are root-owned. Unit tests may run as a normal user;
        # only the owner metadata is mocked, not file IO, modes or atomic writes.
        fstat=e.os.fstat
        def root_owner(fd):
            st=fstat(fd)
            return e.os.stat_result(tuple(st[:4])+(0,)+tuple(st[5:]))
        owner_patch=patch.object(e.os,'fstat',side_effect=root_owner)
        self.addCleanup(owner_patch.stop)
        owner_patch.start()

    def prepare(self, directory):
        record, request, wrapper, handoff, acceptance, proof=self.evidence()
        self.io.directory=Path(directory)
        for name,value in [('HANDOFF.json',handoff),('ORIGINAL.json',self.original),('CHANGE.json',record)]:
            (self.io.directory/name).write_bytes(m.encoded(value))
        raw=pack(request,wrapper); trial=self.io.directory/'trial.tar.gz';trial.write_bytes(raw)
        return record, handoff, acceptance, trial, m.digest(raw)

    def test_actual_atomic_commit_preserves_old_receipt_and_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            record,handoff,acceptance,trial,sha=self.prepare(directory)
            before=(self.io.directory/'CHANGE.json').read_bytes();actions=list(self.io.actions)
            result=e.commit_retention(self.io,record,BUNDLE,acceptance,trial,sha)
            self.assertEqual(result['status'],'RETENTION_COMMITTED')
            self.assertEqual(e.load_retention(self.io,record)['acceptance_sha256'],m.fingerprint(acceptance))
            self.assertEqual((self.io.directory/'CHANGE.json').read_bytes(),before)
            self.assertEqual((self.io.directory/'HANDOFF.json').read_bytes(),m.encoded(handoff))
            self.assertEqual(self.io.actions,actions)
            committed=(self.io.directory/'RETENTION.json').read_bytes()
            self.io.clock=m.instant(record['withdraw_at'])+dt.timedelta(minutes=1)
            self.assertEqual(e.commit_retention(self.io,record,BUNDLE,acceptance,None,None)['status'],'ACCEPTED_RETAINED')
            self.assertEqual((self.io.directory/'RETENTION.json').read_bytes(),committed)

    def test_interruption_before_commit_marker_does_not_grant_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            record,_,acceptance,trial,sha=self.prepare(directory);original=e.atomic
            def interrupted(path,raw):
                if path.name=='RETENTION.json':raise OSError('disk full')
                original(path,raw)
            with patch.object(e,'atomic',side_effect=interrupted),self.assertRaises(OSError):
                e.commit_retention(self.io,record,BUNDLE,acceptance,trial,sha)
            self.assertIsNone(e.load_retention(self.io,record))
            self.io.clock=m.instant(record['withdraw_at'])
            self.assertEqual(e.deadline_action(self.io,self.journal,record,None)['status'],'RESTORED_REVIEW_REQUIRED')

    def test_commit_crossing_first_deadline_is_quarantined_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            record,_,acceptance,trial,sha=self.prepare(directory);original=e.atomic
            def delayed(path,raw):
                original(path,raw)
                if path.name=='RETENTION.json':self.io.clock=m.instant(record['withdraw_at'])
            with patch.object(e,'atomic',side_effect=delayed),self.assertRaisesRegex(ValueError,'crossed'):
                e.commit_retention(self.io,record,BUNDLE,acceptance,trial,sha)
            self.assertIsNone(e.load_retention(self.io,record))
            self.assertTrue((self.io.directory/'RETENTION_LATE.json').exists())

    def test_altered_or_missing_root_acceptance_cannot_suppress_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            record,_,acceptance,trial,sha=self.prepare(directory)
            e.commit_retention(self.io,record,BUNDLE,acceptance,trial,sha)
            path=self.io.directory/'ACCEPTANCE.json';doc=json.loads(path.read_bytes());doc['checks']['positive']['status']='FAIL'
            path.write_bytes(m.encoded(doc))
            with self.assertRaises(ValueError):e.load_retention(self.io,record)
            path.unlink()
            with self.assertRaises(FileNotFoundError):e.load_retention(self.io,record)

    def test_unreviewed_or_changed_acceptance_cannot_replace_committed_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            record,_,acceptance,trial,sha=self.prepare(directory)
            with self.assertRaises(ValueError):
                e.commit_retention(self.io,record,BUNDLE,dict(acceptance,decision='HOLD'),trial,sha)
            self.assertFalse((self.io.directory/'RETENTION.json').exists())
            e.commit_retention(self.io,record,BUNDLE,acceptance,trial,sha)
            with self.assertRaisesRegex(ValueError,'different evidence'):
                e.commit_retention(self.io,record,BUNDLE,dict(acceptance,review_url=acceptance['review_url']+'4'),trial,sha)

    def test_old_job_is_rejected_before_reading_any_retention_input(self):
        with patch.object(e,'root_document') as read,self.assertRaisesRegex(ValueError,'old jobs'):
            e.commit_retention(self.io,{'schema':'fds.cp1-minimal-change.v1'},BUNDLE,{},None,None)
        read.assert_not_called()

    def test_root_document_rejects_symlink_and_writable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'real.json').write_text('{}');(root/'link.json').symlink_to(root/'real.json')
            with self.assertRaises(OSError):e.root_document(root,'link.json')
            (root/'real.json').chmod(0o666)
            with self.assertRaises(ValueError):e.root_document(root,'real.json')


class TimerAdapter(unittest.TestCase):
    def test_non_root_document_owner_is_rejected(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data.json').write_text('{}')
            with patch.object(e.os,'fstat',return_value=SimpleNamespace(st_uid=1234,st_mode=0o100600)),self.assertRaises(ValueError):
                e.root_document(root,'data.json')

    def test_two_bounded_units_use_same_frozen_deadline_command(self):
        io=object.__new__(e.IO);io.job_id='a'*16;io.unit='fds-cp1-minimal-'+io.job_id
        io.directory=Path('/var/lib/fds/cp1-minimal')/io.job_id
        with patch.object(io,'now',return_value=CLOCK),patch.object(io,'command') as command,patch.object(io,'timer_active',return_value=True):
            io.arm((CLOCK+dt.timedelta(hours=3)).isoformat(),(CLOCK+dt.timedelta(hours=6)).isoformat())
        calls=[v.args[0] for v in command.call_args_list]
        self.assertEqual(len(calls),2)
        self.assertIn('--unit='+io.unit+'-retention',calls[0]);self.assertIn('--unit='+io.unit,calls[1])
        self.assertTrue(all(argv[-3:]==['deadline','--job',io.job_id] for argv in calls))
        self.assertTrue(all('--property=TimeoutStartSec=180s' in argv for argv in calls))

    def test_missing_either_initial_timer_is_rejected(self):
        io=object.__new__(e.IO);io.unit='test';io.retained_withdraw_at='present'
        for hard,early in [(False,True),(True,False)]:
            with patch.object(io,'unit_active',side_effect=lambda name:hard if name.endswith('-retention') else early):
                self.assertFalse(io.timer_active())
                self.assertEqual(io.timer_active(retained=True),hard)

    def test_seconds_round_up_not_before_reviewed_deadline(self):
        io=object.__new__(e.IO);io.job_id='a'*16;io.directory=Path('/frozen')
        with patch.object(io,'now',return_value=CLOCK),patch.object(io,'command') as command:
            io.arm_one('owned',(CLOCK+dt.timedelta(seconds=900,microseconds=1)).isoformat(),'deadline')
        self.assertIn('--on-active=901s',command.call_args.args[0])


class DownstreamAdapter(unittest.TestCase):
    def exercise(self, failure=None):
        from test_cp1_minimal_change import AdapterWorkflow
        class FixedDateTime(dt.datetime):
            @classmethod
            def now(cls,tz=None):
                value=CLOCK+dt.timedelta(minutes=2)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)
        def auth(base):
            value=work_authorization(base)
            value.update(window_end=(CLOCK+dt.timedelta(minutes=30)).isoformat(),
                         retention_until=(CLOCK+dt.timedelta(minutes=60)).isoformat())
            return value
        with patch.object(dt,'datetime',FixedDateTime):
            return AdapterWorkflow().exercise(failure,auth_factory=auth,io_factory=WorkIO)

    def test_v2_controller_executes_only_with_retained_proof_and_fitting_window(self):
        rc,result,calls,live=self.exercise()
        self.assertEqual(rc,0,result)
        self.assertEqual([v for v in calls if v[1]=='apply'],[('k8s-control02','apply')])
        self.assertEqual(len(live),2)

    def test_target_window_crossing_cp1_expiry_blocks_before_target_mutation(self):
        rc,result,calls,live=self.exercise('target-window')
        self.assertEqual(rc,2,result)
        self.assertIn('target work window',result['error'])
        self.assertFalse(any(mode=='apply' for _,mode in calls))

    def test_wrong_retained_proof_blocks_target_and_lost_timer_blocks_success(self):
        for mode,count in [('retention-proof',0),('after',1)]:
            with self.subTest(mode=mode):
                rc,result,calls,live=self.exercise(mode)
                self.assertEqual(rc,2,result)
                self.assertEqual(sum(m=='apply' for _,m in calls),count)
