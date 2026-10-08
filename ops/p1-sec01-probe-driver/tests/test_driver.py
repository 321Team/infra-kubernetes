import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('driver', ROOT / 'driver.py')
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
spec = importlib.util.spec_from_file_location('prepare', ROOT / 'prepare.py')
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
NOW = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
SHA = 'a' * 64
COMMIT = 'b' * 40


def permit(mode='run'):
    return {'schema': d.SCHEMA, 'mode': mode, 'session': 'probe-test-1', 'host': d.HOST,
            'scope': 'isolated-exec-probe-only', 'bundle_sha256': SHA, 'source_commit': COMMIT,
            'executor': 'GWONUK', 'approver': 'hayeounglee',
            'go_url': 'https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-123',
            'go_body': 'SYNTHETIC TEST RECORD; NOT AN APPROVAL',
            'go_body_sha256': d.digest(b'SYNTHETIC TEST RECORD; NOT AN APPROVAL'),
            'approved_at': (NOW - timedelta(minutes=5)).isoformat(),
            'window_start': NOW.isoformat(), 'latest_start': (NOW + timedelta(minutes=5)).isoformat(),
            'window_end': (NOW + timedelta(minutes=30)).isoformat(),
            'live_go_rechecked': True, 'notice_confirmed': True, 'no_overlap_confirmed': True,
            'checked_at': NOW.isoformat()}


def evidence(work, mode='precheck', rc=0):
    """Synthetic contract fixture only, never submitted as Runtime evidence."""
    folder = work / ('p1-sec01-ac06-probe-' + mode + '-SYNTHETIC.abcdef')
    folder.mkdir(parents=True)
    raw = b'SYNTHETIC TEST ONLY\n'
    (folder / 'run.log').write_bytes(raw)
    (folder / 'SHA256SUMS').write_text(d.digest(raw) + '  run.log\n', encoding='utf-8', newline='\n')
    with tarfile.open(str(folder) + '.tar.gz', 'w:gz') as tar:
        tar.add(folder, arcname=folder.name)
    archive = Path(str(folder) + '.tar.gz')
    Path(str(archive) + '.sha256').write_text(d.digest(archive.read_bytes()) + '  ' + archive.name + '\n', encoding='utf-8', newline='\n')
    Path(str(folder) + '.sealcheck.txt').write_text('seal=OK\n', encoding='utf-8', newline='\n')
    Path(str(folder) + '.completion.txt').write_text(
        f'EXIT_CODE={rc} REV=v2.2-20261008 MODE={mode} OUT={folder.name} SEALCHECK={folder.name}.sealcheck.txt\n', encoding='utf-8', newline='\n')
    return folder


