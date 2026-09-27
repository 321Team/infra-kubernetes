"""Evidence/terminal-unit regressions; no firewall, VM or runtime qualification.

Capture producer/reaping tests use real local child processes and files. systemd
state transitions are mocked; target VM/systemd integration remains NOT_TESTED.
"""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('capture_finalization', SCRIPTS / 'run-cni-kernel-integration.py')
tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)


class CaptureTests(unittest.TestCase):
    def test_http_match_with_empty_pcap_is_evidence_failure_not_connectivity_failure(self):
        result = tool.flow_outcome(dict(tcp_connected=True, status=200, body='job/b'),
                                   True, 'job/b', False, True, [])
        self.assertEqual(result['communication_status'], 'MATCH')
        self.assertEqual(result['communication_errors'], [])
        self.assertEqual(result['evidence_errors'], ['PACKET_CAPTURE_MISSING'])
        self.assertFalse(result['expected_result_matches'])
        self.assertEqual(result['verdict'], 'COMPONENT_FAILED_REVIEW_REQUIRED')

    def test_negative_timeout_without_ingress_is_not_a_proven_drop(self):
        result = tool.flow_outcome(dict(tcp_connected=False), False, 'job/b', False, False, [])
        self.assertEqual(result['communication_status'], 'MATCH')
        self.assertEqual(result['evidence_status'], 'INCOMPLETE')
        self.assertFalse(result['expected_result_matches'])

    def test_unmeasured_probe_remains_not_tested(self):
        result = tool.flow_outcome({}, True, 'job/b', True, True, [])
        self.assertEqual(result['communication_status'], 'NOT_TESTED')
        self.assertFalse(result['expected_result_matches'])

    def test_connected_negative_remains_failure_even_with_complete_evidence(self):
        result = tool.flow_outcome(dict(tcp_connected=True, error_stage='HTTP'), False, 'job/b', True, True, [])
        self.assertEqual(result['communication_status'], 'MISMATCH')
        self.assertEqual(result['evidence_status'], 'PRESENT_REVIEW_REQUIRED')

    def test_bad_capture_exit_cannot_pass_even_when_files_exist(self):
        result = tool.flow_outcome(dict(tcp_connected=True, status=200, body='job/b'),
                                   True, 'job/b', True, True, ['TCPDUMP_EXIT_ERROR'])
        self.assertEqual(result['communication_status'], 'MATCH')
        self.assertFalse(result['expected_result_matches'])

    def test_waits_for_delayed_bytes_then_reaps_actual_child_with_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); capture=root/'packet.pcap'; trace=root/'packet.trace'
            capture.write_bytes(b'\0'*24); trace.write_bytes(b'')
            child = """import pathlib, signal, sys, time
cap, trace = map(pathlib.Path, sys.argv[1:])
def stop(signum, frame):
    print('1 packet captured', file=sys.stderr, flush=True)
    raise SystemExit(0)
signal.signal(signal.SIGINT, stop)
print('listening on mock', file=sys.stderr, flush=True)
time.sleep(0.4)
with cap.open('ab') as f: f.write(b'packet-record')
trace.write_bytes(b'trace-record')
while True: time.sleep(1)
"""
            with (root/'stderr.log').open('wb') as error:
                process = subprocess.Popen([sys.executable, '-c', child, str(capture), str(trace)],
                                           stderr=error, start_new_session=True)
                try:
                    started = time.monotonic()
                    self.assertEqual(tool.wait_for_observation(capture, trace, [process], lambda: None), 'BYTES_AVAILABLE')
                    self.assertGreater(time.monotonic()-started, 0.3)
                    stopped = tool.stop_observer(process)
                    self.assertEqual(stopped['exit_code'], 0)
                    self.assertFalse(stopped['forced_kill'])
                finally:
                    if process.poll() is None: process.kill(); process.wait()
            self.assertIn('1 packet captured', (root/'stderr.log').read_text())

    def test_header_only_capture_wait_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=Path(directory)/'header.pcap'; capture.write_bytes(b'\0'*24)
            trace=Path(directory)/'trace'; trace.write_bytes(b'trace')
            with patch.object(tool.time, 'monotonic', side_effect=[0, 0, 4]), patch.object(tool.time, 'sleep'):
                self.assertEqual(tool.wait_for_observation(capture, trace, [Mock(poll=lambda:None)], lambda:None),
                                 'DEADLINE_REACHED')

    def test_exited_observer_interrupts_wait(self):
        with self.assertRaisesRegex(ValueError, 'process exited'):
            tool.wait_for_observation(Path('/unused'), Path('/unused'), [Mock(poll=lambda:1)], lambda:None)

    def test_sigint_timeout_escalation_is_recorded(self):
        process=Mock(pid=123, poll=lambda:None)
        process.wait.side_effect=[subprocess.TimeoutExpired('observer', 2), -signal.SIGKILL]
        with patch.object(tool.os,'killpg') as kill:
            result=tool.stop_observer(process)
        self.assertTrue(result['forced_kill']); self.assertEqual(result['exit_code'], -signal.SIGKILL)
        self.assertEqual([c.args[1] for c in kill.call_args_list], [signal.SIGINT, signal.SIGKILL])


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.job=self.root/('a'*16); self.job.mkdir(mode=0o700)
        self.units=self.root/'units'; self.units.mkdir()
        for name in tool.SOURCES: (self.job/name).write_bytes((SCRIPTS/name).read_bytes())
        self.request=dict(scenario='normal', observed=dict(sources_sha256={
            n:hashlib.sha256((self.job/n).read_bytes()).hexdigest() for n in tool.SOURCES}))
        self.docs=tool.documents(self.job, 'normal')
        self.result=dict(cleanup_complete=True, recovery_disarmed=True, disarm_intent=True,
                         unit_cleanup_pending=True, unit_cleanup_complete=False,
                         status='COMPONENT_FAILED_REVIEW_REQUIRED', component_error='ORIGINAL_FAILURE')
        for name, data in [('REQUEST.json',self.request), ('UNITS.json',self.docs), ('RESULT.json',self.result)]:
            (self.job/name).write_text(json.dumps(data))
        for name, body in self.docs.items(): (self.units/name).write_text(body)
        self.calls=[]; self.busy=False; self.enabled=False; self.reload_failure=False
        self.stack.enter_context(patch.object(tool,'ROOT',self.root))
        self.stack.enter_context(patch.object(tool,'HERE',self.job))
        self.stack.enter_context(patch.object(tool,'UNITS',self.units))
        # /tmp is deliberately not a production-safe root. The shared node
        # security suite covers that guard; retain real reads/atomic writes here.
        def safe_dir(path, create=False):
            if create: path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.stack.enter_context(patch.object(tool.node,'safe_dir',side_effect=safe_dir))
        self.stack.enter_context(patch.object(tool,'system',side_effect=self.system))
        self.stack.enter_context(patch.object(tool,'daemon_journal',return_value=(b'',dict(status='MOCK'))))

    def system(self, args, **kwargs):
        self.calls.append(args)
        if args[0]=='is-enabled': return 'enabled' if self.enabled else 'disabled'
        if args[0]=='show':
            if '--value' in args: return 'inactive'
            if self.busy and args[1].endswith('work.service'): return 'ActiveState=activating\nMainPID=123\nJob=1'
            return 'ActiveState=failed\nMainPID=0\nJob=\nResult=exit-code\nExecMainCode=1\nExecMainStatus=1'
        if args==['daemon-reload'] and self.reload_failure: raise ValueError('MOCK_RELOAD_FAILURE')
        return ''

    def test_disarm_does_not_delete_or_reload_running_work_definition(self):
        self.busy=True
        lab=SimpleNamespace(job=self.job, result=dict(self.result, recovery_disarmed=False))
        tool.disarm(lab, self.docs)
        self.assertTrue(lab.result['recovery_disarmed'])
        self.assertTrue(lab.result['unit_cleanup_pending'])
        self.assertEqual({p.name for p in self.units.iterdir()}, set(self.docs))
        self.assertNotIn(['daemon-reload'], self.calls)

    def test_enabled_trigger_cannot_be_reported_disarmed(self):
        self.enabled=True
        lab=SimpleNamespace(job=self.job, result=dict(self.result, recovery_disarmed=False))
        with self.assertRaisesRegex(ValueError, 'remains enabled'): tool.disarm(lab, self.docs)
        self.assertFalse(lab.result['recovery_disarmed'])

    def test_running_or_queued_work_blocks_all_unit_removal(self):
        self.busy=True
        with patch.object(tool.time,'monotonic',side_effect=[0,6]):
            with self.assertRaisesRegex(ValueError, 'still running or queued'):
                tool.finalize_units(self.job,self.result)
        self.assertEqual({p.name for p in self.units.iterdir()},set(self.docs))
        self.assertNotIn(['daemon-reload'],self.calls)

    def test_completed_failed_work_is_removed_and_original_failure_preserved(self):
        tool.finalize_units(self.job,self.result)
        self.assertEqual(list(self.units.iterdir()),[])
        self.assertTrue(self.result['unit_cleanup_complete'])
        self.assertFalse(self.result['unit_cleanup_pending'])
        self.assertEqual(self.result['component_error'],'ORIGINAL_FAILURE')
        state=json.loads((self.job/'UNIT_EXIT_STATE.json').read_text())
        self.assertEqual(state[tool.unit_name(self.job,'work')+'.service']['ExecMainStatus'],'1')

    def test_foreign_or_symlink_unit_prevents_any_removal(self):
        name=next(reversed(self.docs))
        path=self.units/name; path.write_text('FOREIGN')
        with self.assertRaisesRegex(ValueError,'ownership mismatch'): tool.finalize_units(self.job,self.result)
        self.assertEqual(len(list(self.units.iterdir())),len(self.docs))
        path.unlink(); path.symlink_to(self.root/'missing')
        with self.assertRaisesRegex(ValueError,'symlink'): tool.finalize_units(self.job,self.result)
        self.assertEqual(len(list(self.units.iterdir())),len(self.docs))

    def test_changed_staged_source_prevents_systemctl(self):
        (self.job/tool.SOURCES[0]).write_text('CHANGED')
        with self.assertRaisesRegex(ValueError,'Staged source changed'): tool.finalize_units(self.job,self.result)
        self.assertEqual(self.calls,[])

    def test_other_checkout_dispatches_staged_collector_before_mutating(self):
        with patch.object(tool,'HERE',SCRIPTS), patch.object(tool.os,'execve') as execute:
            tool.collect(self.job)
        self.assertEqual(execute.call_args.args[1][-3:], [str(self.job/tool.SOURCES[0]),'collect',self.job.name])
        self.assertEqual(self.calls,[])

    def test_incomplete_cleanup_cannot_finalize(self):
        with self.assertRaisesRegex(ValueError,'requires cleanup'):
            tool.finalize_units(self.job,dict(self.result,cleanup_complete=False))
        self.assertEqual(self.calls,[])

    def test_next_start_requires_prior_disabled_unit_files_collected(self):
        (self.root/'ACTIVE.json').write_text(json.dumps(dict(job_id=self.job.name)))
        decision=self.root/'decision.json'; decision.write_text('{}')
        with patch.object(tool,'prerequisites',return_value={}), patch.object(tool,'authorize'):
            with self.assertRaisesRegex(ValueError,'Collect prior job'):
                tool.start(decision,'normal')
        self.assertEqual(self.calls,[])

    def test_reload_failure_after_removal_retries_idempotently(self):
        self.reload_failure=True
        with self.assertRaisesRegex(ValueError,'RELOAD_FAILURE'): tool.finalize_units(self.job,self.result)
        self.assertTrue(self.result['unit_cleanup_pending'])
        self.assertEqual(list(self.units.iterdir()),[])
        self.reload_failure=False; tool.finalize_units(self.job,self.result)
        self.assertTrue(self.result['unit_cleanup_complete'])

    def test_collection_exports_pending_failure_and_separate_untested_flows(self):
        self.enabled=True
        with patch('builtins.print'): tool.collect(self.job)
        archive=next((self.root/'exports').glob('*/*.tar.gz'))
        with tarfile.open(archive) as tar:
            files={Path(m.name).name:tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
        exported=json.loads(files['RESULT.json']); state=json.loads(files['EXPORT_STATE.json'])
        self.assertEqual(exported['component_error'],'ORIGINAL_FAILURE')
        self.assertIn('remains enabled',exported['unit_cleanup_error'])
        self.assertTrue(state['unit_cleanup_pending'])
        self.assertTrue(state['recovery_pending'])
        summary=json.loads(files['COMPONENT_SUMMARY.json'])
        self.assertTrue(all(r['communication']=='NOT_TESTED' for r in summary['flows'].values()))
        for row in files['SHA256SUMS'].decode().splitlines():
            digest,name=row.split('  ',1); self.assertEqual(hashlib.sha256(files[name]).hexdigest(),digest)

    def test_collection_finishes_terminal_units_and_exports_the_exit_evidence(self):
        with patch('builtins.print'): tool.collect(self.job)
        self.assertEqual(list(self.units.iterdir()),[])
        archive=next((self.root/'exports').glob('*/*.tar.gz'))
        with tarfile.open(archive) as tar:
            files={Path(m.name).name:tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
        self.assertIn('UNIT_EXIT_STATE.json',files)
        state=json.loads(files['EXPORT_STATE.json'])
        self.assertFalse(state['unit_cleanup_pending']); self.assertFalse(state['recovery_pending'])
        result=json.loads(files['RESULT.json'])
        self.assertTrue(result['unit_cleanup_complete'])
        self.assertEqual(result['status'],'COMPONENT_FAILED_REVIEW_REQUIRED')


if __name__=='__main__': unittest.main()
