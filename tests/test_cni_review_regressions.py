"""K11-01..05 offline regressions. All VM/network/systemd adapters are mocked.

Controller tests launch real local Python processes and inspect their rc and
archives. This suite does not supply Runtime qualification or execution approval.
"""
import base64
from contextlib import nullcontext, redirect_stdout
import copy
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
HEAD = 'a' * 40


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller = load('regression_controller', 'run-cp1-cni-diagnostic.py')
node = load('regression_node', 'cp1-cni-diagnostic-node.py')
kernel = load('regression_kernel', 'run-cni-kernel-integration.py')


def complete_state():
    return dict(status='DIAGNOSTIC_ONLY_REVIEW_REQUIRED', phase='COMPLETE',
                cleanup_complete=True, recovery_disarmed=True, diagnostic_outcome='SUCCEEDED',
                positive_summary=dict(passed=5, failed=0, not_tested=0, total=5), finished_within_window=True,
                unit_cleanup_protocol=1, unit_cleanup_complete=True, unit_cleanup_pending=False)


def controller_child(case, output):
    decision = output / 'decision.json'
    qualification = output / 'qualification.json'
    decision.write_text(json.dumps(dict(schema='fds.cp1-cni-diagnostic-decision.v1', source_commit=HEAD,
        execution_approved=True, review_url='https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-123',
        execution_decision_url='https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-123',
        operator='MOCK_ONLY', backup_reference='MOCK_ONLY', console_reference='MOCK_ONLY',
        cp1_hep_transition_not_started=True, same_cp1_change_conflict_clear=True)))
    qualification.write_text('{}')
    state = complete_state()
    if case in ('unit_pending', 'unit_collect_success', 'unit_collect_rc', 'unit_collect_export_pending', 'unit_collect_export_rc'):
        state.update(unit_cleanup_complete=False, unit_cleanup_pending=True)
    if case == 'legacy_collect':
        for key in ('unit_cleanup_protocol', 'unit_cleanup_complete', 'unit_cleanup_pending'): state.pop(key)
    if case in ('unsafe_terminal', 'poll_deadline', 'immediate_deadline'):
        state.update(status='RECOVERY_REQUIRED' if case == 'unsafe_terminal' else 'RUNNING',
                     phase='RECOVERY_REQUIRED' if case == 'unsafe_terminal' else 'RUNNING',
                     cleanup_complete=False, recovery_disarmed=False)
    if case in ('diagnostic_failed', 'collect_failed'):
        state.update(status='DIAGNOSTIC_FAILED_REVIEW_REQUIRED', diagnostic_outcome='FAILED',
                     diagnostic_error='MOCK_DIAGNOSTIC_FAILURE')
    result = dict(status=state['status'], state=state)
    raw = json.dumps(state).encode()
    result['files'] = {'STATE.json': {'base64': base64.b64encode(raw).decode(),
                                    'sha256': '0'*64 if case == 'artifact_hash' else hashlib.sha256(raw).hexdigest()}}
    calls = {'poll': 0}

    def response(rc=0, data=None, stdout=None):
        return SimpleNamespace(returncode=rc, stdout=stdout if stdout is not None else json.dumps(data),
                               stderr='MOCK_FAILURE' if rc else '')

    def run(argv, **kwargs):
        if argv[0] == 'git':
            return response(stdout=(controller.ROOT / argv[-1].split(':', 1)[1]).read_text())
        assert argv[0] == 'MOCK_SSH', argv
        if 'SSH_CONNECTION' in argv[-1]:
            if case == 'identity_rc': return response(rc=1, stdout='')
            if case == 'identity_json': return response(stdout='INVALID_JSON')
            return response(data=dict(host=controller.NODE, connection=['192.0.2.1','1234','192.0.2.2','22']))
        if argv[-1] == '-': return response(rc=int(case == 'launch'), data={'staged': True})
        if 'poll' in argv:
            calls['poll'] += 1
            if case == 'poll': return response(rc=1)
            if case == 'ack': return response(data=dict(phase='WAIT_CONTROLLER_SSH', management_nonce='MOCK'))
            if case == 'disarm_race' and calls['poll'] == 1:
                return response(data=dict(state, recovery_disarmed=False))
            return response(data=state)
        if 'ack' in argv: return response(rc=1)
        if 'collect' in argv:
            assert argv[-3].startswith(controller.REMOTE_ROOT), argv
            if case in ('unit_collect_rc', 'unit_collect_export_rc'): return response(rc=1)
            if case == 'unit_collect_success':
                state.update(unit_cleanup_complete=True, unit_cleanup_pending=False)
                exit_raw = b'{"MOCK_ONLY":true}'
                result['files']['UNIT_EXIT_STATE.json'] = dict(base64=base64.b64encode(exit_raw).decode(),
                                                              sha256=hashlib.sha256(exit_raw).hexdigest())
            else: state['unit_cleanup_error'] = 'MOCK_BUSY_UNIT'
            return response(data=result)
        if 'status' in argv:
            if case == 'empty_status': return response(stdout='')
            return response(rc=int(case == 'status'), data=result)
        if '/usr/bin/systemctl' in argv:
            assert case == 'recover_request' and '--no-block' in argv
            return response(stdout='')
        raise AssertionError(argv)

    def archive(directory):
        target = directory / 'result.tar.gz'
        with tarfile.open(target, 'w:gz') as tar:
            for path in sorted(directory.glob('*.json')): tar.add(path, arcname=path.name)
        side = directory / 'result.tar.gz.sha256'
        side.write_text(hashlib.sha256(target.read_bytes()).hexdigest() + '  result.tar.gz\n')
        return target, side

    fake = SimpleNamespace(verify_source=lambda: HEAD, run=run, preflight=lambda: {'MOCK_ONLY': True},
        SOURCE_IP='192.0.2.1', node=SimpleNamespace(CP={controller.NODE:'192.0.2.2'}, redact=str),
        ssh_argv=lambda host,args: ['MOCK_SSH',host,*args], archive_output=archive)
    original_inputs, original_load = controller.execution_inputs, controller.load
    args = ['program','--execute','--decision',str(decision),'--qualification',str(qualification)]
    if case in ('collect_failed', 'recover_request', 'unit_collect_export_pending', 'legacy_collect', 'unit_collect_export_rc'):
        args = ['program','--recover' if case == 'recover_request' else '--collect','20260915000000-aaaaaaaaaaaa']
    ticks = iter([0, 0, 1201] if case == 'poll_deadline' else [0, 1201])
    clock = (lambda: next(ticks)) if case in ('poll_deadline','immediate_deadline') else time.monotonic
    with patch.object(sys,'argv',args), patch.object(controller.socket,'gethostname',return_value='ansible01'), \
         patch.object(controller,'load',side_effect=lambda name,path: fake if name == 'existing_controller' else original_load(name,path)), \
         patch.object(controller.tempfile,'mkdtemp',return_value=str(output)), \
         patch.object(controller,'execution_inputs',side_effect=lambda *a: original_inputs(*a) if case == 'qualification' else None), \
         patch.object(controller,'verify_evidence_files'), patch.object(controller.time,'monotonic',side_effect=clock), \
         patch.object(controller.time,'sleep'):
        try: controller.main()
        finally: (output / 'CALL_COUNTS.json').write_text(json.dumps(calls))


