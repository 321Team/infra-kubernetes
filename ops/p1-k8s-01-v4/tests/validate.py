#!/usr/bin/env python3
"""Offline tests only. Synthetic inputs never authorize runtime deployment."""
import copy
import hashlib
import importlib.util
import ipaddress
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
import render


def load(name, file):
    s = importlib.util.spec_from_file_location(name, ROOT / 'code' / file)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


audit = load('audit_window', 'audit-window.py')
change = load('cluster_change', 'cluster-change.py')
calico = load('prepare_calico', 'prepare-calico.py')
host = load('node_evidence', 'node-evidence.py')


def fixture():
    c = json.loads((ROOT / 'config/runtime-input.json').read_text())
    c.update(attempt_id='OFFLINE_TEST_ONLY', cluster_uid='00000000-0000-0000-0000-000000000001',
             mtu=1450, probe_image='test.invalid/python@sha256:' + 'a' * 64)
    c['system_images']['mode'] = 'preloaded'
    for n in c['nodes'].values():
        n.update(interface='ens160', ntp_nets=['192.0.2.123/32'])
    return c


def allows(policy, direction, proto, source, destination, port):
    for r in policy['spec'][direction]:
        if 'protocol' in r and r['protocol'] != proto:
            continue
        matched = True
        for key, value in (('source', source), ('destination', destination)):
            q = r.get(key, {})
            if 'selector' in q or 'namespaceSelector' in q:
                matched = False  # Static IP tests deliberately do not simulate endpoint labels.
            if 'nets' in q and not any(ipaddress.ip_address(value) in ipaddress.ip_network(n) for n in q['nets']):
                matched = False
        if 'ports' in r.get('destination', {}) and port not in r['destination']['ports']:
            matched = False
        if matched:
            return r['action'] == 'Allow'
    return False


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.c = fixture()
        self.w = render.policies(self.c, 'k8s-worker01')[0]
        self.cp = render.policies(self.c, 'k8s-control01')[0]

    def test_fixed_flow_matrix(self):
        cases = [
            (self.w, 'ingress', 'TCP', '10.250.10.10', '10.1.93.105', 22, True),
            (self.w, 'ingress', 'TCP', '10.1.93.23', '10.1.93.105', 22, False),
            (self.cp, 'ingress', 'TCP', '10.1.93.109', '10.1.93.102', 6443, True),
            (self.w, 'ingress', 'TCP', '10.1.93.109', '10.1.93.105', 6443, False),
            (self.w, 'ingress', 'TCP', '10.1.93.102', '10.1.93.105', 6443, False),
            (self.w, 'ingress', 'TCP', '10.1.93.102', '10.1.93.105', 2379, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.102', 2379, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.102', 2380, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.107', 10250, False),
            (self.cp, 'egress', 'TCP', '10.1.93.102', '10.1.93.105', 10250, True),
            (self.cp, 'ingress', 'TCP', '10.1.93.103', '10.1.93.102', 2379, True),
            (self.cp, 'ingress', 'TCP', '10.1.93.103', '10.1.93.102', 2380, True),
            (self.cp, 'ingress', 'TCP', '10.1.93.105', '10.1.93.102', 2379, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.250.10.10', 22, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.109', 6443, True),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.54', 443, True),
            (self.cp, 'egress', 'TCP', '10.1.93.102', '10.1.93.54', 443, False),
            (self.w, 'ingress', 'UDP', '10.1.93.108', '10.1.93.105', 4789, True),
            (self.w, 'ingress', 'UDP', '10.1.93.55', '10.1.93.105', 4789, False),
            (self.w, 'ingress', 'TCP', '10.1.93.102', '10.1.93.105', 179, False),
            (self.w, 'ingress', 4, '10.1.93.102', '10.1.93.105', None, False),
            (self.w, 'egress', 'UDP', '10.1.93.105', '192.0.2.123', 123, True),
            (self.w, 'egress', 'UDP', '10.1.93.105', '192.0.2.124', 123, False),
            # ICR-D-NET-01-01 cross-node scrape: worker peers only, 9100/10250 only.
            (self.w, 'ingress', 'TCP', '10.1.93.108', '10.1.93.105', 9100, True),
            (self.w, 'ingress', 'TCP', '10.1.93.108', '10.1.93.105', 10250, True),
            (self.w, 'ingress', 'TCP', '10.1.93.107', '10.1.93.105', 9100, True),
            (self.w, 'ingress', 'TCP', '10.1.93.105', '10.1.93.105', 9100, False),
            (self.w, 'ingress', 'TCP', '10.1.93.102', '10.1.93.105', 9100, False),
            (self.w, 'ingress', 'TCP', '10.1.93.55', '10.1.93.105', 9100, False),
            (self.w, 'ingress', 'TCP', '10.1.93.108', '10.1.93.105', 9101, False),
            (self.w, 'ingress', 'UDP', '10.1.93.108', '10.1.93.105', 9100, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.107', 9100, False),
            (self.cp, 'ingress', 'TCP', '10.1.93.108', '10.1.93.102', 9100, False),
            (self.cp, 'ingress', 'TCP', '10.1.93.108', '10.1.93.102', 10250, False),
            (self.w, 'ingress', 'TCP', '10.1.93.109', '10.1.93.105', 9100, False),
            (self.w, 'ingress', 'TCP', '10.1.93.109', '10.1.93.105', 10250, False),
            (self.w, 'ingress', 'TCP', '10.250.10.10', '10.1.93.105', 9100, False),
            (self.w, 'ingress', 'TCP', '10.250.10.10', '10.1.93.105', 10250, False)]
        for proto in ('UDP', 'TCP'):
            for dns in ('10.1.93.52', '10.1.93.53'):
                cases.append((self.w, 'egress', proto, '10.1.93.105', dns, 53, True))
            cases.append((self.w, 'egress', proto, '10.1.93.105', '8.8.8.8', 53, False))
        self.assertEqual(len(cases), 44)
        for policy, direction, proto, src, dst, port, expect in cases:
            with self.subTest(direction=direction, src=src, dst=dst, port=port):
                self.assertEqual(allows(policy, direction, proto, src, dst, port), expect)

    def test_worker_scrape_rule_excludes_self_and_precedes_deny(self):
        for name in ('k8s-worker01', 'k8s-worker02', 'k8s-worker03'):
            ing = render.policies(self.c, name)[0]['spec']['ingress']
            scrape = [r for r in ing if r.get('destination', {}).get('ports') == [9100, 10250]]
            self.assertEqual(len(scrape), 1)
            own = self.c['nodes'][name]['ip'] + '/32'
            self.assertEqual(sorted(scrape[0]['source']['nets']),
                             sorted(x for x in render.WORKERS if x != own))
            self.assertEqual(ing[-1], {'action': 'Deny'})
            self.assertLess(ing.index(scrape[0]), len(ing) - 1)

    def test_failsafe_does_not_reopen_dns_or_worker_cp_ports(self):
        for name in render.ORDER:
            _, _, f = render.policies(self.c, name)
            for direction in f['spec']:
                for r in f['spec'][direction]:
                    self.assertEqual(ipaddress.ip_network(r['net']).prefixlen, 32)
                    self.assertNotIn(r['port'], [53, 179, 10250])
                    if 'worker' in name and direction == 'failsafeInboundHostPorts':
                        self.assertEqual(r['port'], 22)
                    if 'worker' in name:
                        self.assertNotIn(r['port'], [2379, 2380])

    def test_missing_inputs_block_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'not-created'
            r = subprocess.run([sys.executable, str(ROOT / 'code/render.py'), '--config',
                                str(ROOT / 'config/runtime-input.json'), '--out', str(out)],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(out.exists())

    def test_ntp_unknown_and_broad_nets_rejected(self):
        for value in ([], ['0.0.0.0/0'], ['10.1.93.0/24'], ['127.0.0.1/32']):
            c = copy.deepcopy(self.c)
            c['nodes']['k8s-worker01']['ntp_nets'] = value
            with self.assertRaises(ValueError):
                render.validate(c, ROOT, False)

    def test_roles_api_reciprocity_and_workload_scope(self):
        n = self.c['nodes']
        n['k8s-worker01']['api_egress_nets'] = ['10.1.93.102/32']
        with self.assertRaises(ValueError):
            render.validate(self.c, ROOT, False)
        n['k8s-control01']['api_ingress_node_sources'] = ['10.1.93.105/32']
        render.validate(self.c, ROOT, False)
        n['k8s-worker01']['api_ingress_node_sources'] = ['10.1.93.102/32']
        with self.assertRaises(ValueError):
            render.validate(self.c, ROOT, False)

    def test_evidence_hash_validation(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'fact.txt').write_text('SYNTHETIC')
            with self.assertRaises(ValueError):
                render.evidence({'path': 'fact.txt', 'sha256': '0' * 64}, Path(d))

    def test_probe_manifest_restricted_and_no_external_service(self):
        docs = render.probes(self.c)
        pods = [x for x in docs if x['kind'] == 'Pod']
        self.assertEqual(len(pods), 3)
        self.assertNotEqual(pods[0]['spec']['nodeSelector'], pods[1]['spec']['nodeSelector'])
        for pod in pods:
            c = pod['spec']['containers'][0]
            self.assertFalse(pod['spec']['automountServiceAccountToken'])
            self.assertEqual(c['imagePullPolicy'], 'Never')
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertEqual(c['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertEqual(docs[-1]['spec']['type'], 'ClusterIP')


class OperationalTests(unittest.TestCase):
    def rollback_scenario(self, receipt_mode):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            gen, state = root / 'generated', root / 'state'
            gen.mkdir(); state.mkdir(); (gen / 'felix').mkdir()
            c = fixture()
            node = render.ORDER[0]
            _, hep, f = render.policies(c, node)
            hep['metadata'].update(uid='hep-uid', resourceVersion='11')
            felix = {'metadata': {'name': 'node.' + node, 'uid': 'felix-uid', 'resourceVersion': '12'},
                     'spec': f['spec']}
            (gen / 'config.lock.json').write_text(json.dumps(c))
            (gen / 'felix' / (node + '.json')).write_text(json.dumps(f))
            lines = [hashlib.sha256(x.read_bytes()).hexdigest() + '  ' + str(x.relative_to(gen))
                     for x in gen.rglob('*') if x.is_file()]
            (gen / 'SHA256SUMS').write_text('\n'.join(lines))
            configsha = hashlib.sha256((gen / 'config.lock.json').read_bytes()).hexdigest()
            change.dump(state / 'state-binding.json', {'cluster_uid': c['cluster_uid'],
                        'attempt_id': c['attempt_id'], 'config_sha256': configsha})
            change.dump(state / (node + '-hep-created.json'), hep)
            change.dump(state / (node + '-felix-before.json'), None)
            change.dump(state / (node + '-felix-after.json'), felix)
            proof = root / 'proof.txt'; proof.write_text('SYNTHETIC TEST ONLY')
            receipt = {'node': node, 'cluster_uid': c['cluster_uid'], 'attempt_id': c['attempt_id'],
                       'config_sha256': configsha, 'status': 'PROTECTION_RESTORED_REVIEWED',
                       'evidence': [{'path': str(proof), 'sha256': hashlib.sha256(proof.read_bytes()).hexdigest()}]}
            if receipt_mode == 'wrong-node': receipt['node'] = 'k8s-worker02'
            rp = root / 'receipt.json'; change.dump(rp, receipt)
            actions = []
            class Fake:
                h = copy.deepcopy(hep)
                f = copy.deepcopy(felix)
                def __init__(self, prefix): pass
                def get(self, kind, name=None, optional=False):
                    if kind == 'namespace': return {'metadata': {'uid': c['cluster_uid']}}
                    if kind.startswith('hostendpoints'):
                        return self.h if name else {'items': [self.h] if self.h else []}
                    if kind.startswith('felixconfigurations'): return self.f
                    raise AssertionError(kind)
                def remove_exact(self, plural, current):
                    actions.append(plural)
                    if plural == 'hostendpoints': self.h = None
                    elif plural == 'felixconfigurations': self.f = None
                    else: raise AssertionError(plural)
            args = ['cluster-change.py', 'rollback-node', '--node', node, '--bundle', str(gen),
                    '--state', str(state), '--kubeconfig', '/SYNTHETIC', '--context', 'SYNTHETIC']
            if receipt_mode != 'missing': args += ['--protection-receipt', str(rp)]
            with patch.object(change, 'Cluster', Fake), patch.object(sys, 'argv', args):
                if receipt_mode in ('missing', 'wrong-node'):
                    with self.assertRaises(ValueError): change.main()
                    self.assertEqual(actions, [])
                else:
                    change.main()
                    self.assertEqual(actions, ['hostendpoints', 'felixconfigurations'])

    def test_missing_protection_receipt_blocks_all_rollback_mutations(self):
        self.rollback_scenario('missing')

    def test_wrong_node_receipt_blocks_all_rollback_mutations(self):
        self.rollback_scenario('wrong-node')

    def test_recovery_order_after_bound_protection_receipt(self):
        self.rollback_scenario('valid')

    def audit_query(self, avc, markers=None, start=1790000000, end=1790000100, now=1790000200):
        """Run audit-window.py query against synthetic ausearch results; return result.json."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            mark = 'FDS_G2_SYNTHETIC'
            (root / 'window.json').write_text(json.dumps(
                {'node': 'synthetic-node', 'start_epoch': start, 'end_epoch': end, 'marker': mark}))
            status = 'enabled 1\nlost 0\n'
            (root / 'audit-status-start.txt').write_text(status)
            (root / 'audit-status-end.txt').write_text(status)
            if markers is None:
                markers = (0, ('type=USER msg=audit(%d.000:1): msg=%s_START\n----\n'
                               'type=USER msg=audit(%d.000:2): msg=%s_END\n') % (start, mark, end, mark), '')
            argvs = []
            def fake_call(args):
                argvs.append(args)
                rc, out, err = {'Enforcing': (0, 'Enforcing\n', ''), 'status': (0, status, '')}.get(
                    'Enforcing' if args[0] == 'getenforce' else 'status' if args[0] == 'auditctl' else '',
                    avc if 'AVC,USER_AVC,SELINUX_ERR,USER_SELINUX_ERR' in args
                    else markers if args[0] == 'ausearch' else (0, '', ''))
                return subprocess.CompletedProcess(args, rc, out, err)
            with patch.object(audit, 'call', fake_call), patch.object(audit.os, 'geteuid', return_value=0), \
                    patch.object(audit.socket, 'gethostname', return_value='synthetic-node'), \
                    patch.object(audit.time, 'time', return_value=now), \
                    patch.object(sys, 'argv', ['audit-window.py', 'query', '--dir', str(root)]):
                try:
                    audit.main()
                except SystemExit as e:
                    self.assertEqual(e.code, 2)
            result = json.loads((root / 'result.json').read_text())
            result['argvs'] = argvs
            return result

    def test_audit_rc1_empty_stdout_is_hold_not_zero_avc(self):
        r = self.audit_query((1, '', ''))
        self.assertEqual((r['status'], r['no_matches'], r['avc_query']), ('HOLD', False, 'ERROR'))

    def test_audit_rc1_exact_no_matches_is_zero_avc(self):
        r = self.audit_query((1, '<no matches>\n', ''))
        self.assertEqual((r['status'], r['no_matches'], r['avc_query']),
                         ('NO_AVC_REVIEW_REQUIRED', True, 'NO_MATCH'))

    def test_audit_rc0_avc_in_window_is_hold(self):
        avc = 'type=AVC msg=audit(1790000050.123:9): avc:  denied  { read } for pid=1\n'
        r = self.audit_query((0, avc, ''))
        self.assertEqual((r['status'], r['no_matches'], r['avc_query']), ('HOLD', False, 'OK'))

    def test_audit_rc0_avc_outside_window_only_is_zero_avc(self):
        avc = 'type=AVC msg=audit(1789999000.123:9): avc:  denied  { read } for pid=1\n'
        r = self.audit_query((0, avc, ''))
        self.assertEqual((r['status'], r['no_matches']), ('NO_AVC_REVIEW_REQUIRED', True))

    def test_audit_stderr_failure_is_hold(self):
        for avc in ((1, '', 'Error opening config file\n'), (1, '<no matches>\n', 'warning\n'), (2, '', 'boom\n')):
            r = self.audit_query(avc)
            self.assertEqual((r['status'], r['no_matches'], r['avc_query']), ('HOLD', False, 'ERROR'), avc)

    def test_audit_marker_query_failure_is_hold(self):
        r = self.audit_query((1, '<no matches>\n', ''), markers=(1, '', ''))
        self.assertEqual((r['status'], r['both_markers_retained'], r['markers_query']), ('HOLD', False, 'ERROR'))

    def test_audit_window_crossing_midnight_uses_yesterday(self):
        midnight = 1790035200  # 2026-09-22 00:00:00 UTC
        self.assertEqual(audit.search_start(midnight + 10, midnight + 100), 'today')
        self.assertEqual(audit.search_start(midnight - 10, midnight + 100), 'yesterday')
        self.assertIsNone(audit.search_start(midnight - 86401, midnight + 100))
        r = self.audit_query((1, '<no matches>\n', ''), start=midnight - 60, end=midnight + 60, now=midnight + 100)
        self.assertTrue(all(a[-1] == 'yesterday' for a in r['argvs'] if a[0] == 'ausearch'))
        r = self.audit_query((1, '<no matches>\n', ''), start=midnight - 86401, end=midnight, now=midnight + 100)
        self.assertEqual((r['status'], r['avc_query']), ('HOLD', 'NOT_RUN'))

    def preflight_with(self, weps_error, pods):
        """Run cluster-change.py preflight with a fake cluster; return the state directory contents."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bundle = root / 'bundle'; bundle.mkdir()
            (bundle / 'config.lock.json').write_text(json.dumps(
                {'cluster_uid': 'uid-synthetic', 'attempt_id': 'OFFLINE_TEST_ONLY'}))
            (bundle / 'SHA256SUMS').write_text(
                hashlib.sha256((bundle / 'config.lock.json').read_bytes()).hexdigest() + '  config.lock.json\n')
            node = {'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
            class Fake:
                def __init__(self, prefix): pass
                def get(self, kind, name=None, optional=False):
                    if kind == 'namespace': return {'metadata': {'uid': 'uid-synthetic'}}
                    if kind == 'nodes':
                        return {'items': [dict(node, metadata={'name': n}) for n in render.ORDER]}
                    return {'items': []}
                def call(self, *args, data=None):
                    if args[1].startswith('workloadendpoints'):
                        raise RuntimeError(weps_error)
                    if args[1] == 'pods':
                        return json.dumps({'items': pods})
                    raise AssertionError(args)
            state = root / 'state'
            args = ['cluster-change.py', 'preflight', '--bundle', str(bundle), '--state', str(state),
                    '--kubeconfig', '/SYNTHETIC', '--context', 'SYNTHETIC']
            with patch.object(change, 'Cluster', Fake), patch.object(sys, 'argv', args):
                change.main()
            return json.loads((state / 'wep-fallback.json').read_text())

    def test_missing_wep_falls_back_to_pod_labels(self):
        absent = 'error: the server doesn\'t have a resource type "workloadendpoints"'
        clean = [{'metadata': {'labels': {'app': 'x'}}}]
        self.assertEqual(self.preflight_with(absent, clean)['source'], 'pods-as-fallback')
        selected = [{'metadata': {'labels': {render.LABEL: render.BUNDLE}}}]
        with self.assertRaisesRegex(ValueError, 'selects a workload'):
            self.preflight_with(absent, selected)
        with self.assertRaises(RuntimeError):
            self.preflight_with('Unable to connect to the server', clean)

    def test_raw_delete_binds_uid_and_resource_version(self):
        k = change.Cluster(['kubectl'])
        current = {'metadata': {'name': 'one', 'uid': 'uid1', 'resourceVersion': '17'}}
        with patch.object(k, 'call') as mocked:
            k.remove_exact('hostendpoints', current)
            args = mocked.call_args
            self.assertIn('-f', args.args)
            self.assertEqual(json.loads(args.kwargs['data'])['preconditions'],
                             {'uid': 'uid1', 'resourceVersion': '17'})

    def test_bundle_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'one').write_text('bad')
            (root / 'SHA256SUMS').write_text('0' * 64 + '  one\n')
            with self.assertRaises(ValueError):
                change.verify_bundle(root)
            (root / 'SHA256SUMS').write_text('0' * 64 + '  ../one\n')
            with self.assertRaises(ValueError):
                change.verify_bundle(root)

    def test_cockpit_empty_ss_error_is_not_pass(self):
        import os
        if os.geteuid() != 0:
            self.skipTest('shell under test requires root; no privilege escalation in tests')
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / 'systemctl').write_text('#!/bin/bash\nprintf "Id=cockpit.socket\\nLoadState=masked\\nUnitFileState=masked\\nActiveState=inactive\\n"\n')
            (folder / 'ss').write_text('#!/bin/bash\nexit 1\n')
            for f in folder.iterdir(): f.chmod(0o755)
            r = subprocess.run(['bash', str(ROOT / 'code/cockpit.sh'), 'check'],
                               env=dict(os.environ, PATH=d + ':' + os.environ['PATH']), capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn('SS_QUERY_ERROR', r.stderr)

    def test_firewall_evidence_capture_records_errors(self):
        r = host.run(['/DOES_NOT_EXIST_FOR_OFFLINE_TEST'], False)
        self.assertEqual(r['rc'], 125)
        self.assertTrue(r['stderr'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