class GateTests(unittest.TestCase):
    def test_valid_each_mode(self):
        for mode in d.MODES:
            d.permit_check(permit(mode), mode, SHA, COMMIT, NOW)

    def test_mismatched_fields_and_operator_attestation(self):
        for key, value in [('schema', 'wrong'), ('mode', 'precheck'), ('bundle_sha256', 'c' * 64),
                           ('source_commit', 'd' * 40), ('host', 'harbor01'), ('session', '../other'),
                           ('scope', 'app-http'), ('executor', 'unknown'), ('approver', 'GWONUK'),
                           ('approver', 'unknown'), ('go_url', 'https://example.test/123'),
                           ('go_body_sha256', '0' * 64), ('live_go_rechecked', False),
                           ('notice_confirmed', False), ('no_overlap_confirmed', False)]:
            with self.subTest(key=key, value=value):
                x = permit(); x[key] = value
                with self.assertRaises(d.Stop): d.permit_check(x, 'run', SHA, COMMIT, NOW)

    def test_before_window_late_approval_expired_and_short_budget(self):
        for key, seconds in [('window_start', 1), ('approved_at', 1), ('latest_start', -1),
                             ('window_end', 1500), ('checked_at', 1), ('checked_at', -61)]:
            with self.subTest(key=key, seconds=seconds):
                x = permit(); x[key] = (NOW + timedelta(seconds=seconds)).isoformat()
                with self.assertRaises(d.Stop): d.permit_check(x, 'run', SHA, COMMIT, NOW)

    def test_naive_timestamp_rejected(self):
        x = permit(); x['checked_at'] = '2026-10-09T00:00:00'
        with self.assertRaises(d.Stop): d.permit_check(x, 'run', SHA, COMMIT, NOW)

    def test_blank_template_cannot_execute(self):
        x = json.loads((ROOT / 'permit.example.json').read_bytes())
        with self.assertRaises((d.Stop, TypeError)):
            d.permit_check(x, 'precheck', SHA, COMMIT, NOW)

    def test_python_optimized_mode_still_enforces_gate(self):
        code = (f'import sys; sys.path.insert(0, {str(ROOT)!r}); import driver; '
                f'driver.permit_check({permit()!r}, "run", "WRONG", {COMMIT!r}, driver.stamp({NOW.isoformat()!r}))')
        q = subprocess.run([sys.executable, '-O', '-c', code], capture_output=True)
        self.assertNotEqual(q.returncode, 0)
        self.assertIn(b'permit source mismatch', q.stderr)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def test_archive_and_failed_runtime_rc_preserved(self):
        for rc in (0, 2, 3, 12, 13):
            with self.subTest(rc=rc):
                work = self.root / str(rc); work.mkdir()
                evidence(work, 'run', rc)
                self.assertEqual(d.receipt_check(work, 'run', rc)['seal'], 'VERIFIED')

    def test_completion_cannot_hide_nonzero(self):
        evidence(self.root, 'run', 3)
        with self.assertRaises(d.Stop): d.receipt_check(self.root, 'run', 0)

    def test_changed_file_rejected(self):
        folder = evidence(self.root)
        (folder / 'run.log').write_bytes(b'changed')
        with self.assertRaises(d.Stop): d.receipt_check(self.root, 'precheck', 0)

    def test_extra_file_rejected(self):
        folder = evidence(self.root)
        (folder / 'extra').write_bytes(b'X')
        with self.assertRaises(d.Stop): d.receipt_check(self.root, 'precheck', 0)

    def test_manifest_parent_traversal_rejected(self):
        folder = evidence(self.root)
        (folder / 'SHA256SUMS').write_text(SHA + '  ../outside\n')
        with self.assertRaises(d.Stop): d.receipt_check(self.root, 'precheck', 0)

    def test_tar_extra_symlink_duplicate_and_changed_member_rejected(self):
        for kind in ('extra', 'symlink', 'duplicate', 'changed'):
            with self.subTest(kind=kind):
                work = self.root / kind; work.mkdir()
                folder = evidence(work)
                archive = Path(str(folder) + '.tar.gz')
                with tarfile.open(archive, 'w:gz') as tar:
                    tar.add(folder, arcname=folder.name)
                    item = tarfile.TarInfo(folder.name + ('/run.log' if kind in ('duplicate', 'changed') else '/extra'))
                    if kind == 'symlink':
                        item.type = tarfile.SYMTYPE; item.linkname = '/etc/shadow'; tar.addfile(item)
                    else:
                        item.size = 1; tar.addfile(item, io.BytesIO(b'X'))
                Path(str(archive) + '.sha256').write_text(d.digest(archive.read_bytes()) + '  ' + archive.name + '\n')
                with self.assertRaises(d.Stop): d.receipt_check(work, 'precheck', 0)

    def test_precheck_freshness_boot_and_source_binding(self):
        work = self.root / 'precheck'; work.mkdir(); evidence(work)
        result = {'script_rc': 0, 'driver_rc': 0, 'bundle_sha256': SHA, 'source_commit': COMMIT,
                  'boot_id': 'boot1', 'finished_at': NOW.isoformat(), 'finished_monotonic': 100}
        (work / 'result.json').write_bytes(d.json_bytes(result))
        d.precheck_check(self.root, SHA, COMMIT, NOW, 100, 'boot1')
        for now, mono, boot, sha in [(NOW, 401, 'boot1', SHA), (NOW, 99, 'boot1', SHA),
                                    (NOW + timedelta(seconds=301), 101, 'boot1', SHA),
                                    (NOW, 101, 'boot2', SHA), (NOW, 101, 'boot1', 'bad')]:
            with self.subTest(mono=mono, boot=boot, sha=sha):
                with self.assertRaises(d.Stop): d.precheck_check(self.root, sha, COMMIT, now, mono, boot)
        result['script_rc'] = 3
        (work / 'result.json').write_bytes(d.json_bytes(result))
        with self.assertRaises(d.Stop): d.precheck_check(self.root, SHA, COMMIT, NOW, 100, 'boot1')

    def test_collect_original_bytes_including_failure_and_no_unlisted_files(self):
        session = self.root / 'session'; work = session / 'run'; work.mkdir(parents=True)
        folder = evidence(work, 'run', 3)
        receipt = d.receipt_check(work, 'run', 3)
        for name in ('permit.json', 'BUNDLE.json', 'started.json', 'stdout.txt', 'stderr.txt'):
            (work / name).write_bytes(b'fixture')
        (work / 'result.json').write_bytes(d.json_bytes({'mode': 'run', 'script_rc': 3, 'driver_rc': 3, 'receipt': receipt}))
        (work / 'unlisted-secret').write_bytes(b'DO NOT EXPORT')
        out = self.root / 'out'
        d.collect(session, out)
        self.assertFalse((out / 'run/unlisted-secret').exists())
        for suffix in ('.tar.gz', '.tar.gz.sha256'):
            self.assertEqual((out / 'run' / (folder.name + suffix)).read_bytes(), Path(str(folder) + suffix).read_bytes())
        self.assertEqual(json.loads((out / 'run/result.json').read_bytes())['script_rc'], 3)
        with self.assertRaises(d.Stop): d.collect(session, out)

    def test_incomplete_session_not_exported_as_complete(self):
        (self.root / 'run').mkdir()
        with self.assertRaises(OSError): d.collect(self.root, self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_bounded_regular_read(self):
        f = self.root / 'big'; f.write_bytes(b'123')
        with self.assertRaises(d.Stop): d.read(f, 2)
        if os.name != 'nt':
            s = self.root / 'link'; s.symlink_to(f)
            with self.assertRaises(d.Stop): d.read(s)
            h = self.root / 'hard'; os.link(f, h)
            with self.assertRaises(d.Stop): d.read(h)

    def test_process_detection_exact_basename_and_nul_boundary(self):
        for args, blocked in [(b'/bin/bash\0/x/probe.sh\0run\0', True),
                              (b'/bin/bash\0/x/backup.sh\0run\0', True),
                              (b'/bin/bash\0/x/not-probe.sh\0', False)]:
            q = self.root / '123'; q.mkdir(exist_ok=True); (q / 'cmdline').write_bytes(args)
            if blocked:
                with self.assertRaises(d.Stop): d.no_other_tests(self.root)
            else: d.no_other_tests(self.root)


class BundleTests(unittest.TestCase):
    def test_build_uses_git_blobs_not_dirty_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); repo = root / 'repo'; repo.mkdir()
            def git(*args): return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL)
            git('init'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'user.name', 'Fixture')
            git('config', 'core.autocrlf', 'false')
            src = repo / p.PREFIX; src.mkdir(parents=True)
            for name in p.NAMES: (src / name).write_bytes(b'original\n')
            (src / 'SHA256SUMS').write_text(''.join(d.digest(b'original\n') + '  ' + name + '\n' for name in p.NAMES))
            drv = repo / p.DRIVER; drv.parent.mkdir(parents=True); drv.write_bytes((ROOT / 'driver.py').read_bytes())
            git('add', '.'); git('commit', '-qm', 'fixture'); commit = git('rev-parse', 'HEAD').decode().strip()
            (src / 'probe.sh').write_bytes(b'DIRTY')
            out = root / 'bundle'; result = p.build(repo, commit, out)
            self.assertEqual((out / 'source/probe.sh').read_bytes(), b'original\n')
            d.bundle_check(out, result['bundle_sha256'])
            (out / 'source/probe.sh').write_bytes(b'tampered')
            with self.assertRaises(d.Stop): d.bundle_check(out, result['bundle_sha256'])
            with self.assertRaises(ValueError): p.build(repo, 'main', root / 'other')