class ControllerRegressions(unittest.TestCase):
    def run_case(self, case, successful=False):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            p = subprocess.run([sys.executable, '-B', __file__, '--controller-child', case, directory],
                               capture_output=True, text=True, timeout=15)
            self.assertEqual(p.returncode, 0 if successful else 1, p.stdout + p.stderr)
            result = json.loads((output / 'RESULT.json').read_text())
            archive = output / 'result.tar.gz'
            self.assertTrue(archive.is_file())
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                             (output / 'result.tar.gz.sha256').read_text().split()[0])
            with tarfile.open(archive) as tar:
                self.assertEqual(json.loads(tar.extractfile('RESULT.json').read()), result)
            self.assertEqual('error' in result, not successful)
            self.assertEqual(result['controller_exit_code'], p.returncode)
            if not successful: self.assertIn('STATUS=INCOMPLETE', p.stdout)
            if case == 'disarm_race':
                self.assertEqual(json.loads((output/'CALL_COUNTS.json').read_text())['poll'], 2)
            return result

    def test_success_requires_final_disarm_after_cleanup(self):
        self.assertEqual(self.run_case('disarm_race', True)['operation_status'], 'EXECUTION_COMPLETE')

    def test_collecting_failed_diagnosis_is_successful_collection_only(self):
        result = self.run_case('collect_failed', True)
        self.assertEqual(result['operation_status'], 'EVIDENCE_COLLECTED')
        self.assertEqual(result['result']['state']['diagnostic_outcome'], 'FAILED')

    def test_recovery_request_does_not_claim_completed_recovery(self):
        result = self.run_case('recover_request', True)
        self.assertEqual(result['operation_status'], 'RECOVERY_REQUESTED')
        self.assertFalse(result['result']['recovery_completion_confirmed'])

    def test_execute_finalizes_staged_units_before_success(self):
        result = self.run_case('unit_collect_success', True)
        self.assertEqual(result['operation_status'], 'EXECUTION_COMPLETE')
        self.assertTrue(result['unit_cleanup_complete'])

    def test_collect_can_export_pending_without_claiming_execution_complete(self):
        result = self.run_case('unit_collect_export_pending', True)
        self.assertEqual(result['operation_status'], 'EVIDENCE_COLLECTED')
        self.assertTrue(result['unit_cleanup_pending'])
        self.assertEqual(result['result']['state']['unit_cleanup_error'], 'MOCK_BUSY_UNIT')

    def test_old_staged_node_is_read_only_without_protocol(self):
        result = self.run_case('legacy_collect', True)
        self.assertEqual(result['operation_status'], 'EVIDENCE_COLLECTED')
        self.assertIsNone(result['unit_cleanup_complete'])

    def test_failed_finalization_transport_keeps_initial_evidence(self):
        result = self.run_case('unit_collect_export_rc', True)
        self.assertEqual(result['operation_status'], 'EVIDENCE_COLLECTED')
        self.assertTrue(result['unit_cleanup_pending'])
        self.assertIn('collection_error', result['result'])


