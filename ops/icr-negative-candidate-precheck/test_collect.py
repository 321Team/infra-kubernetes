"""Synthetic success/failure/privacy cases; no server connections."""
import copy
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).with_name('collect.py'))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def pod():
    return {'metadata': {'name': 'app-mock', 'namespace': 'fds-app', 'uid': 'SYNTHETIC',
                         'labels': {'app': 'example', 'custom-token': 'MOCK-SECRET'},
                         'annotations': {'last-applied': 'MOCK-SECRET'}},
            'spec': {'nodeName': c.NODE, 'serviceAccountName': 'mock-sa',
                     'containers': [{'name': 'app', 'image': 'mock@sha256:0',
                                     'env': [{'name': 'TOKEN', 'value': 'MOCK-SECRET'}],
                                     'args': ['MOCK-SECRET']}],
                     'volumes': [{'secret': {'secretName': 'MOCK-SECRET'}}]},
            'status': {'phase': 'Running', 'podIP': '10.244.1.2', 'hostIP': c.NODE_IP,
                       'conditions': [{'type': 'Ready', 'status': 'True'}],
                       'containerStatuses': [{'name': 'app', 'imageID': 'mock@sha256:0', 'ready': True}]}}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.failure, self.api = [], None, c.API
        self.node = {'kind': 'Node', 'metadata': {'name': c.NODE, 'uid': 'NODE-SYNTHETIC'},
                     'status': {'addresses': [{'type': 'InternalIP', 'address': c.NODE_IP}],
                                'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        self.pods = {'kind': 'PodList', 'metadata': {'resourceVersion': '123'}, 'items': [pod()]}
        for obj, attr, value in [(c.socket, 'gethostname', 'k8s-control01'),
                                 (c.pwd, 'getpwuid', types.SimpleNamespace(pw_name='svc_ansible')),
                                 (c.os.path, 'isfile', True), (c.os, 'access', True),
                                 (c.os, 'stat', types.SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o755))]:
            p = patch.object(obj, attr, return_value=value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(c.subprocess, 'run', side_effect=self.fake)
        p.start(); self.addCleanup(p.stop)

    def fake(self, cmd, **kw):
        self.calls.append(cmd)
        self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertEqual(kw['timeout'], 15)
        if self.failure and 'fieldSelector' in cmd[-1]:
            if self.failure == 'timeout':
                raise subprocess.TimeoutExpired(cmd, 15, output='MOCK-SECRET')
            if self.failure == 'forbidden':
                return subprocess.CompletedProcess(cmd, 1, 'MOCK-SECRET', 'Forbidden MOCK-SECRET')
            if self.failure == 'malformed':
                return subprocess.CompletedProcess(cmd, 0, 'MOCK-SECRET', '')
            if self.failure == 'oversized':
                return subprocess.CompletedProcess(cmd, 0, 'x' * 4_000_001, '')
        out = self.api if 'config' in cmd else json.dumps(self.pods if 'fieldSelector' in cmd[-1] else self.node)
        return subprocess.CompletedProcess(cmd, 0, out, '')

    def test_success_only_inventory_and_three_fixed_read_commands(self):
        r, rc = c.collect()
        self.assertEqual(rc, 0); self.assertEqual(len(self.calls), 3)
        self.assertEqual(r['owner_review_count'], 1)
        self.assertEqual(r['runtime_ac'], 'NOT_ASSESSED'); self.assertEqual(r['probe'], 'NOT_RUN')
        self.assertEqual(r['approval'], 'NOT_ESTABLISHED')
        self.assertEqual(self.calls[1][-2:], ['get', '--raw=/api/v1/nodes/k8s-worker03'])
        self.assertEqual(self.calls[2][-2:], ['get', '--raw=' + c.POD_PATH])
        self.assertTrue(all(cmd[:2] == ['/usr/bin/sudo', '-n'] for cmd in self.calls))

    def test_credentials_full_objects_and_custom_labels_are_not_exported(self):
        r, rc = c.collect(); self.assertEqual(rc, 0)
        text = json.dumps(r)
        self.assertNotIn('MOCK-SECRET', text); self.assertNotIn('custom-token', text)
        self.assertEqual(r['queries'][-1]['data']['items'][0]['role_labels'], {'app': 'example'})

    def test_empty_list_is_no_selection_not_security_pass(self):
        self.pods['items'] = []; r, rc = c.collect()
        self.assertEqual(rc, 0); self.assertEqual(r['owner_review_count'], 0)
        self.assertEqual(r['approval'], 'NOT_ESTABLISHED')

    def test_host_network_system_and_prometheus_are_excluded(self):
        for ns, sa, host in [('monitoring-node', 'node-exporter-sa', True),
                              ('kube-system', 'calico-node', True),
                              ('monitoring-api', 'prometheus', False)]:
            p = pod(); p['metadata']['namespace'] = ns
            p['spec'].update(serviceAccountName=sa, hostNetwork=host)
            self.pods['items'] = [p]; r, rc = c.collect()
            self.assertEqual(rc, 0); self.assertEqual(r['owner_review_count'], 0)

    def test_missing_image_id_does_not_establish_probe_capability(self):
        del self.pods['items'][0]['status']['containerStatuses'][0]['imageID']
        r, rc = c.collect(); self.assertEqual(rc, 0); self.assertEqual(r['owner_review_count'], 0)
        self.assertEqual(r['queries'][-1]['data']['items'][0]['probe_capability'], 'NOT_CHECKED')

    def test_wrong_host_stops_without_subprocess(self):
        with patch.object(c.socket, 'gethostname', return_value='ansible01'):
            r, rc = c.collect()
        self.assertEqual(rc, 2); self.assertEqual(self.calls, [])

    def test_root_is_not_accepted_as_svc_ansible(self):
        with patch.object(c.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_name='root')):
            r, rc = c.collect()
        self.assertEqual(rc, 2); self.assertEqual(self.calls, [])

    def test_cp_direct_api_does_not_fall_back(self):
        self.api = 'https://10.1.93.102:6443'; r, rc = c.collect()
        self.assertEqual(rc, 2); self.assertEqual(len(self.calls), 1)

    def test_wrong_node_ip_stops_before_pod_query(self):
        self.node['status']['addresses'][0]['address'] = '192.0.2.1'
        r, rc = c.collect(); self.assertEqual(rc, 2); self.assertEqual(len(self.calls), 2)

    def test_node_not_ready_stops(self):
        self.node['status']['conditions'][0]['status'] = 'False'
        r, rc = c.collect(); self.assertEqual(rc, 2); self.assertEqual(len(self.calls), 2)

    def test_continuation_is_failure_not_partial_success(self):
        self.pods['metadata']['continue'] = 'MOCK-SECRET'
        r, rc = c.collect(); self.assertEqual(rc, 2); self.assertEqual(len(self.calls), 3)
        self.assertNotIn('data', r['queries'][-1]); self.assertNotIn('MOCK-SECRET', json.dumps(r))

    def test_other_node_and_duplicate_uid_are_rejected(self):
        for fault in ('node', 'duplicate'):
            self.pods['items'] = [pod()]
            if fault == 'node': self.pods['items'][0]['spec']['nodeName'] = 'k8s-worker01'
            else: self.pods['items'].append(copy.deepcopy(self.pods['items'][0]))
            r, rc = c.collect(); self.assertEqual(rc, 2)

    def test_forbidden_is_not_negative_test_pass_and_no_error_secret_export(self):
        self.failure = 'forbidden'; r, rc = c.collect()
        self.assertEqual(rc, 2); self.assertEqual(r['queries'][-1]['status'], 'FORBIDDEN')
        self.assertNotIn('MOCK-SECRET', json.dumps(r)); self.assertEqual(r['probe'], 'NOT_RUN')

    def test_timeout_and_bad_response_keep_query_failure(self):
        for fault, status in [('timeout', 'TIMEOUT'), ('malformed', 'INVALID_OR_INCOMPLETE_RESPONSE'),
                               ('oversized', 'OUTPUT_LIMIT')]:
            self.failure = fault; r, rc = c.collect()
            self.assertEqual(rc, 2); self.assertEqual(r['queries'][-1]['status'], status)

    def test_missing_tools_and_untrusted_binary_stop_before_query(self):
        with patch.object(c.os.path, 'isfile', return_value=False):
            self.assertEqual(c.collect()[1], 2)
        with patch.object(c.os, 'stat', return_value=types.SimpleNamespace(st_uid=1000, st_mode=0o777)):
            self.assertEqual(c.collect()[1], 2)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
