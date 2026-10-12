"""SYNTHETIC Linux filesystem/process tests; no cluster, network, or credentials."""
import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULES))
import decision as d
import journal as j


@unittest.skipUnless(sys.platform == 'linux', 'Linux flock/dir_fd/fsync required')
class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        os.chmod(self.path, 0o700)
        self.directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.directory)
        self.binding = dict(session_id='synthetic-session', cluster_uid='synthetic-cluster',
                            host_boot_id='synthetic-boot', guardian_epoch='synthetic-epoch')
        self.body = dict(apiVersion=d.API, kind='GlobalNetworkPolicy',
                         metadata=dict(name=d.NAME, uid='synthetic-uid', resourceVersion='opaque-a'),
                         spec=dict(selector='synthetic == "test"', types=['Egress']))
        self.pin = d.spec_hash(self.body['spec'])
        self.create = dict(binding=self.binding, http_status=201, create_body=self.body,
                           reviewed_spec_sha256=self.pin, created_mono=10, expires_mono=20,
                           cleanup_deadline_mono=30, observation_max_age=2)
        self.observation = dict(binding=copy.deepcopy(self.binding), requested_name=d.NAME,
                                observed_mono=20, http_status=200, body=copy.deepcopy(self.body))

    def session(self):
        session = j.Session(self.directory, binding=self.binding, reviewed_spec_sha256=self.pin)
        self.addCleanup(session.close)
        return session

    def owned(self):
        session = self.session()
        session.record_owned(**self.create)
        return session

    def plan(self, session, **changes):
        args = dict(binding=self.binding, observation=self.observation, now_mono=20, trigger='expiry')
        args.update(changes)
        return session.plan_delete(**args)

    def not_found(self):
        obs = copy.deepcopy(self.observation)
        obs.update(http_status=404, body=dict(apiVersion='v1', kind='Status', reason='NotFound',
                                            code=404, details=dict(name=d.NAME, group=d.GROUP, kind=d.RESOURCE)))
        return obs

    def events(self):
        return [json.loads(line) for line in (self.path / j.JOURNAL).read_bytes().splitlines()]

    def assert_failed(self, session):
        with self.assertRaisesRegex(j.JournalError, 'CLOSED_OR_FAILED'):
            self.plan(session)

    def test_create_intent_and_delete_intent_durable_before_return(self):
        events = []
        fsync = os.fsync
        def record(fd):
            events.append('dir' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
            fsync(fd)
        with patch.object(j.os, 'fsync', side_effect=record):
            s = self.session()
            self.assertEqual(events, ['file', 'dir'])
            s.record_owned(**self.create)
            events.clear()
            intent = self.plan(s)
            events.append('fake-send')
        self.assertEqual(events, ['file', 'dir', 'fake-send'])
        self.assertEqual(intent['body']['preconditions'], dict(uid='synthetic-uid', resourceVersion='opaque-a'))
        self.assertEqual([e['event'] for e in self.events()], ['CREATE_INTENT', 'OWNED', 'DELETE_INTENT'])
        self.assertEqual(stat.S_IMODE((self.path / j.JOURNAL).stat().st_mode), 0o600)

    def test_ack_then_explicit_absence_is_separate(self):
        s = self.owned()
        self.plan(s)
        receipt = s.record_delete_result(202)
        self.assertEqual(receipt['phase'], 'DELETE_ACK')
        self.assertEqual(self.plan(s, observation=self.not_found())['action'], 'ABSENT')
        s.close()
        history = j.inspect_history(self.directory)
        self.assertEqual(history['last_event'], 'ABSENT')
        self.assertFalse(history['automatic_replay_allowed'])
        self.assertEqual(history['disposition'], 'HANDOFF_BASELINE_UNVERIFIED')

    def test_no_reissue_after_intent_without_response(self):
        s = self.owned()
        self.assertEqual(self.plan(s)['action'], 'DELETE_INTENT')
        self.assertEqual(self.plan(s)['action'], 'HANDOFF')
        self.assertEqual([e['event'] for e in self.events()].count('DELETE_INTENT'), 1)
        with self.assertRaises(j.JournalError):
            self.plan(s)

    def test_conflict_and_unknown_are_not_retried(self):
        # Distinct synthetic directories only; runtime uncertainty cannot be bypassed this way.
        for status, phase in ((409, 'CONFLICT'), (404, 'DELETE_UNKNOWN'), (None, 'DELETE_UNKNOWN')):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as path:
                directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with j.Session(directory, binding=self.binding, reviewed_spec_sha256=self.pin) as s:
                        s.record_owned(**self.create)
                        self.plan(s)
                        self.assertEqual(s.record_delete_result(status)['phase'], phase)
                        self.assertEqual(self.plan(s)['action'], 'HANDOFF')
                finally:
                    os.close(directory)

    def test_delete_404_followed_by_not_found_can_record_absence(self):
        s = self.owned()
        self.plan(s)
        s.record_delete_result(404)
        self.assertEqual(self.plan(s, observation=self.not_found())['action'], 'ABSENT')

    def test_create_uncertain_is_terminal(self):
        s = self.session()
        s.handoff()
        with self.assertRaises(j.JournalError):
            s.record_owned(**self.create)
        self.assertEqual([e['event'] for e in self.events()], ['CREATE_INTENT', 'HANDOFF'])

    def test_unconfirmed_create_and_changed_binding_refused(self):
        s = self.session()
        with self.assertRaises(ValueError):
            s.record_owned(**dict(self.create, http_status=200))
        self.assert_failed(s)
        self.assertEqual(len(self.events()), 1)

    def test_owned_receipt_cannot_change_pinned_generation(self):
        s = self.session()
        other = dict(self.binding, guardian_epoch='other')
        with self.assertRaisesRegex(j.JournalError, 'OWNERSHIP_CHANGED'):
            s.record_owned(**dict(self.create, binding=other))
        self.assert_failed(s)

    def test_generation_change_stops_before_intent(self):
        s = self.owned()
        other = dict(self.binding, host_boot_id='different')
        self.assertEqual(self.plan(s, binding=other)['action'], 'HANDOFF')
        self.assertNotIn('DELETE_INTENT', [e['event'] for e in self.events()])

    def test_wait_does_not_append_and_expiry_does(self):
        s = self.owned()
        obs = dict(self.observation, observed_mono=19)
        self.assertEqual(self.plan(s, observation=obs, now_mono=19)['action'], 'WAIT')
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.plan(s)['action'], 'DELETE_INTENT')

    def test_input_and_returned_receipt_mutation_cannot_change_journal(self):
        s = self.session()
        self.binding['guardian_epoch'] = 'changed'
        with self.assertRaises(j.JournalError):
            s.record_owned(**self.create)
        self.assertEqual(self.events()[0]['payload']['binding']['guardian_epoch'], 'synthetic-epoch')

    def test_result_copy_cannot_change_next_decision(self):
        s = self.session()
        receipt = s.record_owned(**self.create)
        receipt['uid'] = 'wrong'
        receipt['binding']['guardian_epoch'] = 'wrong'
        self.assertEqual(self.plan(s)['body']['preconditions']['uid'], 'synthetic-uid')

    def test_file_fsync_failure_never_returns_intent_or_allows_retry(self):
        s = self.owned()
        with patch.object(j.os, 'fsync', side_effect=OSError('synthetic I/O failure')):
            with self.assertRaises(OSError):
                self.plan(s)
        self.assert_failed(s)
        self.assertTrue((self.path / j.JOURNAL).exists())

    def test_directory_fsync_failure_never_authorizes_create(self):
        fsync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('synthetic directory failure')
            fsync(fd)
        with patch.object(j.os, 'fsync', side_effect=fail_directory):
            with self.assertRaises(OSError):
                self.session()
        before = (self.path / j.JOURNAL).read_bytes()
        with self.assertRaises(FileExistsError):
            self.session()
        self.assertEqual(before, (self.path / j.JOURNAL).read_bytes())

    def test_partial_write_preserved_and_cold_read_rejected(self):
        s = self.owned()
        write = os.write
        calls = []
        def partial(fd, data):
            if not calls:
                calls.append(1)
                return write(fd, data[:7])
            raise OSError('synthetic full disk')
        with patch.object(j.os, 'write', side_effect=partial):
            with self.assertRaises(OSError):
                self.plan(s)
        self.assert_failed(s)
        s.close()
        before = (self.path / j.JOURNAL).read_bytes()
        with self.assertRaisesRegex(j.JournalError, 'INCOMPLETE'):
            j.inspect_history(self.directory)
        self.assertEqual(before, (self.path / j.JOURNAL).read_bytes())

    def test_short_writes_complete_before_intent_return(self):
        s = self.owned()
        write = os.write
        with patch.object(j.os, 'write', side_effect=lambda fd, data: write(fd, data[:11])):
            self.assertEqual(self.plan(s)['action'], 'DELETE_INTENT')
        self.assertEqual(self.events()[-1]['event'], 'DELETE_INTENT')

    def test_existing_empty_file_is_not_reset(self):
        path = self.path / j.JOURNAL
        path.touch(mode=0o600)
        with self.assertRaises(FileExistsError):
            self.session()
        self.assertEqual(path.read_bytes(), b'')

    def test_reopen_is_inspection_only_even_valid_prefix(self):
        s = self.owned()
        self.plan(s)
        s.close()
        before = (self.path / j.JOURNAL).read_bytes()
        self.assertFalse(j.inspect_history(self.directory)['automatic_replay_allowed'])
        with self.assertRaises(FileExistsError):
            self.session()
        self.assertEqual(before, (self.path / j.JOURNAL).read_bytes())

    def test_live_writer_excludes_reader_and_second_writer(self):
        self.session()
        with self.assertRaises(BlockingIOError):
            j.inspect_history(self.directory)
        with self.assertRaises(BlockingIOError):
            self.session()

    def test_other_process_cannot_acquire_writer(self):
        self.session()
        code = '''import os,sys,json
sys.path.insert(0, sys.argv[1])
import journal as j
fd=os.open(sys.argv[2], os.O_RDONLY|os.O_DIRECTORY)
try:
 j.Session(fd,binding=json.loads(sys.argv[3]),reviewed_spec_sha256=sys.argv[4])
except BlockingIOError:
 print("LOCKED")
else:
 raise SystemExit(3)
'''
        p = subprocess.run([sys.executable, '-B', '-c', code, str(MODULES), str(self.path),
                            json.dumps(self.binding), self.pin], capture_output=True, text=True, timeout=10)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, 'LOCKED'), p.stderr)

    def test_process_exit_keeps_create_intent_and_forbids_replay(self):
        code = '''import os,sys,json
sys.path.insert(0,sys.argv[1])
import journal as j
fd=os.open(sys.argv[2],os.O_RDONLY|os.O_DIRECTORY)
s=j.Session(fd,binding=json.loads(sys.argv[3]),reviewed_spec_sha256=sys.argv[4])
os._exit(17)
'''
        p = subprocess.run([sys.executable, '-B', '-c', code, str(MODULES), str(self.path),
                            json.dumps(self.binding), self.pin], capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 17)
        self.assertEqual(j.inspect_history(self.directory)['last_event'], 'CREATE_INTENT')
        with self.assertRaises(FileExistsError):
            self.session()

    def test_forked_object_is_rejected(self):
        s = self.owned()
        pid = os.fork()
        if pid == 0:
            try:
                self.plan(s)
            except j.JournalError:
                s.close()
                os._exit(0)
            os._exit(2)
        _, status_code = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status_code), 0)
        with self.assertRaises(BlockingIOError):
            self.session()
        self.assertEqual(self.plan(s)['action'], 'DELETE_INTENT')

    def test_concurrent_thread_entry_rejected(self):
        s = self.owned()
        with s._guard:
            with self.assertRaisesRegex(j.JournalError, 'WRITER_BUSY'):
                self.plan(s)
        self.assertEqual(self.plan(s)['action'], 'DELETE_INTENT')

    def test_private_directory_mode_and_owner_required(self):
        os.chmod(self.path, 0o750)
        with self.assertRaises(j.JournalError):
            self.session()
        os.chmod(self.path, 0o700)
        with patch.object(j.os, 'geteuid', return_value=os.geteuid() + 1):
            with self.assertRaises(j.JournalError):
                self.session()
        self.assertEqual(list(self.path.iterdir()), [])

    def test_symlink_lock_not_followed(self):
        target = self.path / 'unrelated'
        target.write_text('preserve')
        (self.path / j.LOCK).symlink_to(target)
        with self.assertRaises(OSError):
            self.session()
        self.assertEqual(target.read_text(), 'preserve')

    def test_hardlinked_or_public_lock_refused(self):
        lock = self.path / j.LOCK
        lock.touch(mode=0o600)
        other = self.path / 'alias'
        os.link(lock, other)
        with self.assertRaises(j.JournalError):
            self.session()
        other.unlink()
        os.chmod(lock, 0o644)
        with self.assertRaises(j.JournalError):
            self.session()

    def test_lock_replacement_and_journal_size_change_stop_writer(self):
        s = self.owned()
        (self.path / j.LOCK).rename(self.path / 'old-lock')
        (self.path / j.LOCK).touch(mode=0o600)
        with self.assertRaisesRegex(j.JournalError, 'FILE_CHANGED'):
            self.plan(s)
        self.assert_failed(s)

    def test_external_append_stops_writer(self):
        s = self.owned()
        with (self.path / j.JOURNAL).open('ab') as f:
            f.write(b'corrupt\n')
        with self.assertRaisesRegex(j.JournalError, 'SIZE_CHANGED'):
            self.plan(s)
        self.assert_failed(s)

    def test_invalid_history_is_never_repaired(self):
        s = self.session()
        s.close()
        path = self.path / j.JOURNAL
        valid = path.read_bytes()
        cases = (b'', b'{}', b'{}\n', valid + valid,
                 valid.replace(b'"seq":1', b'"seq":true'),
                 valid.replace(b'"seq":1', b'"seq":1,"seq":1'))
        for raw in cases:
            with self.subTest(raw=raw[:30]):
                path.write_bytes(raw)
                with self.assertRaises(j.JournalError):
                    j.inspect_history(self.directory)
                self.assertEqual(path.read_bytes(), raw)

    def test_unexpected_result_before_delete_refused(self):
        s = self.owned()
        with self.assertRaises(j.JournalError):
            s.record_delete_result(200)
        self.assert_failed(s)


if __name__ == '__main__':
    unittest.main()