for _case in ('qualification','launch','poll','ack','artifact_hash','status','unsafe_terminal',
              'poll_deadline','immediate_deadline','identity_rc','identity_json','diagnostic_failed','empty_status',
              'unit_pending', 'unit_collect_rc'):
    def test(self, case=_case): self.run_case(case)
    setattr(ControllerRegressions, 'test_' + _case + '_fails_after_archiving', test)


def good_probes():
    return {'a': {'direct':{'passed':True}, 'service':{'passed':True}, 'dns':{'passed':True}},
            'b': {'direct':{'passed':True}, 'dns':{'passed':True}}}


class NodeRegressions(unittest.TestCase):
    def setUp(self):
        self.job = node.ROOT / '20260915000000-aaaaaaaaaaaa'
        self.pre, self.post = good_probes(), good_probes()
        self.pre['a']['direct']['passed'] = self.pre['b']['direct']['passed'] = False
        self.state = {'baseline': {}, 'original_after': {}}
        self.tx = SimpleNamespace(prepare=Mock(), withdraw=Mock())
        self.fixture = SimpleNamespace(job=self.job, result=self.state, helper=SimpleNamespace(redact=str),
            boot_changed=False, request={'budget':{'wall_end':time.time()+1200,
                                                 'boot_start':time.clock_gettime(time.CLOCK_BOOTTIME)}},
            preflight=Mock(), create_workload=Mock(return_value=[{'name':'a'},{'name':'b'}]), save=Mock(),
            cleanup_namespace=Mock(), legacy_snapshot=lambda: {}, management=Mock(), cmd=lambda args: '{}',
            emergency_disable=Mock(), core=SimpleNamespace(Transaction=Mock(return_value=self.tx),
                                                           validate_native_policy=Mock(return_value={})))
        self.fixture.probes = lambda peers, **kw: copy.deepcopy(self.pre if kw else self.post)
        for p in (patch.object(node,'safe_dir'), patch.object(node,'lock',return_value=nullcontext()),
                  patch.object(node,'verify_staged_job'), patch.object(node,'validate_owned_units'),
                  patch.object(node,'Linux',return_value=self.fixture), patch.object(node,'save_json'),
                  patch.object(node,'safe_read',return_value=b'{}'),
                  patch.object(node,'system',return_value='not-found'), patch.object(node,'atomic'),
                  patch.object(node,'disarm',side_effect=lambda io: io.result.update(recovery_disarmed=True))):
            p.start(); self.addCleanup(p.stop)

    def failed(self):
        with self.assertRaises(ValueError): node.run_job(self.job)
        self.assertEqual(self.state['status'], 'DIAGNOSTIC_FAILED_REVIEW_REQUIRED')
        self.assertEqual(self.state['diagnostic_outcome'], 'FAILED')
        self.assertTrue(self.state['cleanup_complete']); self.assertTrue(self.state['recovery_disarmed'])
        self.fixture.cleanup_namespace.assert_called()

    def test_pre_direct_failure_is_allowed_when_controls_work(self):
        node.run_job(self.job)
        self.assertEqual(self.state['diagnostic_outcome'], 'SUCCEEDED')
        self.assertEqual(self.state['positive_summary'], dict(passed=5, failed=0, not_tested=0, total=5))
        self.tx.prepare.assert_called_once()

    def test_native_failure_survives_successful_cleanup(self):
        self.fixture.core.validate_native_policy.side_effect = ValueError('MOCK_NATIVE_FAILURE')
        self.failed(); self.assertEqual(self.state['diagnostic_error'], 'MOCK_NATIVE_FAILURE')

    def test_post_failure_survives_successful_cleanup(self):
        self.post['a']['direct']['passed'] = self.post['b']['direct']['passed'] = False
        self.failed(); self.assertEqual(self.state['positive_summary']['failed'], 2)

    def test_missing_post_result_is_not_success(self):
        del self.post['b']['dns']
        self.failed(); self.assertEqual(self.state['positive_summary']['not_tested'], 1)

    def test_window_exceeded_cannot_be_success(self):
        self.fixture.request['budget']['wall_end'] = 0
        self.failed(); self.assertFalse(self.state['finished_within_window'])

    def test_cleanup_failure_remains_armed_then_recovery_preserves_diagnosis_failure(self):
        self.post['a']['direct']['passed'] = False
        self.fixture.cleanup_namespace.side_effect = [ValueError('MOCK_CLEANUP_FAILURE'), None]
        with self.assertRaises(ValueError): node.run_job(self.job)
        self.assertEqual(self.state['phase'], 'RECOVERY_REQUIRED')
        self.assertFalse(self.state.get('recovery_disarmed', False))
        node.run_job(self.job, recovery=True)
        self.assertEqual(self.state['status'], 'DIAGNOSTIC_FAILED_REVIEW_REQUIRED')
        self.assertTrue(self.state['cleanup_complete']); self.assertTrue(self.state['recovery_disarmed'])
        self.tx.prepare.assert_called_once()

    def test_repeat_work_on_completed_failure_is_nonzero_but_recovery_succeeds(self):
        self.post['a']['direct']['passed'] = False
        self.failed()
        with self.assertRaises(ValueError): node.run_job(self.job)
        node.run_job(self.job, recovery=True)
        self.tx.prepare.assert_called_once()


