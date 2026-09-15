"""Evidence boundaries for the read-only integration preparation tool."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / 'scripts/collect-cni-integration-preflight.py'
spec = importlib.util.spec_from_file_location('preparation', PATH)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def fixture():
    observed = {'host_files': {name: {'text': text, 'truncated': False, 'error': None}
        for name, text in [('os_release', 'ID=centos'), ('pid1', 'systemd\n'),
                           ('self_status', 'CapEff:\t0000000000003000\n')]},
        'euid': 0, 'binaries': {name: '/usr/bin/' + name for name in tool.REQUIRED_TOOLS},
        'executor_files_sha256': {name: '0' * 64 for name in tool.SOURCE_FILES},
        'executor_bundle_sha256': '0' * 64, 'commands': {}}
    outputs = {'packages': '\n'.join(tool.PACKAGES), 'virtual_machine': 'vmware',
               'container': 'none', 'firewalld_active': 'active', 'nm_active': 'active',
               'firewalld_state': 'running', 'selinux': 'Enforcing'}
    for name in tool.QUERIES:
        observed['commands'][name] = {'stdout': outputs.get(name, 'version'),
            'stderr': '', 'returncode': 1 if name == 'container' else 0,
            'error': None, 'truncated': False, 'timeout': False}
    return observed


class Boundaries(unittest.TestCase):
    def test_matching_platform_never_becomes_qualification_or_isolation_proof(self):
        result = tool.assess(fixture())
        self.assertEqual(result['status'], 'PREPARATION_REVIEW_REQUIRED')
        self.assertFalse(result['qualification_created'])
        self.assertFalse(result['isolation_verified'])
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['runtime_integration_executed'])

    def test_incomplete_queries_cannot_look_ready(self):
        for name in tool.QUERIES:
            for field, value in [('timeout', True), ('truncated', True),
                                 ('returncode', None), ('error', 'missing')]:
                with self.subTest(query=name, field=field):
                    observed = fixture()
                    observed['commands'][name][field] = value
                    self.assertEqual(tool.assess(observed)['status'], 'PREPARATION_BLOCKED')

    def test_container_and_unprivileged_host_are_blocked(self):
        for change in ('container', 'root', 'capability', 'pid1', 'package', 'source'):
            with self.subTest(change=change):
                observed = fixture()
                if change == 'container':
                    observed['commands']['container'].update(stdout='docker', returncode=0)
                elif change == 'root':
                    observed['euid'] = 1000
                elif change == 'capability':
                    observed['host_files']['self_status']['text'] = 'CapEff: 0'
                elif change == 'pid1':
                    observed['host_files']['pid1']['text'] = 'supervisord'
                elif change == 'package':
                    observed['commands']['packages']['stdout'] = 'different'
                else:
                    observed['executor_files_sha256'] = {}
                self.assertEqual(tool.assess(observed)['status'], 'PREPARATION_BLOCKED')

    def test_default_plan_does_not_query_or_write(self):
        with mock.patch.object(sys, 'argv', [str(PATH)]), mock.patch.object(tool, 'collect') as collect, \
                mock.patch.object(tool, 'write_artifacts') as write, mock.patch('builtins.print'):
            self.assertEqual(tool.main(), 0)
            collect.assert_not_called()
            write.assert_not_called()

    def test_missing_binary_does_not_launch_process(self):
        with mock.patch.object(tool.shutil, 'which', return_value=None), \
                mock.patch.object(tool.subprocess, 'Popen') as launch:
            self.assertEqual(tool.query(['missing'], 1)['error'], 'MISSING_BINARY')
            launch.assert_not_called()

    def test_archive_hashes_and_unexecuted_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            result, archive, sidecar = tool.write_artifacts(Path(directory), fixture())
            self.assertEqual(sidecar.read_text().split()[0], hashlib.sha256(archive.read_bytes()).hexdigest())
            with tarfile.open(archive) as tar:
                files = {m.name.rsplit('/', 1)[-1]: tar.extractfile(m).read()
                         for m in tar.getmembers() if m.isfile()}
            for row in files['SHA256SUMS'].decode().splitlines():
                expected, name = row.split('  ', 1)
                self.assertEqual(hashlib.sha256(files[name]).hexdigest(), expected)
            matrix = json.loads(files['INTEGRATION_MATRIX.json'])
            self.assertNotEqual(matrix['schema'], 'fds.cp1-cni-qualification.v1')
            self.assertEqual(matrix['stage'], 'NOT_EXECUTED')
            self.assertTrue(all(x['verdict'] == 'NOT_TESTED' for x in matrix['cases'].values()))
            self.assertIsNone(matrix['all_owned_objects_absent'])


if __name__ == '__main__':
    unittest.main()
