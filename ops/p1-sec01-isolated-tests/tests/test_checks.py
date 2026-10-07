import base64
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('checks', ROOT / 'checks.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class Checks(unittest.TestCase):
    def test_exact_six_node_inventory_and_ready(self):
        data = {'items': [{'metadata': {'name': f'k8s-{role}{i:02}'}, 'status': {
            'conditions': [{'type': 'Ready', 'status': 'True'}]}}
            for role in ('control', 'worker') for i in (1, 2, 3)]}
        c.nodes_ready(data)
        for change in ('name', 'ready', 'missing'):
            bad = copy.deepcopy(data)
            if change == 'name': bad['items'][0]['metadata']['name'] = 'unknown-node'
            elif change == 'ready': bad['items'][0]['status']['conditions'][0]['status'] = 'False'
            else: bad['items'].pop()
            with self.assertRaises(ValueError): c.nodes_ready(bad)

    def test_count_fields_zero_and_positive(self):
        for n in (0, 620):
            self.assertEqual(c.count(f'"ClusterID" : 1\n"Count" : {n}\n'), n)

    def test_count_rejects_error_ambiguous_negative(self):
        for text in ('count-only is only for --write-out=fields', '', '"Count" : -1',
                     '"Count" : 2\n"Count" : 2', 'Count: 42'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                c.count(text)

    def test_secrets_counts_without_values(self):
        values = [b'k8s:enc:secretbox:v1:sentinel', b'plaintext-sentinel']
        d = {'header': {}, 'count': 2, 'kvs': [{'value': base64.b64encode(v).decode()} for v in values]}
        self.assertEqual(c.secret_counts(d), (2, 1, 1))
        result = subprocess.run([sys.executable, str(ROOT/'checks.py'), 'secrets'],
                                input=json.dumps(d), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('sentinel', result.stdout + result.stderr)

    def test_invalid_secret_json_and_base64_fail_without_leak(self):
        for value in ('SECRET-SENTINEL', json.dumps({'header': {}, 'kvs': [{'value': 'SECRET-SENTINEL'}]}),
                      json.dumps({'header': {}, 'count': 2, 'kvs': []}), '{}'):
            result = subprocess.run([sys.executable, str(ROOT/'checks.py'), 'secrets'],
                                    input=value, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('SECRET-SENTINEL', result.stdout + result.stderr)

    def test_token_automount_and_projection_are_both_gated(self):
        c.token_absent({'spec': {'automountServiceAccountToken': False}})
        for spec in ({}, {'automountServiceAccountToken': True},
                     {'automountServiceAccountToken': False, 'volumes': [{'projected': {'sources': [{'serviceAccountToken': {}}]}}]}):
            with self.assertRaises(ValueError): c.token_absent({'spec': spec})

    def test_pid_match_uses_executable_and_exact_argument(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for pid, args in enumerate([
                ['/usr/local/bin/etcd', '--data-dir=/test/run/data'],
                ['etcd', '--data-dir', '/test/run/data'],
                ['etcd', '--data-dir=/test/run/data-other'],
                ['bash', '-c', 'etcd --data-dir=/test/run/data'],
                ['etcd', '--data-dir=/var/lib/etcd']], 1):
                (root/str(pid)).mkdir()
                (root/str(pid)/'cmdline').write_bytes(b'\0'.join(a.encode() for a in args)+b'\0')
            self.assertEqual(c.process_ids('/test/run/data', root), [1, 2])

    def test_eps_baseline_detects_address_uid_port_change(self):
        data = {'items': [{'metadata': {'name': 'app-abc', 'uid': 'slice', 'labels': {}},
                           'ports': [{'port': 8000}], 'endpoints': [{'addresses': ['10.0.0.1'],
                           'conditions': {'ready': True}, 'targetRef': {'uid': 'pod'}}]}]}
        baseline = c.baseline('eps', data, 'test')
        for change in ('address', 'uid', 'port'):
            modified = copy.deepcopy(data)
            if change == 'port': modified['items'][0]['ports'][0]['port'] = 8001
            elif change == 'uid': modified['items'][0]['endpoints'][0]['targetRef']['uid'] = 'replacement'
            else: modified['items'][0]['endpoints'][0]['addresses'] = ['10.0.0.2']
            self.assertNotEqual(c.baseline('eps', modified, 'test'), baseline)

    def test_seal_roundtrip_and_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'evidence'; root.mkdir()
            (root/'a.txt').write_text('fixture only', encoding='utf-8')
            c.seal(root); c.verify(root)
            self.assertTrue(Path(str(root)+'.tar.gz.sha256').exists())
            (root/'a.txt').write_text('tampered', encoding='utf-8')
            with self.assertRaises(ValueError): c.verify(root)

    def test_verify_rejects_path_escape_and_unlisted_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'a').write_text('a')
            c.seal(root)
            (root/'extra').write_text('extra')
            with self.assertRaises(ValueError): c.verify(root)
            (root/'SHA256SUMS').write_text('0'*64+'  ../escape\n')
            with self.assertRaises(ValueError): c.verify(root)

    def test_revision_rejects_error_json_and_multiple_endpoints(self):
        for data in ({'error': 'fixture'}, [], [{'Status': {'header': {'revision': '3287996'}}}],
                     [{'Status': {'header': {'revision': 1}}}]*2):
            r = subprocess.run([sys.executable, str(ROOT/'checks.py'), 'revision'],
                               input=json.dumps(data), capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)


if __name__ == '__main__': unittest.main()