for _peer, _flow in node.CONTROL_FLOWS:
    def test(self, peer=_peer, flow=_flow):
        self.pre[peer][flow]['passed'] = False
        self.failed(); self.tx.prepare.assert_not_called()
    setattr(NodeRegressions, 'test_pre_' + _peer + '_' + _flow + '_blocks_firewall_prepare', test)


class StagedRecoveryRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.job = self.root / ('a'*16); self.job.mkdir()
        self.other = self.root/'checkout_b'; self.other.mkdir()
        self.units = self.root/'units'; self.units.mkdir()
        for name in kernel.SOURCES:
            raw = (SCRIPTS/name).read_bytes()
            (self.job/name).write_bytes(raw)
            (self.other/name).write_bytes(raw+b'\n# Different checkout B\n')
        self.request = {'scenario':'normal', 'observed':{'sources_sha256':{
            name:hashlib.sha256((self.job/name).read_bytes()).hexdigest() for name in kernel.SOURCES}}}
        self.docs = kernel.documents(self.job, 'normal')
        self.state = dict(mutation_attempted=True, recovery_disarmed=False)
        self.write_state()
        (self.job/'REQUEST.json').write_text(json.dumps(self.request))
        (self.job/'UNITS.json').write_text(json.dumps(self.docs))
        for name, text in self.docs.items(): (self.units/name).write_text(text)
        for p in (patch.object(kernel,'ROOT',self.root), patch.object(kernel,'HERE',self.other),
                  patch.object(kernel,'UNITS',self.units), patch.object(kernel.node,'safe_dir'),
                  patch.object(kernel.node,'safe_read',side_effect=lambda path:path.read_bytes())):
            p.start(); self.addCleanup(p.stop)

    def write_state(self): (self.job/'RESULT.json').write_text(json.dumps(self.state))

    def dispatch(self):
        with patch.object(kernel.os,'execve') as execute, patch.object(kernel,'execute') as current, \
             patch.object(kernel,'system') as system, patch.object(kernel.node,'atomic') as mutate:
            kernel.manual_recover(self.job)
            current.assert_not_called(); system.assert_not_called(); mutate.assert_not_called()
            self.assertEqual(execute.call_args.args[1], ['/usr/bin/python3','-I','-B',
                str(self.job/kernel.SOURCES[0]),'_recover',self.job.name])

    def rejected(self):
        with patch.object(kernel.os,'execve') as execute, patch.object(kernel,'system') as system, \
             patch.object(kernel.node,'atomic') as mutate:
            with self.assertRaises(ValueError): kernel.manual_recover(self.job)
            execute.assert_not_called(); system.assert_not_called(); mutate.assert_not_called()

    def test_checkout_b_dispatches_staged_a_even_when_unit_generator_body_changed(self):
        self.assertNotEqual(kernel.hashes(), self.request['observed']['sources_sha256'])
        changed = {name:body+'# B\n' for name,body in self.docs.items()}
        with patch.object(kernel,'documents',return_value=changed): self.dispatch()

    def test_direct_new_checkout_recover_rejected_before_stop_or_systemctl(self):
        with patch.object(kernel,'system') as system, patch.object(kernel.node,'atomic') as mutate:
            with self.assertRaisesRegex(ValueError,'staged source'): kernel.execute(self.job, recovery=True)
            system.assert_not_called(); mutate.assert_not_called()

    def test_changed_staged_source_rejected_before_exec(self):
        (self.job/kernel.SOURCES[0]).write_text('CHANGED')
        self.rejected()

    def test_foreign_unit_rejected_before_exec(self):
        (self.units/next(iter(self.docs))).write_text('FOREIGN')
        self.rejected()

    def test_missing_unit_after_mutation_rejected(self):
        (self.units/next(iter(self.docs))).unlink()
        self.rejected()

    def test_partial_install_and_partial_disarm_use_same_staged_recovery(self):
        for path in self.units.iterdir(): path.unlink()
        for state in (dict(mutation_attempted=False), dict(mutation_attempted=True, disarm_intent=True)):
            self.state = state; self.write_state(); self.dispatch()

    def test_broken_symlink_is_not_tolerated_as_missing_unit(self):
        path = self.units/next(iter(self.docs)); path.unlink(); path.symlink_to(self.units/'MISSING')
        self.state['disarm_intent'] = True; self.write_state(); self.rejected()

    def test_unsafe_job_owner_rejected_before_read_or_exec(self):
        with patch.object(kernel.node,'safe_dir',side_effect=ValueError('Unsafe root directory')):
            self.rejected()

    def test_matching_staged_source_completes_recovery_and_preserves_component_failure(self):
        spec=importlib.util.spec_from_file_location('actual_staged_recovery',self.job/kernel.SOURCES[0])
        staged=importlib.util.module_from_spec(spec); spec.loader.exec_module(staged)
        self.request['boot_id']=staged.node.boot_id()
        (self.job/'REQUEST.json').write_text(json.dumps(self.request))
        self.state.update(recovery_attempts=0, component_error='MOCK_PRIOR_FAILURE')
        self.write_state()
        (self.job/'RESOURCES.json').write_text(json.dumps(dict(namespaces={},pending_namespace=None)))
        with patch.object(staged,'ROOT',self.root), patch.object(staged,'UNITS',self.units), \
             patch.object(staged.node,'safe_dir'), patch.object(staged.node,'safe_read',side_effect=lambda p:p.read_bytes()), \
             patch.object(staged.node,'lock',return_value=nullcontext()), \
             patch.object(staged,'system',return_value='not-found'), patch.object(staged,'verify_recovery_disabled'), \
             patch.object(staged,'call',side_effect=AssertionError('No external command allowed in offline test')):
            staged.execute(self.job,recovery=True)
        result=json.loads((self.job/'RESULT.json').read_text())
        self.assertTrue(result['cleanup_complete']); self.assertTrue(result['recovery_disarmed'])
        self.assertEqual(result['status'],'COMPONENT_FAILED_REVIEW_REQUIRED')
        self.assertEqual(result['component_error'],'MOCK_PRIOR_FAILURE')
        self.assertEqual({p.name for p in self.units.iterdir()},set(self.docs))
        self.assertTrue(result['unit_cleanup_pending'])


