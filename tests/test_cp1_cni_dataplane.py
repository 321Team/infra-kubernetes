"""Read-only collector: partial failures, real argv0, bounded output and CLI."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cp1_dataplane', ROOT / 'scripts/collect-cp1-cni-dataplane.py')
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
spec = importlib.util.spec_from_file_location('cp1_dataplane_helper', ROOT / 'scripts/node-firewall.py')
n = importlib.util.module_from_spec(spec); spec.loader.exec_module(n)


class Node:
    COMMAND_STATE = {'deadline': None}
    need = staticmethod(n.need)
    now = staticmethod(n.now)
    class BaseBackend:
        def __init__(self, name):
            assert name == 'k8s-control01'
            self.cache = tempfile.TemporaryDirectory()
        def identity(self): pass


def good(argv, timeout):
    return {'rc': 0, 'stdout': '12345678-1234-1234-1234-123456789abc' if argv[0] == 'cat' else '{}',
            'stderr': '', 'stdout_truncated': False, 'stderr_truncated': False}


class DataplaneTests(unittest.TestCase):
    def test_paired_capture_includes_hidden_kernel_sets_and_api_inputs_read_only(self):
        calls = []
        def reader(argv, timeout):
            self.assertLessEqual(timeout, 10)
            calls.append(argv)
            return good(argv, timeout)
        before = copy.deepcopy(Node.COMMAND_STATE)
        result = d.collect(Node, reader)
        self.assertTrue(result['complete'], result['errors'])
        self.assertFalse(result['authorizes_apply']); self.assertFalse(result['atomic_snapshot'])
        self.assertEqual(Node.COMMAND_STATE, before)
        self.assertIn(['ipset', 'save'], calls)
        self.assertEqual(calls.count(['iptables-save', '-c']), 2)
        self.assertEqual(calls.count(d.HOST_READS['boot']), 3)
        for argv in calls:
            self.assertFalse(any(a in ('apply', 'create', 'delete', 'restore', '--reload', 'start', 'stop') for a in argv))
            if argv[0] == 'kubectl':
                self.assertIn('get', argv); self.assertIn('-o', argv)
                self.assertTrue(all('secrets' not in a for a in argv))

    def test_missing_ipset_continues_other_reads_and_is_never_complete(self):
        def reader(argv, timeout):
            value = good(argv, timeout)
            if argv[0] == 'ipset': value.update(rc=127, stderr='not found')
            return value
        result = d.collect(Node, reader)
        self.assertFalse(result['complete'])
        self.assertIn('native_json_after', result['queries'])
        self.assertIn('ipsets: INCOMPLETE_QUERY', result['errors'])
        self.assertIn('ipsets_after: INCOMPLETE_QUERY', result['errors'])

    def test_native_warning_is_preserved_without_claiming_semantic_validity(self):
        def reader(argv, timeout):
            value = good(argv, timeout)
            if argv[0] == 'nft': value['stderr'] = '# Warning: XT target MASQUERADE not found\n'
            return value
        result = d.collect(Node, reader)
        self.assertTrue(result['complete'])
        self.assertIn('native_json', result['warnings'])
        self.assertFalse(result['authorizes_apply'])
        self.assertIn('MASQUERADE', result['queries']['native_json']['stderr'])

    def test_truncation_boolean_rc_and_boot_change_are_explicit_failures(self):
        for change in ({'stdout_truncated': True}, {'stderr_truncated': None}, {'rc': True}):
            with self.subTest(change=change):
                result = d.collect(Node, lambda a,t: dict(good(a,t), **change))
                self.assertFalse(result['complete'])
        count = 0
        def reader(argv, timeout):
            nonlocal count
            value = good(argv, timeout)
            if argv == d.HOST_READS['boot']:
                count += 1
                if count == 3: value['stdout'] = 'changed'
            return value
        self.assertIn('BOOT_CHANGED_OR_MISSING', d.collect(Node, reader)['errors'])

    def test_wrong_host_does_not_start_other_queries(self):
        calls = []
        with patch.object(Node.BaseBackend, 'identity', side_effect=ValueError('wrong host')):
            result = d.collect(Node, lambda a,t: calls.append(a))
        self.assertFalse(result['complete']); self.assertEqual(calls, [])

    def test_exhausted_budget_records_remaining_requests_and_preserves_partial_data(self):
        clock = [100.0]
        def reader(argv, timeout):
            clock[0] += 2
            return good(argv, timeout)
        with patch.object(d.time, 'monotonic', side_effect=lambda: clock[0]):
            result = d.collect(Node, reader, budget=1)
        self.assertFalse(result['complete'])
        self.assertEqual(len(result['queries']), 1)
        self.assertTrue(any('CAPTURE_BUDGET_EXHAUSTED' in e for e in result['errors']))
        self.assertGreater(result['elapsed_seconds'], 1)

    def test_real_process_preserves_argv0_when_executable_is_resolved(self):
        # A real shell process reports argv[0]. No actual firewall command runs.
        with patch.object(n, 'binary', return_value='/bin/sh'):
            result = d.command(n, ['iptables-save', '-c', 'printf %s "$0"'], 2)
        self.assertEqual(result['rc'], 0)
        self.assertEqual(result['stdout'], 'iptables-save')

    def test_real_process_timeout_and_output_truncation_are_retained(self):
        with patch.object(n, 'binary', return_value='/bin/sh'), patch.object(d, 'MAX_BYTES', 4):
            result = d.command(n, ['sh', '-c', 'printf 123456789'], 2)
        self.assertTrue(result['stdout_truncated']); self.assertEqual(result['stdout'], '1234')
        with patch.object(n, 'binary', return_value='/bin/sh'):
            result = d.command(n, ['sh', '-c', 'while :; do :; done'], 0.05)
        self.assertEqual(result['rc'], 124); self.assertEqual(result['error'], 'COMMAND_TIMEOUT')

    def test_isolated_python_cli_imports_and_limits_modes(self):
        result = subprocess.run(['python3', '-I', '-B', str(ROOT / 'scripts/collect-cp1-cni-dataplane.py'), '--help'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--output', result.stdout)
        self.assertNotIn('--apply', result.stdout)

    def test_output_collision_stops_before_collection_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'CAPTURE.json'
            path.write_text('prior evidence')
            with patch.object(sys, 'argv', ['collect', '--output', str(path)]), \
                 patch.object(d, 'collect', side_effect=AssertionError('must not query host')):
                with self.assertRaises(FileExistsError): d.main()
            self.assertEqual(path.read_text(), 'prior evidence')


if __name__ == '__main__': unittest.main()