@unittest.skipUnless(sys.platform == 'linux', 'Linux process/flock semantics')
class LinuxTests(unittest.TestCase):
    def test_actual_probe_failure_receipt_compatibility_with_mock_api(self):
        source = ROOT.parent / 'p1-sec01-isolated-tests'
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            env = dict(os.environ, KC=sys.executable + ' ' + str(source / 'tests/fake_kubectl.py'),
                       CONFIRM='YES', SCENARIO='t0_restart', MOCK_STATE=str(work / 'state.json'))
            q = subprocess.run(['/bin/bash', str(source / 'probe.sh'), 'run'], cwd=work, env=env,
                               capture_output=True, timeout=90)
            self.assertEqual(q.returncode, 3, q.stdout + q.stderr)
            self.assertEqual(d.receipt_check(work, 'run', 3)['seal'], 'VERIFIED')

    def test_execute_precheck_run_and_global_go_replay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); bundle = root / 'bundle'; (bundle / 'source').mkdir(parents=True)
            (bundle / 'driver.py').write_bytes((ROOT / 'driver.py').read_bytes())
            for name in d.FILES: (bundle / 'source' / name).write_bytes(b'FIXTURE')
            names = ['driver.py'] + ['source/' + x for x in d.FILES]
            meta = {'schema': d.SCHEMA, 'commit': COMMIT,
                    'files': {name: d.digest((bundle / name).read_bytes()) for name in names}}
            (bundle / 'BUNDLE.json').write_bytes(d.json_bytes(meta))
            sha = d.digest((bundle / 'BUNDLE.json').read_bytes())
            def private(path, create=False):
                if create: path.mkdir(mode=0o700, exist_ok=True)
            def child(_bundle, mode, work):
                (work / 'stdout.txt').write_bytes(b'fixture')
                (work / 'stderr.txt').write_bytes(b'')
                evidence(work, mode, 0 if mode == 'precheck' else 3)
                return (0 if mode == 'precheck' else 3), ['FIXTURE']
            original = d.os.fstat
            def root_stat(fd):
                s = original(fd)
                # Preserve nanosecond fields used by read(); change uid via proxy.
                class Proxy:
                    st_uid = 0
                    def __getattr__(self, key): return getattr(s, key)
                return Proxy()
            path = root / 'permit.json'
            with patch.object(d, 'STATE', root / 'state'), patch.object(d, 'private_dir', side_effect=private), \
                 patch.object(d.os, 'geteuid', return_value=0), patch.object(d.os, 'fstat', side_effect=root_stat), \
                 patch.object(d.socket, 'gethostname', return_value=d.HOST), patch.object(d, 'utc', return_value=NOW), \
                 patch.object(d, 'no_other_tests'), patch.object(d, 'call_probe', side_effect=child) as call:
                x = permit('precheck'); x['bundle_sha256'] = sha
                path.write_bytes(d.json_bytes(x)); self.assertEqual(d.execute(bundle, sha, path), 0)
                x['mode'] = 'run'; path.write_bytes(d.json_bytes(x))
                self.assertEqual(d.execute(bundle, sha, path), 3)
                self.assertEqual(call.call_count, 2)
                # Same GO in a newly named session still cannot repeat precheck/run.
                x['mode'] = 'precheck'; x['session'] = 'new-name'; path.write_bytes(d.json_bytes(x))
                with self.assertRaises(FileExistsError): d.execute(bundle, sha, path)
                self.assertEqual(call.call_count, 2)

    def test_signal_forwarding_waits_for_shell_finalization(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / 'source').mkdir(); work = root / 'work'; work.mkdir()
            (root / 'source/probe.sh').write_text(
                "#!/bin/bash\ntrap 'echo cleaned; exit 3' TERM\necho started\nwhile true; do sleep 0.1; done\n")
            # A separate harness executes the REAL driver call/handler, not kubectl.
            code = (f'import sys; from pathlib import Path; sys.path.insert(0, {str(ROOT)!r}); import driver; '
                    f'rc, argv=driver.call_probe(Path({str(root)!r}), "run", Path({str(work)!r})); sys.exit(rc)')
            process = subprocess.Popen([sys.executable, '-c', code])
            import time
            try:
                for _ in range(100):
                    if (work / 'stdout.txt').exists() and b'started' in (work / 'stdout.txt').read_bytes(): break
                    time.sleep(0.02)
                else: self.fail('child did not start')
                process.terminate()
                self.assertEqual(process.wait(timeout=5), 3)
                self.assertIn(b'cleaned', (work / 'stdout.txt').read_bytes())
            finally:
                if process.poll() is None: process.kill(); process.wait()

    def test_environment_is_allowlisted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = unittest.mock.Mock(); fake.wait.return_value = 3
            with patch.dict(os.environ, {'BASH_ENV': '/malicious', 'KC': 'bad', 'PYTHONPATH': '/bad'}), \
                 patch.object(d.subprocess, 'Popen', return_value=fake) as call:
                rc, argv = d.call_probe(root, 'run', root)
                env = call.call_args.kwargs['env']
                self.assertNotIn('BASH_ENV', env); self.assertNotIn('PYTHONPATH', env)
                self.assertEqual(env['KC'], d.KC); self.assertEqual(rc, 3)
                self.assertTrue(call.call_args.kwargs['start_new_session'])

    def test_concurrent_lock_and_claim_replay(self):
        # No root needed: exercise actual flock with uid patched to this test runner.
        import fcntl
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            with patch.object(d, 'private_dir'):
                original = d.os.fstat
                def root_stat(fd):
                    s = original(fd)
                    values = list(s); values[4] = 0
                    return os.stat_result(values)
                with patch.object(d.os, 'fstat', side_effect=root_stat):
                    with d.lock(path):
                        with self.assertRaises(d.Stop):
                            with d.lock(path): pass
            claim = path / 'claim'
            d.write_new(claim, b'first failed attempt')
            with self.assertRaises(FileExistsError): d.write_new(claim, b'replay')
            self.assertEqual(claim.read_bytes(), b'first failed attempt')


if __name__ == '__main__': unittest.main()