class ProbeRegressions(unittest.TestCase):
    def test_tcp_success_is_preserved_after_http_errors(self):
        for error in (ConnectionResetError('reset'), http.client.BadStatusLine('bad status'), UnicodeError('encoding')):
            with self.subTest(error=type(error).__name__):
                connection = Mock(); connection.getresponse.side_effect = error
                result = self.probe(connection)
                self.assertTrue(result['tcp_connected']); self.assertTrue(result['connected'])
                self.assertEqual(result['error_stage'], 'HTTP'); connection.connect.assert_called_once()

    def test_connect_failure_remains_false_and_does_not_request_http(self):
        connection = Mock(); connection.connect.side_effect = TimeoutError('timeout')
        result = self.probe(connection)
        self.assertFalse(result['tcp_connected']); self.assertEqual(result['error_stage'], 'CONNECT')
        connection.request.assert_not_called()

    def test_http_success_preserves_status_and_body(self):
        connection = Mock(); connection.getresponse.return_value = SimpleNamespace(status=200, read=lambda n:b'job/b')
        result = self.probe(connection)
        self.assertTrue(result['tcp_connected']); self.assertEqual((result['status'],result['body']), (200,'job/b'))

    def probe(self, connection):
        output = io.StringIO()
        with patch.object(sys,'argv',['program','_probe','a'*16,'a','10.244.246.81','8080','41000']), \
             patch.object(kernel,'require_inside'), patch.object(kernel,'read_json',return_value={'peers':[{'ip':'10.244.246.81'}]}), \
             patch('http.client.HTTPConnection',return_value=connection), redirect_stdout(output):
            kernel.main()
        connection.close.assert_called_once()
        return json.loads(output.getvalue())


