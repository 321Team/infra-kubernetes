"""Filesystem crash-boundary tests; no real systemctl/firewall invocation.

The staged entrypoint is deliberately harmless mock bytes. These tests do
not claim that an operational entrypoint exists or has been runtime tested.
"""
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_cp1_cni_membership import lease, Crash
from test_cp1_cni_guard import unit_fixture
import cp1_cni_guard as guard
import cp1_cni_staging as staging
import cp1_cni_unit_files as u
from cp1_cni_journal import Journal


@unittest.skipUnless(os.geteuid() == 0, 'requires root temporary-file ownership checks')
class UnitFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cni-unit-files-test-', dir=Path.home())
        self.base = Path(self.tmp.name)
        self.src, self.dst, self.units, self.jdir = [self.base / p for p in ('src', 'staged', 'units', 'journal')]
        for path in (self.src, self.dst, self.units, self.jdir):
            path.mkdir(mode=0o700)
        (self.src / 'scripts').mkdir(mode=0o700)
        self.payload = b'# Mock entrypoint only: no operations or runtime authority.\n'
        (self.src / u.ENTRYPOINT).write_bytes(self.payload)
        self.manifest = {u.ENTRYPOINT: hashlib.sha256(self.payload).hexdigest()}
        self.sha = u.fingerprint(self.manifest)
        self.lease = lease(); self.lease['source_sha256'] = self.sha
        self.run = self.lease['run_id']
        self.patches = [patch.object(guard, 'ROOT', self.dst), patch.object(guard, 'UNITS', self.units)]
        for value in self.patches: value.start()
        staging.stage_sources(self.src, self.dst, self.run, self.manifest, self.sha)
        self.journal = Journal(self.jdir).__enter__()
        self.state = {name: {'Id': name, 'LoadState': 'not-found', 'ActiveState': 'inactive', 'SubState': 'dead',
                            'MainPID': '0', 'Job': '', 'FragmentPath': '', 'DropInPaths': '', 'UnitFileState': ''}
                      for name in guard.names(self.run).values()}
        self.calls = []

    def tearDown(self):
        self.journal.close()
        for value in reversed(self.patches): value.stop()
        self.tmp.cleanup()

    def writer(self, journal=None):
        return u.UnitFiles(self.lease, self.dst, self.units, self.manifest, self.sha,
                           journal or self.journal, lambda: copy.deepcopy(self.state))

    def loaded(self):
        observed = unit_fixture(self.lease)
        observed['links'] = {}
        for props in observed['properties'].values():
            props.update(ActiveState='inactive', SubState='dead', MainPID='0', Job='', UnitFileState='static')
        self.state = copy.deepcopy(observed['properties'])
        return observed

    def command(self, argv, timeout):
        self.assertEqual(argv, ['systemctl', 'daemon-reload'])
        self.assertEqual(timeout, 5)
        self.assertEqual(self.journal.load()['phase'], 'LOAD_INTENT')
        self.calls.append(argv)
        return {'rc': 0, 'stdout': '', 'stderr': '', 'truncated': False}

    def test_stage_and_load_exact_definitions_without_arming(self):
        result = self.writer().load(self.command, self.loaded)
        self.assertEqual(result['phase'], 'DEFINITIONS_LOADED')
        self.assertFalse(result['recovery_armed']); self.assertFalse(result['authorizes_apply'])
        self.assertEqual(set(p.name for p in self.units.iterdir()), set(guard.documents(self.lease)))
        for name, content in guard.documents(self.lease).items():
            self.assertEqual((self.units / name).read_text(), content)
            self.assertEqual((self.units / name).stat().st_mode & 0o777, 0o400)
        self.assertEqual(self.calls, [['systemctl', 'daemon-reload']])

    def test_absent_operational_entrypoint_rejected_before_any_file(self):
        self.manifest = {'scripts/other.py': 'a' * 64}
        self.sha = u.fingerprint(self.manifest); self.lease['source_sha256'] = self.sha
        with self.assertRaisesRegex(ValueError, 'entrypoint absent'): self.writer()
        self.assertEqual(list(self.units.iterdir()), [])

    def test_lease_source_binding_and_staged_bytes_are_independent_gates(self):
        self.lease['source_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'lease source'): self.writer()
        self.lease['source_sha256'] = self.sha
        staged = self.dst / self.run / u.ENTRYPOINT
        staged.chmod(0o600); staged.write_bytes(b'changed'); staged.chmod(0o400)
        with self.assertRaisesRegex(ValueError, 'source bytes'): self.writer().stage()
        self.assertFalse((self.jdir / 'STATE.json').exists())

    def test_existing_identical_unit_is_not_adopted(self):
        name = sorted(guard.documents(self.lease))[0]
        (self.units / name).write_text(guard.documents(self.lease)[name]); (self.units / name).chmod(0o400)
        with self.assertRaisesRegex(ValueError, 'preexisting'): self.writer().stage()
        self.assertFalse((self.jdir / 'STATE.json').exists())
        self.assertEqual(len(list(self.units.iterdir())), 1)

    def test_any_late_file_collision_prevents_all_writes(self):
        name = sorted(guard.documents(self.lease))[-1]
        (self.units / name).write_bytes(b'foreign'); (self.units / name).chmod(0o400)
        with self.assertRaises(ValueError): self.writer().stage()
        self.assertEqual([p.name for p in self.units.iterdir()], [name])

    def test_foreign_symlink_and_hardlink_not_followed(self):
        name = sorted(self.state)[0]; target = self.units / name
        target.symlink_to('/etc/passwd')
        with self.assertRaises(OSError): self.writer().stage()
        target.unlink(); origin = self.base / 'foreign'; origin.write_text('foreign'); origin.chmod(0o400)
        os.link(origin, target)
        with self.assertRaisesRegex(ValueError, 'unsafe source'): self.writer().stage()

    def test_writable_unit_parent_is_rejected(self):
        self.units.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'ancestor'): self.writer().stage()
        self.assertFalse((self.jdir / 'STATE.json').exists())

    def test_preexisting_loaded_unit_without_disk_file_is_not_adopted(self):
        name = sorted(self.state)[0]
        self.state[name].update(LoadState='loaded', FragmentPath='/run/systemd/transient/' + name)
        with self.assertRaises(ValueError): self.writer().stage()
        self.assertEqual(list(self.units.iterdir()), [])

    def test_pending_job_running_failed_enabled_and_foreign_dropin_block(self):
        name = sorted(self.state)[0]; original = copy.deepcopy(self.state)
        edits = ({'Job': '9/start'}, {'ActiveState': 'active', 'MainPID': '123'}, {'ActiveState': 'failed'},
                 {'LoadState': 'loaded', 'UnitFileState': 'enabled'}, {'DropInPaths': '/run/foreign.conf'})
        for change in edits:
            self.state = copy.deepcopy(original); self.state[name].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError): self.writer().stage()
            self.assertEqual(list(self.units.iterdir()), [])

    def test_persist_failure_before_first_intent_cannot_publish(self):
        with patch.object(self.journal, 'save', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.writer().stage()
        self.assertEqual(list(self.units.iterdir()), [])

    def test_durable_intent_precedes_each_publication(self):
        real = u.publish
        def check(fd, name, content):
            saved = self.journal.load()
            self.assertEqual(saved['pending'], name)
            self.assertNotIn(name, saved['completed'])
            real(fd, name, content)
        with patch.object(u, 'publish', side_effect=check): self.writer().stage()
        self.assertEqual(len(self.journal.load()['completed']), 4)

    def test_crash_after_each_publication_resumes_same_intent_without_overwrite(self):
        real = u.publish
        for index in range(4):
            def crash(fd, name, content):
                real(fd, name, content); raise Crash()
            with patch.object(u, 'publish', side_effect=crash), self.assertRaises(Crash): self.writer().stage()
            saved = self.journal.load()
            self.assertEqual(len(saved['completed']), index)
            self.assertEqual(saved['pending'], sorted(self.state)[index])
            self.assertEqual(len(list(self.units.iterdir())), index + 1)
        result = self.writer().stage()
        self.assertEqual(result['phase'], 'FILES_READY')

    def test_resuming_unknown_pending_bytes_requires_review_and_preserves_file(self):
        def crash(*_): raise Crash()
        with patch.object(u, 'publish', side_effect=crash), self.assertRaises(Crash): self.writer().stage()
        name = self.journal.load()['pending']; path = self.units / name
        path.write_bytes(b'unknown bytes'); path.chmod(0o400)
        with self.assertRaisesRegex(ValueError, 'pending unit'): self.writer().stage()
        self.assertEqual(path.read_bytes(), b'unknown bytes')

    def test_pending_file_appearing_after_intent_is_never_overwritten(self):
        real = u.publish
        def race(fd, name, content):
            path = self.units / name; path.write_bytes(b'foreign race'); path.chmod(0o400)
            real(fd, name, content)
        with patch.object(u, 'publish', side_effect=race), self.assertRaises(FileExistsError): self.writer().stage()
        name = self.journal.load()['pending']
        self.assertEqual((self.units / name).read_bytes(), b'foreign race')

    def test_missing_committed_file_does_not_trigger_silent_recreation(self):
        self.writer().stage(); (self.units / sorted(self.state)[0]).unlink()
        with self.assertRaisesRegex(ValueError, 'committed unit'): self.writer().stage()

    def test_mutated_journal_cannot_claim_armed_or_change_run(self):
        self.writer().stage(); saved = self.journal.load()
        for field, value in (('recovery_armed', True), ('authorizes_apply', True), ('phase', 'READY_TO_APPLY'),
                             ('completed', list(reversed(saved['completed'])))):
            bad = copy.deepcopy(saved); bad[field] = value; self.journal.save(bad)
            with self.subTest(field=field), self.assertRaises(ValueError): self.writer().stage()
        bad = copy.deepcopy(saved); bad['binding']['run_id'] = 'other'; self.journal.save(bad)
        with self.assertRaisesRegex(ValueError, 'binding'): self.writer().stage()

    def test_reload_timeout_keeps_durable_intent_and_safe_retry(self):
        def timeout(*args, **kwargs): raise TimeoutError('unknown completion')
        with self.assertRaises(TimeoutError): self.writer().load(timeout, self.loaded)
        self.assertEqual(self.journal.load()['phase'], 'LOAD_INTENT')
        self.assertFalse(self.journal.load()['definitions_loaded'])
        self.assertEqual(self.writer().load(self.command, self.loaded)['phase'], 'DEFINITIONS_LOADED')

    def test_false_zero_warnings_truncation_and_output_do_not_complete_loading(self):
        for result in ({'rc': False, 'stdout': '', 'stderr': '', 'truncated': False},
                       {'rc': 0, 'stdout': '', 'stderr': 'warning', 'truncated': False},
                       {'rc': 0, 'stdout': '', 'stderr': '', 'truncated': True},
                       {'rc': 0, 'stdout': 'unexpected', 'stderr': '', 'truncated': False}):
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.writer().load(lambda *a, **kw: result, self.loaded)
            self.assertEqual(self.journal.load()['phase'], 'LOAD_INTENT')

    def test_loaded_wrong_argv_rejected_even_when_source_files_match(self):
        def stale():
            observed = self.loaded()
            observed['properties'][guard.names(self.run)['work']]['ExecStart'] = '/bin/true'
            return observed
        with self.assertRaisesRegex(ValueError, 'loaded unit command'):
            self.writer().load(self.command, stale)
        self.assertEqual(self.journal.load()['phase'], 'LOAD_INTENT')

    def test_loaded_files_do_not_satisfy_armed_guard(self):
        self.writer().load(self.command, self.loaded)
        with self.assertRaises(ValueError): guard.verified_guard(self.loaded(), self.lease, {'monotonic': 110})

    def test_source_tamper_during_reload_prevents_completion(self):
        def mutate(argv, timeout):
            result = self.command(argv, timeout)
            path = self.dst / self.run / u.ENTRYPOINT
            path.chmod(0o600); path.write_text('changed'); path.chmod(0o400)
            return result
        with self.assertRaisesRegex(ValueError, 'source bytes'):
            self.writer().load(mutate, self.loaded)
        self.assertEqual(self.journal.load()['phase'], 'LOAD_INTENT')

    def test_live_service_started_during_reload_prevents_completion(self):
        def active():
            value = self.loaded(); value['properties'][guard.names(self.run)['work']]['MainPID'] = '123'
            return value
        with self.assertRaisesRegex(ValueError, 'running'):
            self.writer().load(self.command, active)

    def test_loader_cannot_accept_a_boot_or_timer_enablement_link(self):
        def enabled():
            value = self.loaded(); value['links']['timers.target.wants/foreign'] = 'foreign'
            return value
        with self.assertRaisesRegex(ValueError, 'enablement links'):
            self.writer().load(self.command, enabled)

    def test_timer_need_not_expose_service_main_pid_property(self):
        timer = guard.names(self.run)['timer']; self.state[timer].pop('MainPID')
        self.writer().stage()
        value = self.loaded(); value['properties'][timer].pop('MainPID')
        self.writer().load(self.command, lambda: value)

    def test_verified_sources_at_another_path_cannot_load_wrong_exec_target(self):
        for source, destination in ((self.src, self.units), (self.dst, self.jdir)):
            with self.subTest(source=source, destination=destination), self.assertRaisesRegex(ValueError, 'compiled paths'):
                u.UnitFiles(self.lease, source, destination, self.manifest, self.sha, self.journal, lambda: self.state)

    def test_directory_sync_failure_leaves_intent_and_reestablishes_durability(self):
        real_sync = u.os.fsync
        def fail_unit_directory(fd):
            if os.fstat(fd).st_ino == self.units.stat().st_ino:
                raise OSError('unit directory sync failed')
            real_sync(fd)
        with patch.object(u.os, 'fsync', side_effect=fail_unit_directory), self.assertRaises(OSError):
            self.writer().stage()
        self.assertIsNotNone(self.journal.load()['pending'])
        self.assertEqual(self.writer().stage()['phase'], 'FILES_READY')

    def test_unclean_process_death_with_temporary_hardlink_cannot_claim_complete(self):
        def die(fd, name, content):
            path = self.units / name
            path.write_bytes(content); path.chmod(0o400)
            os.link(path, self.units / ('.' + name + '.unlinked-after-crash.tmp'))
            raise Crash()
        with patch.object(u, 'publish', side_effect=die), self.assertRaises(Crash): self.writer().stage()
        with self.assertRaisesRegex(ValueError, 'unsafe source'): self.writer().stage()
        self.assertIsNotNone(self.journal.load()['pending'])
        self.assertFalse(self.journal.load()['definitions_loaded'])

    def test_failure_of_final_completion_save_cannot_report_loaded_success(self):
        real_save = self.journal.save
        def fail_last(value):
            if value['phase'] == 'DEFINITIONS_LOADED': raise OSError('final sync failed')
            real_save(value)
        with patch.object(self.journal, 'save', side_effect=fail_last), self.assertRaises(OSError):
            self.writer().load(self.command, self.loaded)
        self.assertEqual(self.journal.load()['phase'], 'LOAD_INTENT')
        self.assertFalse(self.journal.load()['definitions_loaded'])


if __name__ == '__main__': unittest.main()
