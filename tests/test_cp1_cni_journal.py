"""Real filesystem/process tests; no privileged network operations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cp1_cni_journal as j


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_roundtrip_atomic_replacement_and_permissions(self):
        with j.Journal(self.path) as journal:
            journal.save({'pending': None})
            old_inode = (self.path / 'STATE.json').stat().st_ino
            journal.save({'pending': {'action': 'remove', 'entry': 'exact'}})
            self.assertNotEqual(old_inode, (self.path / 'STATE.json').stat().st_ino)
            self.assertEqual(journal.load(), {'pending': {'action': 'remove', 'entry': 'exact'}})
        self.assertEqual((self.path / 'STATE.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(p.name for p in self.path.iterdir()), ['LOCK', 'STATE.json'])

    def test_lock_required_and_other_process_cannot_acquire(self):
        journal = j.Journal(self.path)
        with self.assertRaisesRegex(ValueError, 'lock required'):
            journal.save({})
        journal.close()
        code = ('import sys; sys.path.insert(0, sys.argv[1]); from cp1_cni_journal import Journal\n'
                'with Journal(sys.argv[2]): pass\n')
        with j.Journal(self.path):
            blocked = j.Journal(self.path)
            with self.assertRaises(BlockingIOError): blocked.__enter__()
            self.assertIsNone(blocked.directory)
            self.assertIsNone(blocked.lock)
            run = subprocess.run([sys.executable, '-I', '-c', code, str(Path(j.__file__).parent), str(self.path)],
                                 capture_output=True, text=True, timeout=10)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn('BlockingIOError', run.stderr)

    def test_sigkill_retains_fsynced_intent_and_releases_lock(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); from cp1_cni_journal import Journal\n'
                'with Journal(sys.argv[2]) as journal:\n'
                ' journal.save({"pending":{"sequence":1,"action":"add","before":[],"after":["x"]}})\n'
                ' print("DURABLE",flush=True)\n'
                ' sys.stdin.read()\n')
        process = subprocess.Popen([sys.executable, '-I', '-c', code, str(Path(j.__file__).parent), str(self.path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            import selectors
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=10), 'child did not persist intent')
                self.assertEqual(process.stdout.readline().strip(), 'DURABLE')
            process.kill(); process.communicate(timeout=10)
            self.assertEqual(process.returncode, -9)
            with j.Journal(self.path) as journal:
                self.assertEqual(journal.load()['pending']['after'], ['x'])
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

    def test_write_failure_before_replace_preserves_prior_state(self):
        for function in ('fsync', 'replace'):
            with self.subTest(function=function), j.Journal(self.path) as journal:
                journal.save({'value': 'old'})
                with patch.object(j.os, function, side_effect=OSError('injected storage failure')):
                    with self.assertRaises(OSError): journal.save({'value': 'new'})
                self.assertEqual(journal.load(), {'value': 'old'})
                self.assertFalse(list(self.path.glob('STATE.tmp-*')))

    def test_directory_fsync_failure_is_reported_even_with_new_state_visible(self):
        with j.Journal(self.path) as journal:
            journal.save({'value': 'old'})
            original = j.os.fsync
            def fsync(fd):
                if fd == journal.directory:
                    raise OSError('directory fsync failed')
                return original(fd)
            with patch.object(j.os, 'fsync', side_effect=fsync), self.assertRaises(OSError):
                journal.save({'value': 'new'})
            self.assertEqual(journal.load(), {'value': 'new'})

    def test_symlink_directory_and_state_rejected_without_touching_target(self):
        link = self.path / 'alias'; link.symlink_to(self.path, target_is_directory=True)
        with self.assertRaises(OSError): j.Journal(link)
        outside = self.path / 'outside'; outside.write_text('untouched')
        (self.path / 'STATE.json').symlink_to(outside)
        with j.Journal(self.path) as journal:
            with self.assertRaises(OSError): journal.load()
            with self.assertRaises(OSError): journal.save({})
        self.assertEqual(outside.read_text(), 'untouched')

    def test_hardlink_fifo_and_nonprivate_files_rejected(self):
        state = self.path / 'STATE.json'
        state.write_text('{}'); state.chmod(0o600)
        os.link(state, self.path / 'hardlink')
        with j.Journal(self.path) as journal:
            with self.assertRaises(ValueError): journal.load()
            with self.assertRaises(ValueError): journal.save({})
        state.unlink(); os.mkfifo(state, 0o600)
        with j.Journal(self.path) as journal:
            with self.assertRaises(ValueError): journal.load()
            with self.assertRaises(ValueError): journal.save({})
        state.unlink(); state.write_text('{}'); state.chmod(0o644)
        with j.Journal(self.path) as journal:
            with self.assertRaises(ValueError): journal.load()

    def test_duplicate_nonfinite_oversized_and_nonobject_json_rejected(self):
        with j.Journal(self.path) as journal:
            for raw in ('{"x":1,"x":2}', '{"x":NaN}', '[]'):
                state = self.path / 'STATE.json'
                state.write_text(raw); state.chmod(0o600)
                with self.subTest(raw=raw), self.assertRaises(ValueError): journal.load()
            with patch.object(j, 'MAX_BYTES', 4):
                with self.assertRaises(ValueError): journal.save({'long': 'value'})
                state.write_text(' ' * 5 + '{}')
                with self.assertRaises(ValueError): journal.load()

    def test_directory_must_be_private(self):
        self.path.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'private'): j.Journal(self.path)
        self.path.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