class FlowRegressions(unittest.TestCase):
    def flow(self, case, failure=True):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            lab = kernel.Lab.__new__(kernel.Lab); lab.job=job; lab.ns={'r':'MOCK_ROUTER'}
            lab.result={'component_flows':{}}; lab.guard=Mock()
            source={'name':'c' if case=='outside_connected' else 'a', 'ip':'10.244.246.80','interface':'MOCK_VETH'}
            dest={'name':'b','ip':'10.244.246.81'}
            port = 8081 if case in ('negative_connected','negative_http_reset','valid_negative') else 8080
            actual = dict(tcp_connected=True, connected=True, status=200, body=job.name+'/b')
            if case in ('positive_denied','valid_negative'): actual.update(tcp_connected=False, connected=False,status=None,body=None)
            if case == 'wrong_body': actual['body']='WRONG'
            if case == 'http_500': actual['status']=500
            if case == 'negative_http_reset': actual.update(status=None,body=None,error='reset',error_stage='HTTP')
            def net(role,args,**kwargs):
                if args[0].endswith('conntrack'): return ''
                if '_probe' in args:
                    return json.dumps(dict(tcp_connected=True,connected=True,status=200,body=job.name+'/b') if '127.0.0.1' in args else actual)
                if case=='trace_cleanup_error' and 'delete' in args: raise ValueError('MOCK_TRACE_DELETE_ERROR')
                return ''
            lab.net=net
            def cmd(*args,**kwargs):
                if case=='readback_error': raise ValueError('MOCK_READBACK_ERROR')
                return '' if case=='missing_capture' else 'MOCK_INGRESS_PACKET'
            lab.cmd=cmd
            class Process:
                pid=424242
                def __init__(self,argv,**kwargs):
                    self.capture = 'tcpdump' in Path(argv[4]).name
                    self.err = kwargs['stderr']
                    if self.capture:
                        Path(argv[argv.index('-w')+1]).write_bytes(b'MOCK_PCAP')
                        kwargs['stderr'].write(b'listening on MOCK\n'); kwargs['stderr'].flush()
                    else: kwargs['stdout'].write(b'' if case=='missing_trace' else b'MOCK_NFT_TRACE\n')
                def poll(self): return 1 if self.capture and case=='startup_exit' else None
                def wait(self,**kwargs):
                    if self.capture:
                        self.err.write(b'1 packet captured\n1 packet received by filter\n0 packets dropped by kernel\n'); self.err.flush()
                    return 1 if self.capture and case in ('startup_exit','capture_exit_error') else 0
            with patch.object(kernel.subprocess,'Popen',side_effect=Process), patch.object(kernel.os,'killpg'), \
                 patch.object(kernel.time,'sleep'), patch.object(kernel,'wait_for_observation',return_value='BYTES_AVAILABLE'), patch.object(kernel.node,'safe_read',side_effect=lambda p:p.read_bytes()), \
                 patch.object(kernel.node,'save_json',side_effect=lambda p,d:p.write_text(json.dumps(d))):
                if failure:
                    with self.assertRaisesRegex(ValueError,'Component flow failed'): lab.flow(source,dest,port)
                else: lab.flow(source,dest,port)
            record = next(iter(json.loads((job/'RESULT.json').read_text())['component_flows'].values()))
            self.assertEqual(record['verdict'], 'COMPONENT_FAILED_REVIEW_REQUIRED' if failure else 'OBSERVED_REVIEW_REQUIRED')
            self.assertEqual(bool(record['expectation_errors']), failure)
            self.assertEqual(record['component_attribution'], 'REQUIRES_TRACE_REVIEW')
            self.assertTrue(list(job.glob('*.pcap'))); self.assertTrue(list(job.glob('*.trace')))
            self.assertTrue(list(job.glob('*.tcpdump.log'))); self.assertTrue(list(job.glob('*.nft.log')))
            detail=json.loads(next(job.glob('*.observation.json')).read_text())
            self.assertEqual(detail['tcpdump_statistics']['captured'],1)
            if case=='missing_capture':
                self.assertEqual(record['communication_status'],'MATCH')
                self.assertEqual(record['evidence_errors'],['PACKET_CAPTURE_MISSING'])
            if case=='startup_exit': self.assertEqual(record['communication_status'],'NOT_TESTED')
            if case=='capture_exit_error': self.assertEqual(detail['processes']['tcpdump']['exit_code'],1)

    def test_matching_positive_still_requires_review(self): self.flow('valid_positive', False)
    def test_matching_negative_still_requires_attribution_review(self): self.flow('valid_negative', False)


