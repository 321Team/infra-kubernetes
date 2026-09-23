"""Local synthetic tests; no cluster calls and no runtime acceptance claims."""
import base64
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('tls_inputs', ROOT / 'filter_plugins/tls_inputs.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def b64(value):
    return base64.b64encode(value.encode()).decode()


def fixture():
    return {'host': 'k8s-worker03', 'time': '2026-09-24T04:30:00+09:00', 'account': 'svc_ansible',
            'pid': 123, 'metadata': '0|0|600|system_u:object_r:var_lib_t:s0|123|12|time|time',
            'argv_b64': b64('/usr/bin/kubelet\0--config=/var/lib/kubelet/config.yaml\0--unknown-token=DO_NOT_LOG\0'),
            'config_b64': b64('apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\n'
                              'rotateCertificates: true\nauthentication:\n  anonymous:\n    enabled: false\n'
                              '  webhook:\n    enabled: true\nauthorization:\n  mode: Webhook\n'
                              'staticPodURLHeader:\n  Authorization: [DO_NOT_LOG]\n')}


def result(obj, rc=0):
    return {'rc': rc, 'stdout': json.dumps(obj)}


class InputsTests(unittest.TestCase):
    def test_worker_selected_fields_only(self):
        report = mod.report(result(fixture()), 'k8s-worker03')
        self.assertTrue(report['collection_ok'])
        self.assertEqual(report['pending'], [])
        self.assertNotIn('DO_NOT_LOG', json.dumps(report))

    def test_duplicate_yaml_fails_without_echo(self):
        obj = fixture()
        obj['config_b64'] = b64('kind: KubeletConfiguration\nkind: DO_NOT_LOG\n')
        report = mod.report(result(obj), 'k8s-worker03')
        self.assertFalse(report['collection_ok'])
        self.assertNotIn('DO_NOT_LOG', json.dumps(report))

    def test_multidoc_and_alias_rejected(self):
        for value in ['a: 1\n---\na: 2\n', 'a: &x {b: 1}\nc: *x\n']:
            with self.assertRaises(Exception):
                mod.document(value)

    def test_explicit_cli_collision_holds(self):
        obj = fixture()
        obj['argv_b64'] = b64('/usr/bin/kubelet\0--config\0/var/lib/kubelet/config.yaml\0--rotate-server-certificates=false\0')
        report = mod.report(result(obj), 'k8s-worker03')
        self.assertTrue(report['collection_ok'])
        self.assertIn('EXPLICIT_CLI_OVERRIDE_REQUIRES_REVIEW', report['pending'])

    def test_unknown_host_and_remote_failure(self):
        self.assertFalse(mod.report(result(fixture()), 'k8s-worker01')['collection_ok'])
        r = mod.report(result({'stage': 'worker_config', 'collection_error': 'READ_FAILED'}, 1), 'k8s-worker03')
        self.assertEqual(r['stage'], 'worker_config')
        self.assertEqual(r['detail'], 'READ_FAILED')

    def test_ca_mismatch_and_missing_host_hold(self):
        hosts = {h: {'tls_report': {'collection_ok': True, 'signer_ca_der_sha256': ['one']}} for h in mod.HOSTS}
        hosts['k8s-control01']['tls_report']['prom_ca_der_sha256'] = ['one']
        self.assertTrue(mod.summary(hosts)['all_three_signer_ca_in_prom_bundle'])
        hosts['k8s-control02']['tls_report']['signer_ca_der_sha256'] = ['two']
        self.assertFalse(mod.summary(hosts)['all_three_signer_ca_in_prom_bundle'])
        del hosts['k8s-worker03']
        self.assertFalse(mod.summary(hosts)['collection_ok'])

    def test_public_certificate_parsing(self):
        # Disposable test-only key and certificate; never committed or used in runtime.
        with tempfile.TemporaryDirectory() as d:
            cert, key = pathlib.Path(d) / 'cert.pem', pathlib.Path(d) / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-subj', '/CN=synthetic-test', '-keyout', str(key), '-out', str(cert)],
                           check=True, capture_output=True)
            self.assertEqual(len(mod.certificates(cert.read_bytes())), 1)
            with self.assertRaises(Exception):
                mod.certificates(key.read_bytes())
        with self.assertRaises(Exception):
            mod.certificates(b'-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----')

    def test_prom_loaded_config_secret_omission_and_strict_refs(self):
        cfg = 'scrape_configs:\n'
        targets = []
        for name, metrics in mod.JOBS.items():
            cfg += ('- job_name: ' + name + '\n  scheme: https\n  metrics_path: ' + metrics + '\n'
                    '  tls_config:\n    ca_file: ' + mod.CA + '\n    insecure_skip_verify: false\n'
                    '  authorization:\n    type: Bearer\n    credentials_file: ' + mod.TOKEN + '\n')
            targets.append({'scrapePool': name, 'scrapeUrl': 'https://10.1.93.108:10250' + metrics,
                            'health': 'down', 'lastScrape': 'time', 'lastError': 'IP SAN error DO_NOT_LOG'})
        cfg += '- job_name: unrelated\n  bearer_token: DO_NOT_LOG\n'
        obj = {'loaded_config_b64': b64(json.dumps({'status': 'success', 'data': {'yaml': cfg}})),
               'targets_b64': b64(json.dumps({'status': 'success', 'data': {'activeTargets': targets}})),
               'prom_ca_b64': b64('unused'), 'pod': {'uid': 'synthetic'}}
        from unittest.mock import patch
        with patch.object(mod, 'certificates', return_value=['public-fingerprint']):
            r = mod.prometheus(obj)
            self.assertEqual(r['pending'], [])
            self.assertNotIn('DO_NOT_LOG', json.dumps(r))
            obj['loaded_config_b64'] = b64(json.dumps({'status': 'success', 'data': {
                'yaml': cfg.replace('insecure_skip_verify: false', 'insecure_skip_verify: true')}}))
            self.assertEqual(len(mod.prometheus(obj)['pending']), 2)

    def test_play_remote_tasks_are_only_raw_and_memory(self):
        import yaml
        play = yaml.safe_load((ROOT / 'collect.yml').read_text())
        first = play[0]
        self.assertFalse(first['gather_facts'])
        for task in first['tasks']:
            self.assertTrue(task['no_log'])
            if 'ansible.builtin.copy' in task:
                self.assertEqual(task['delegate_to'], 'localhost')
            else:
                self.assertTrue('ansible.builtin.raw' in task or 'ansible.builtin.set_fact' in task)


if __name__ == '__main__':
    unittest.main()
