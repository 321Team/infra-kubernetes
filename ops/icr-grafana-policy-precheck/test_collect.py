"""Synthetic read-only collector tests; never a live Kubernetes test."""
import copy
import json
import subprocess
import types
import unittest
from unittest.mock import patch
import collect as c


def meta(name, namespace=None, uid='fixture-uid'):
    m = {'name': name, 'uid': uid, 'resourceVersion': '123', 'labels': {'app': 'grafana'}}
    if namespace:
        m['namespace'] = namespace
    return m


def pod():
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': meta(c.POD, c.NS, c.UID),
            'spec': {'nodeName': 'k8s-worker03', 'serviceAccountName': 'default',
                     'containers': [{'name': 'grafana', 'env': [{'name': 'SECRET', 'value': 'DO_NOT_EXPORT'}]}]},
            'status': {'hostIP': '10.1.93.108', 'podIP': '10.244.39.217', 'phase': 'Running',
                       'conditions': [{'type': 'Ready', 'status': 'True'}],
                       'containerStatuses': [{'name': 'grafana', 'imageID': c.IMAGE, 'ready': True,
                                              'state': {'running': {'startedAt': 'fixture'}}}]}}


def policy_list(api='networking.k8s.io/v1', kind='NetworkPolicy', namespace=c.NS):
    return {'apiVersion': api, 'kind': kind + 'List', 'metadata': {'resourceVersion': '12'},
            'items': [{'metadata': meta('fixture-policy', namespace), 'spec': {'podSelector': {}, 'egress': [], 'policyTypes': ['Egress']}}]}