for _case in ('positive_denied','negative_connected','outside_connected','wrong_body','http_500','negative_http_reset','missing_trace',
              'missing_capture','startup_exit','capture_exit_error','readback_error','trace_cleanup_error'):
    def test(self, case=_case): self.flow(case)
    setattr(FlowRegressions, 'test_' + _case + '_records_failure_before_raising', test)


class ComponentOutcomeRegressions(unittest.TestCase):
    def test_work_failure_returns_nonzero_after_cleanup_but_recovery_can_succeed(self):
        job=kernel.job_path('a'*16)
        lab=SimpleNamespace(job=job,result={'recovery_attempts':0},j={},request={'scenario':'normal'},
                            build=Mock(side_effect=ValueError('MOCK_BUILD_FAILURE')))
        def cleanup(obj):
            obj.result.update(cleanup_complete=True,recovery_disarmed=True,status='COMPONENT_FAILED_REVIEW_REQUIRED')
        with patch.object(kernel,'verify_staged_sources'), patch.object(kernel,'recovery_unit_check'), \
             patch.object(kernel,'read_json',return_value={}), patch.object(kernel.node,'lock',return_value=nullcontext()), \
             patch.object(kernel,'Lab',return_value=lab), patch.object(kernel.node,'save_json'), \
             patch.object(kernel,'cleanup',side_effect=cleanup), patch.object(kernel.node,'atomic'), \
             patch.object(kernel,'system',return_value='not-found'):
            with self.assertRaisesRegex(ValueError,'Component test failed'): kernel.execute(job)
            self.assertTrue(lab.result['cleanup_complete']); self.assertTrue(lab.result['recovery_disarmed'])
            lab.result['recovery_disarmed']=False
            kernel.execute(job,recovery=True)
            self.assertEqual(lab.result['status'],'COMPONENT_FAILED_REVIEW_REQUIRED')
            lab.build.assert_called_once()


