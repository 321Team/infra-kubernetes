"""Production unit lifecycle regressions; local files, mocked systemd only."""
from contextlib import ExitStack, nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod
node = load('lifecycle_node', 'cp1-cni-diagnostic-node.py')
kernel = load('lifecycle_kernel', 'run-cni-kernel-integration.py')
controller = load('lifecycle_controller', 'run-cp1-cni-diagnostic.py')

class ProductionUnits(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.job = self.root / '20260916000000-aaaaaaaaaaaa'; self.job.mkdir()
        self.units = self.root / 'units'; self.units.mkdir()
        for name in node.FILES: (self.job / name).write_bytes((SCRIPTS / name).read_bytes())
        self.request = dict(unit_cleanup_protocol=1, recovery_delay_seconds=900,
            files_sha256={n: hashlib.sha256((self.job / n).read_bytes()).hexdigest() for n in node.FILES})
        self.docs = node.unit_documents(self.job, 900)
        self.state = dict(phase='COMPLETE', cleanup_complete=True, recovery_disarmed=False,
            unit_cleanup_protocol=1, unit_cleanup_pending=True, unit_cleanup_complete=False,
            diagnostic_outcome='FAILED', diagnostic_error='ORIGINAL_FAILURE',
            status='DIAGNOSTIC_FAILED_REVIEW_REQUIRED')
        for name, data in [('REQUEST.json', self.request), ('UNITS.json', self.docs), ('STATE.json', self.state)]:
            (self.job / name).write_text(json.dumps(data))
        for name, body in self.docs.items(): (self.units / name).write_text(body)
        self.calls = []; self.busy = None; self.enabled = False; self.reload_error = False
        self.queued = False; self.dropin = False; self.missing_mainpid = False; self.clock = 0
        self.stack.enter_context(patch.object(node, 'ROOT', self.root))
        self.stack.enter_context(patch.object(node, 'UNITS', self.units))
        self.stack.enter_context(patch.object(node, '__file__', str(self.job / node.FILES[1])))
        self.stack.enter_context(patch.object(node, 'safe_dir'))
        self.stack.enter_context(patch.object(node, 'system', side_effect=self.system))
        self.io = SimpleNamespace(job=self.job, result=self.state, request=self.request,
            save=lambda data: node.save_json(self.job / 'STATE.json', data))

    def system(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == 'is-enabled': return 'enabled' if self.enabled else 'disabled'
        if args[0] == 'show':
            if '--value' in args: return 'inactive'
            name = args[1]; transient = name.startswith('fds-cni-job-')
            fields = dict(ActiveState='active' if self.busy == name else 'inactive',
                MainPID='123' if self.busy == name else '0', Job='7' if self.queued else '',
                LoadState='loaded', Result='exit-code', ExecMainCode='1', ExecMainStatus='1',
                DropInPaths='/foreign.conf' if self.dropin else '',
                FragmentPath=str(Path('/run/systemd/transient') / name) if transient else str(self.units / name),
                Transient='yes' if transient else 'no')
            if self.missing_mainpid: fields.pop('MainPID')
            return '\n'.join(k + '=' + v for k, v in fields.items())
        if args == ['daemon-reload'] and self.reload_error: raise ValueError('RELOAD_FAILURE')
        return ''

    def arm_complete(self):
        self.state.update(recovery_disarmed=True, disarm_intent=True)
        node.save_json(self.job / 'STATE.json', self.state)

    def test_disarm_keeps_running_recovery_definition(self):
        self.busy = node.unit_names(self.job)[1] + '.service'
        node.disarm(self.io)
        self.assertTrue(self.state['recovery_disarmed'])
        self.assertEqual(set(p.name for p in self.units.iterdir()), set(self.docs))
        self.assertNotIn(['daemon-reload'], self.calls)
        self.assertTrue(self.state['unit_cleanup_pending'])

    def test_enabled_timer_cannot_be_reported_disarmed(self):
        self.enabled = True
        with self.assertRaises(ValueError): node.disarm(self.io)
        self.assertFalse(self.state['recovery_disarmed'])
        self.assertEqual(len(list(self.units.iterdir())), 2)

    def test_changed_unit_blocks_disarm_before_system_mutation(self):
        (self.units / next(iter(self.docs))).write_text('FOREIGN')
        with self.assertRaises(ValueError): node.disarm(self.io)
        self.assertFalse(any(c[0] == 'disable' for c in self.calls))

    def test_terminal_failed_job_finalizes_without_erasing_failure(self):
        self.arm_complete(); node.finalize_units(self.job, self.state)
        self.assertEqual(list(self.units.iterdir()), [])
        self.assertTrue(self.state['unit_cleanup_complete'])
        self.assertFalse(self.state['unit_cleanup_pending'])
        self.assertEqual(self.state['diagnostic_error'], 'ORIGINAL_FAILURE')
        self.assertEqual(len(json.loads((self.job / 'UNIT_EXIT_STATE.json').read_text())), 3)

    def test_running_recovery_blocks_all_removal(self):
        self.arm_complete(); self.busy = node.unit_names(self.job)[1] + '.service'
        with patch.object(node.time, 'monotonic', side_effect=lambda: self.clock), \
             patch.object(node.time, 'sleep', side_effect=lambda delay: setattr(self, 'clock', self.clock + 6)):
            with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(len(list(self.units.iterdir())), 2)
        self.assertFalse(any(c[0] == 'stop' for c in self.calls))

    def test_running_work_blocks_all_removal(self):
        self.arm_complete(); self.busy = node.unit_names(self.job)[0] + '.service'
        with patch.object(node.time, 'monotonic', side_effect=lambda: self.clock), \
             patch.object(node.time, 'sleep', side_effect=lambda delay: setattr(self, 'clock', self.clock + 6)):
            with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(len(list(self.units.iterdir())), 2)

    def test_queued_service_blocks_all_removal(self):
        self.arm_complete(); self.queued = True
        with patch.object(node.time, 'monotonic', side_effect=lambda: self.clock), \
             patch.object(node.time, 'sleep', side_effect=lambda delay: setattr(self, 'clock', self.clock + 6)):
            with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(len(list(self.units.iterdir())), 2)

    def test_missing_process_state_is_not_terminal(self):
        self.arm_complete(); self.missing_mainpid = True
        with patch.object(node.time, 'monotonic', side_effect=lambda: self.clock), \
             patch.object(node.time, 'sleep', side_effect=lambda delay: setattr(self, 'clock', self.clock + 6)):
            with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(len(list(self.units.iterdir())), 2)

    def test_source_change_blocks_finalization_before_systemctl(self):
        self.arm_complete(); (self.job / node.FILES[0]).write_text('CHANGED')
        with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(self.calls, [])

    def test_other_checkout_cannot_finalize_old_job(self):
        self.arm_complete()
        with patch.object(node, '__file__', str(SCRIPTS / node.FILES[1])):
            with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(self.calls, [])

    def test_foreign_manifest_cannot_remove_other_unit(self):
        self.arm_complete(); (self.job / 'UNITS.json').write_text(json.dumps({'other.service':'foreign'}))
        with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(self.calls, [])

    def test_foreign_dropin_blocks_removal(self):
        self.arm_complete(); self.dropin = True
        with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(len(list(self.units.iterdir())), 2)

    def test_unit_symlink_blocks_removal(self):
        self.arm_complete(); p = self.units / next(iter(self.docs)); p.unlink(); p.symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(len(list(self.units.iterdir())), 2)

    def test_reload_failure_keeps_pending_and_retry_completes(self):
        self.arm_complete(); self.reload_error = True
        with self.assertRaisesRegex(ValueError, 'RELOAD_FAILURE'): node.finalize_units(self.job, self.state)
        self.assertFalse(self.state['unit_cleanup_complete'])
        self.assertTrue(self.state['unit_cleanup_intent'])
        self.reload_error = False; node.finalize_units(self.job, self.state)
        self.assertTrue(self.state['unit_cleanup_complete'])

    def test_unexpected_missing_file_is_not_partial_cleanup(self):
        self.arm_complete(); (self.units / next(iter(self.docs))).unlink()
        with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(len(list(self.units.iterdir())), 1)

    def test_reenabled_recovery_clears_disarm_claim(self):
        self.arm_complete(); self.enabled = True
        with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertFalse(self.state['recovery_disarmed'])

    def test_reenabled_after_reload_clears_disarm_claim(self):
        self.arm_complete()
        original = self.system
        def changed(args, **kwargs):
            if args == ['daemon-reload']: self.enabled = True
            return original(args, **kwargs)
        with patch.object(node, 'system', side_effect=changed):
            with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertFalse(self.state['recovery_disarmed'])
        self.assertTrue(self.state['unit_cleanup_pending'])
        self.enabled = False
        node.collect_job(self.job)
        observed = node.status_payload(self.job)['state']
        self.assertTrue(observed['recovery_disarmed'])
        self.assertTrue(observed['unit_cleanup_complete'])

    def test_slow_systemctl_cannot_exceed_collection_budget(self):
        self.arm_complete(); deadlines = []
        original = self.system
        def slow(args, **kwargs):
            deadlines.append(kwargs['timeout']); self.clock += 7
            return original(args, **kwargs)
        with patch.object(node.time, 'monotonic', side_effect=lambda: self.clock), \
             patch.object(node, 'system', side_effect=slow):
            with self.assertRaisesRegex(ValueError, 'budget exhausted'): node.finalize_units(self.job, self.state)
        self.assertEqual(deadlines, [15, 13, 6])
        self.assertEqual(len(list(self.units.iterdir())), 2)

    def test_incomplete_resource_cleanup_blocks_finalization(self):
        self.arm_complete(); self.state['cleanup_complete'] = False
        with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        self.assertEqual(self.calls, [])

    def test_collection_reports_busy_pending_and_preserves_failure(self):
        self.arm_complete(); self.busy = node.unit_names(self.job)[1] + '.service'
        with patch.object(node.time, 'monotonic', side_effect=lambda: self.clock), \
             patch.object(node.time, 'sleep', side_effect=lambda delay: setattr(self, 'clock', self.clock + 6)): node.collect_job(self.job)
        output = node.status_payload(self.job)
        self.assertTrue(output['state']['unit_cleanup_pending'])
        self.assertIn('unit_cleanup_error', output['state'])
        self.assertEqual(output['state']['diagnostic_error'], 'ORIGINAL_FAILURE')

    def test_finished_collect_exports_exit_states_and_is_repeatable(self):
        self.arm_complete(); node.collect_job(self.job); node.collect_job(self.job)
        output = node.status_payload(self.job)
        self.assertTrue(output['state']['unit_cleanup_complete'])
        self.assertIn('UNIT_EXIT_STATE.json', output['files'])

    def test_existing_job_pending_blocks_next_install(self):
        (self.root / 'active.json').write_text(json.dumps({'run_id':self.job.name}))
        node.save_json(self.job / 'STATE.json', dict(self.state, recovery_disarmed=True))
        data = dict(request=dict(run_id='20260916000001-bbbbbbbbbbbb',issued_at=node.time.time()),
                    sources={n:'' for n in node.FILES})
        with patch.object(node.socket, 'gethostname', return_value='k8s-control01'):
            with self.assertRaisesRegex(ValueError, 'unit'): node.install(data)
        self.assertFalse((self.root / data['request']['run_id']).exists())

    def test_wrong_checkout_recover_rejected_before_stop_or_signal(self):
        with patch.object(node, '__file__', str(SCRIPTS / node.FILES[1])):
            with self.assertRaises(ValueError): node.run_job(self.job, recovery=True)
        self.assertFalse((self.job / 'STOP').exists())
        self.assertEqual(self.calls, [])

    def test_partial_install_missing_units_allowed_only_before_mutation(self):
        for p in self.units.iterdir(): p.unlink()
        self.state['mutation_attempted'] = False
        node.disarm(self.io)
        self.assertTrue(self.state['recovery_disarmed'])

    def test_partial_removal_retry_retains_original_exit_record(self):
        self.arm_complete(); self.reload_error = True
        with self.assertRaises(ValueError): node.finalize_units(self.job, self.state)
        original = (self.job / 'UNIT_EXIT_STATE.json').read_bytes()
        self.reload_error = False; node.finalize_units(self.job, self.state)
        self.assertEqual((self.job / 'UNIT_EXIT_STATE.json').read_bytes(), original)

class FaultObservations(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.job = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(kernel.node, 'safe_dir'))
        self.lab = SimpleNamespace(job=self.job, request={'scenario':'kill'}, result={}, boot_changed=False,
                                   guard=lambda: None)

    def test_slow_prepare_recovery_is_not_target_reached(self):
        (self.job / 'ARMED.json').write_text(json.dumps({'armed_monotonic':0}))
        with patch.object(kernel.time, 'monotonic', return_value=400):
            kernel.record_scenario_recovery(self.lab, {'ExecMainCode':'2','ExecMainStatus':'9','ActiveState':'failed'})
        event = self.lab.result['scenario_observation']
        self.assertEqual(event['verdict'], 'NOT_REACHED')
        self.assertIn('component_error', self.lab.result)

    def test_intent_is_not_proof_of_sigkill(self):
        kernel.mark_scenario_target(self.lab)
        kernel.record_scenario_recovery(self.lab, {'ActiveState':'active','ExecMainCode':'0','ExecMainStatus':'0'})
        self.assertFalse(self.lab.result['scenario_observation']['sigkill_observed'])
        self.assertEqual(self.lab.result['scenario_observation']['verdict'], 'REVIEW_REQUIRED')

    def test_pre_stop_sigkill_proof_survives_recovery_retry(self):
        kernel.mark_scenario_target(self.lab)
        kernel.record_scenario_recovery(self.lab, {'ActiveState':'failed','ExecMainCode':'2','ExecMainStatus':'9'})
        kernel.record_scenario_recovery(self.lab, {'ActiveState':'inactive','ExecMainCode':'0','ExecMainStatus':'0'})
        self.assertTrue(self.lab.result['scenario_observation']['sigkill_observed'])

    def test_deadline_uses_monotonic_not_wall_clock(self):
        self.lab.request['scenario'] = 'deadline'; kernel.mark_scenario_target(self.lab)
        (self.job / 'ARMED.json').write_text(json.dumps({'armed_monotonic':100}))
        with patch.object(kernel.time, 'monotonic', return_value=203.025), patch.object(kernel.time, 'time', return_value=10**10):
            kernel.record_scenario_recovery(self.lab)
        self.assertFalse(self.lab.result['scenario_observation']['accelerated_deadline_elapsed'])
        with patch.object(kernel.time, 'monotonic', return_value=401): kernel.record_scenario_recovery(self.lab)
        self.assertTrue(self.lab.result['scenario_observation']['accelerated_deadline_elapsed'])

    def test_changed_boot_does_not_compare_old_monotonic_epoch(self):
        self.lab.request['scenario'] = 'deadline'; kernel.mark_scenario_target(self.lab)
        self.lab.boot_changed = True
        (self.job / 'ARMED.json').write_text(json.dumps({'armed_monotonic':0}))
        with patch.object(kernel.time, 'monotonic', return_value=1000): kernel.record_scenario_recovery(self.lab)
        self.assertFalse(self.lab.result['scenario_observation']['accelerated_deadline_elapsed'])

    def test_missing_armed_clock_cannot_claim_deadline_elapsed(self):
        self.lab.request['scenario'] = 'deadline'; kernel.mark_scenario_target(self.lab)
        kernel.record_scenario_recovery(self.lab)
        self.assertFalse(self.lab.result['scenario_observation']['accelerated_deadline_elapsed'])

    def test_target_guard_failure_does_not_leave_reached_marker(self):
        def stop(): raise ValueError('Recovery already requested')
        self.lab.guard = stop
        with self.assertRaises(ValueError): kernel.mark_scenario_target(self.lab)
        self.assertNotIn('scenario_observation', self.lab.result)

    def test_normal_has_no_fault_claim(self):
        self.lab.request['scenario'] = 'normal'
        kernel.mark_scenario_target(self.lab); kernel.record_scenario_recovery(self.lab)
        self.assertNotIn('scenario_observation', self.lab.result)

    def test_cleanup_retry_injection_requires_reached_target_and_observed_sigkill(self):
        for reached, killed in ((False, True), (True, False), (True, True)):
            with self.subTest(reached=reached, killed=killed):
                self.lab.request['scenario'] = 'cleanup-retry'
                self.lab.result = {'recovery_attempts': 0}
                if reached: kernel.mark_scenario_target(self.lab)
                kernel.node.save_json(self.job / 'RESULT.json', self.lab.result)
                raw = ('ActiveState=failed\nExecMainCode=2\nExecMainStatus=9' if killed else
                       'ActiveState=active\nExecMainCode=0\nExecMainStatus=0')
                def system(args): return 'not-found' if '--value' in args else raw
                with patch.object(kernel, 'verify_staged_sources'), patch.object(kernel, 'recovery_unit_check'), \
                     patch.object(kernel.node, 'lock', return_value=nullcontext()), \
                     patch.object(kernel, 'Lab', return_value=self.lab), \
                     patch.object(kernel, 'system', side_effect=system), patch.object(kernel, 'cleanup') as cleanup:
                    if reached and killed:
                        with self.assertRaisesRegex(ValueError, 'Intentional first recovery'): kernel.execute(self.job, recovery=True)
                        cleanup.assert_not_called()
                        self.assertTrue(self.lab.result['injected_cleanup_retry'])
                    else:
                        kernel.execute(self.job, recovery=True)
                        cleanup.assert_called_once()
                        self.assertNotIn('injected_cleanup_retry', self.lab.result)

if __name__ == '__main__': unittest.main()