def responses():
    result = [c.API, pod(), {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': meta(c.NS)},
              {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': meta('default', c.NS),
               'secrets': [{'name': 'DO_NOT_EXPORT'}]}]
    result += [policy_list(api, kind, ns) for _, _, api, kind, ns in c.POLICY_READS]
    return result + [pod()]


class CollectorTests(unittest.TestCase):
    def collect_with(self, values):
        proc = []
        for x in values:
            if isinstance(x, Exception):
                proc.append(x)
            elif isinstance(x, subprocess.CompletedProcess):
                proc.append(x)
            else:
                proc.append(subprocess.CompletedProcess([], 0, x if isinstance(x, str) else json.dumps(x), ''))
        with patch.object(c.socket, 'gethostname', return_value='k8s-control01'), \
             patch.object(c.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_name='svc_ansible')), \
             patch.object(c.os.path, 'isfile', return_value=True), \
             patch.object(c.os, 'access', return_value=True), \
             patch.object(c.os, 'stat', return_value=types.SimpleNamespace(st_uid=0, st_mode=0o755)), \
             patch.object(c.subprocess, 'run', side_effect=proc) as run:
            report, rc = c.collect()
        return report, rc, run.call_args_list

    def test_complete_is_inputs_only_and_exact_read_commands(self):
        r, rc, calls = self.collect_with(responses())
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 11)
        self.assertEqual(r['result'], 'COLLECTED_PENDING_POLICY_REVIEW')
        self.assertEqual(r['probe'], 'NOT_RUN')
        self.assertEqual(r['policy_verdict'], 'NOT_ASSESSED')
        expected = [c.POD_PATH, '/api/v1/namespaces/' + c.NS,
                    '/api/v1/namespaces/' + c.NS + '/serviceaccounts/default']
        expected += [v[1] for v in c.POLICY_READS] + [c.POD_PATH]
        for call, path in zip(calls[1:], expected):
            self.assertEqual(call.args[0][5:], ['get', '--raw=' + path])
            self.assertEqual(call.kwargs['timeout'], 15)
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(r))

    def test_no_control_plane_api_fallback(self):
        r, rc, calls = self.collect_with(['https://10.1.93.102:6443'])
        self.assertEqual(rc, 2); self.assertEqual(len(calls), 1)

    def test_replacement_uid_stops_before_policy_reads(self):
        values = responses(); values[1]['metadata']['uid'] = 'replacement'
        r, rc, calls = self.collect_with(values)
        self.assertEqual(rc, 2); self.assertEqual(len(calls), 2)

    def test_image_or_node_or_sa_or_ip_changes_rejected(self):
        for group, key, value in [('spec', 'nodeName', 'k8s-worker01'), ('spec', 'serviceAccountName', 'prometheus'),
                                  ('status', 'podIP', '10.244.39.219'), ('status', 'hostIP', '10.1.93.105')]:
            p = pod(); p[group][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): c.project_pod(p)
        p = pod(); p['status']['containerStatuses'][0]['imageID'] = 'changed'
        with self.assertRaises(ValueError): c.project_pod(p)

    def test_host_privileged_and_not_ready_are_rejected(self):
        for key in ('hostNetwork', 'hostPID', 'hostIPC'):
            p = pod(); p['spec'][key] = True
            with self.subTest(key=key), self.assertRaises(ValueError): c.project_pod(p)
        p = pod(); p['spec']['initContainers'] = [{'securityContext': {'privileged': True}}]
        with self.assertRaises(ValueError): c.project_pod(p)
        p = pod(); p['status']['containerStatuses'][0]['ready'] = False
        with self.assertRaises(ValueError): c.project_pod(p)

    def test_projection_omits_annotations_and_secret_references(self):
        p = pod(); p['metadata']['annotations'] = {'value': 'DO_NOT_EXPORT'}
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(c.project_pod(p)))
        sa = responses()[3]
        self.assertNotIn('secrets', c.project_identity(sa, 'ServiceAccount', 'default', c.NS))

    def test_incomplete_or_oversized_lists_stop(self):
        for field, value in [('continue', 'token'), ('remainingItemCount', 2)]:
            p = policy_list(); p['metadata'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                c.project_list(p, 'networking.k8s.io/v1', 'NetworkPolicy', c.NS)
        p = policy_list(); p['items'] *= 101
        with self.assertRaises(ValueError): c.project_list(p, 'networking.k8s.io/v1', 'NetworkPolicy', c.NS)

    def test_wrong_kind_namespace_api_and_duplicates_stop(self):
        for key, value in [('namespace', 'other'), ('uid', '')]:
            p = policy_list(); p['items'][0]['metadata'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                c.project_list(p, 'networking.k8s.io/v1', 'NetworkPolicy', c.NS)
        for key, value in [('kind', 'Secret'), ('apiVersion', 'v1')]:
            p = policy_list(); p['items'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                c.project_list(p, 'networking.k8s.io/v1', 'NetworkPolicy', c.NS)
        p = policy_list(); p['items'].append(copy.deepcopy(p['items'][0]))
        with self.assertRaises(ValueError): c.project_list(p, 'networking.k8s.io/v1', 'NetworkPolicy', c.NS)

    def test_empty_complete_list_is_retained_without_verdict(self):
        p = policy_list(); p['items'] = []
        self.assertEqual(c.project_list(p, 'networking.k8s.io/v1', 'NetworkPolicy', c.NS)['items'], [])

    def test_forbidden_stops_without_error_content_leak(self):
        values = responses(); values[4] = subprocess.CompletedProcess([], 1, 'DO_NOT_EXPORT', 'Forbidden DO_NOT_EXPORT')
        r, rc, calls = self.collect_with(values)
        self.assertEqual(rc, 2); self.assertEqual(len(calls), 5)
        self.assertEqual(r['queries'][-1]['status'], 'FORBIDDEN')
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(r))

    def test_missing_crd_is_not_empty_policy_success(self):
        values = responses(); values[7] = subprocess.CompletedProcess([], 1, '', 'NotFound')
        r, rc, calls = self.collect_with(values)
        self.assertEqual(rc, 2); self.assertEqual(len(calls), 8)

    def test_timeout_and_malformed_and_oversized_are_stop(self):
        for value in (subprocess.TimeoutExpired('fixture', 15), '{bad json', 'x' * 4_000_001):
            values = responses(); values[4] = value
            with self.subTest(value_type=type(value).__name__):
                r, rc, calls = self.collect_with(values)
                self.assertEqual(rc, 2); self.assertEqual(len(calls), 5)

    def test_pod_label_drift_stops_after_reads(self):
        values = responses(); values[-1]['metadata']['labels']['extra-selector'] = 'changed'
        r, rc, _ = self.collect_with(values)
        self.assertEqual(rc, 2); self.assertEqual(r['reason'], 'GRAFANA_CHANGED_DURING_COLLECTION')

    def test_wrong_real_account_never_queries(self):
        with patch.object(c.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_name='root')), \
             patch.object(c.subprocess, 'run') as run:
            r, rc = c.collect()
        self.assertEqual(rc, 2); run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