class ReviewBindingRegressions(unittest.TestCase):
    def setUp(self):
        self.observed=dict(product_uuid='MOCK_VM',machine_id_sha256='a'*64,sources_sha256=kernel.hashes())
        self.decision=dict(self.observed,schema='fds.cni-kernel-lab-decision.v2',lab_vm='cp1-cni-lab',
            lan_segment='FDS-CNI-LAB',execution_approved=True,operator='MOCK_ONLY',snapshot_reference='MOCK_ONLY',
            review_url='https://github.com/321Team/infra-kubernetes/pull/11#pullrequestreview-123',
            reviewed_bundle_sha256=kernel.bundle_digest(self.observed['sources_sha256']),scope='KERNEL_COMPONENT_ONLY')

    def test_missing_or_different_reviewed_digest_rejected(self):
        for value in ('',None,'f'*64):
            with self.subTest(value=value), self.assertRaises(ValueError):
                kernel.authorize(dict(self.decision,reviewed_bundle_sha256=value),self.observed)

    def test_unrelated_review_url_rejected(self):
        for url in ('https://github.com/321Team/infra-kubernetes/pull/999#issuecomment-123',
                    'https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-123'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                kernel.authorize(dict(self.decision,review_url=url),self.observed)

    def test_old_v1_contract_rejected(self):
        with self.assertRaises(ValueError):
            kernel.authorize(dict(self.decision,schema='fds.cni-kernel-lab-decision.v1'),self.observed)

    def test_digest_is_independent_of_mapping_insertion_order(self):
        self.assertEqual(kernel.bundle_digest(self.observed['sources_sha256']),
                         kernel.bundle_digest(dict(reversed(list(self.observed['sources_sha256'].items())))))
        kernel.authorize(self.decision,self.observed)  # Contract only: not a GitHub approval.

    def test_inspect_template_does_not_preapprove_source_review(self):
        saved={}
        with tempfile.TemporaryDirectory() as directory, patch.object(sys,'argv',['program','inspect']), \
             patch.object(kernel,'prerequisites',return_value=self.observed), patch.object(kernel.node,'safe_dir'), \
             patch.object(kernel.tempfile,'mkdtemp',return_value=directory), patch('builtins.print'), \
             patch.object(kernel.node,'save_json',side_effect=lambda p,d:saved.update({p.name:d})):
            kernel.main()
        template=saved['lab-decision.template.json']
        self.assertFalse(template['execution_approved']); self.assertEqual(template['reviewed_bundle_sha256'],'')
        self.assertEqual(template['review_url'],''); self.assertEqual(template['schema'],'fds.cni-kernel-lab-decision.v2')


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--controller-child':
        controller_child(sys.argv[2],Path(sys.argv[3]))
    else: unittest.main()
