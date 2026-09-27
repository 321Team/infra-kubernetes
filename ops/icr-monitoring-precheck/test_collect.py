"""Synthetic inputs only. No server access."""
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
    return {'metadata': {'name': 'prometheus-mock', 'uid': 'SYNTHETIC',
                         'annotations': {'secret': 'MOCK-PRIVATE'}},
            'spec': {'nodeName': 'k8s-worker03', 'serviceAccountName': 'prometheus',
                     'containers': [{'name': 'p', 'image': 'mock@sha256:0',
                                     'env': [{'name': 'TOKEN', 'value': 'MOCK-PRIVATE'}]}]},
            'status': {'phase': 'Running', 'podIP': '10.244.1.2',
                       'conditions': [{'type': 'Ready', 'status': 'True'}]}}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.failure = None
        self.sender = [pod()]
        self.api = 'https://10.1.93.109:6443'
        self.nodes = [{'metadata': {'name': n}, 'status': {'addresses':
                       [{'type': 'InternalIP', 'address': ip}]}}
                      for n, ip in c.EXPECTED.items()]
        for target, value in [('socket.gethostname', 'k8s-control01'),
                              ('getpass.getuser', 'svc_ansible'),
                              ('os.path.isfile', True), ('os.access', True),
                              ('os.geteuid', 1001), ('shutil.which', '/usr/bin/sudo'),
                              ('os.stat', types.SimpleNamespace(st_uid=0,st_mode=stat.S_IFREG|0o755))]:
            # Module is loaded by path; patch the actual nested object.
            obj = c
            for part in target.split('.')[:-1]: obj = getattr(obj, part)
            p = patch.object(obj, target.split('.')[-1], return_value=value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(c.subprocess, 'run', side_effect=self.run_fake)
        p.start(); self.addCleanup(p.stop)

    def run_fake(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.failure and self.failure[0] in ' '.join(cmd):
            action = self.failure[1]
            if action == 'timeout': raise subprocess.TimeoutExpired(cmd,15)
            if action == 'malformed': return subprocess.CompletedProcess(cmd,0,'not-json','')
            return subprocess.CompletedProcess(cmd,1,'','Forbidden (MOCK)')
        if 'config' in cmd: out = self.api
        elif cmd[-1] == '--raw=/api/v1/nodes': out=json.dumps({'items':self.nodes})
        elif 'app%3Dprometheus' in cmd[-1]: out=json.dumps({'items':self.sender})
        else: out=json.dumps({'items':[]})
        return subprocess.CompletedProcess(cmd,0,out,'')

    def test_success_is_collection_only_and_get_only(self):
        r,rc=c.collect()
        self.assertEqual(rc,0); self.assertEqual(r['result'],'COLLECTED_PENDING_ANALYSIS')
        self.assertEqual(r['runtime_ac'],'NOT_ASSESSED'); self.assertEqual(len(self.calls),17)
        for cmd in self.calls[1:]:
            self.assertEqual(cmd[-2],'get'); self.assertTrue(cmd[-1].startswith('--raw=/'))
            self.assertNotIn('/secrets',cmd[-1])

    def test_pod_credentials_and_annotations_are_not_emitted(self):
        r,rc=c.collect(); self.assertNotIn('MOCK-PRIVATE',json.dumps(r)); self.assertEqual(rc,0)

    def test_wrong_host_no_queries(self):
        with patch.object(c.socket,'gethostname',return_value='worker03'):
            r,rc=c.collect()
        self.assertEqual(rc,2); self.assertEqual(self.calls,[])

    def test_wrong_api_stops_before_cluster_queries(self):
        self.api='https://127.0.0.1:6443'; r,rc=c.collect()
        self.assertEqual(rc,2); self.assertEqual(len(self.calls),1)

    def test_wrong_node_ip_stops_before_workload_queries(self):
        self.nodes[0]['status']['addresses'][0]['address']='192.0.2.1'
        r,rc=c.collect(); self.assertEqual(rc,2); self.assertEqual(len(self.calls),2)

    def test_multiple_senders_stops(self):
        self.sender.append(pod()); r,rc=c.collect()
        self.assertEqual(rc,2); self.assertEqual(r['reason'],'SENDER_NOT_UNIQUELY_READY')

    def test_missing_sender_stops(self):
        self.sender=[]; r,rc=c.collect(); self.assertEqual(rc,2)

    def test_timeout_preserves_partial_queries(self):
        self.failure=('globalnetworkpolicies','timeout'); r,rc=c.collect()
        self.assertEqual(rc,2); self.assertEqual(r['queries'][-1]['rc'],124)
        self.assertGreater(len(r['queries']),3)

    def test_forbidden_is_not_network_deny_pass(self):
        self.failure=('hostendpoints','forbidden'); r,rc=c.collect()
        self.assertEqual(rc,2); self.assertEqual(r['queries'][-1]['status'],'QUERY_ERROR')

    def test_malformed_json_is_error(self):
        self.failure=('/api/v1/nodes','malformed'); r,rc=c.collect()
        self.assertEqual(rc,2); self.assertEqual(r['queries'][-1]['status'],'COLLECT_ERROR')

    def test_untrusted_binary_stops(self):
        with patch.object(c.os,'stat',return_value=types.SimpleNamespace(st_uid=1001,st_mode=0o777)):
            r,rc=c.collect()
        self.assertEqual(rc,2); self.assertEqual(self.calls,[])


if __name__ == '__main__': unittest.main()
