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
            (self.w, 'egress', 'UDP', '10.1.93.105', '192.0.2.124', 123, False)]
        for proto in ('UDP', 'TCP'):
            for dns in ('10.1.93.52', '10.1.93.53'):
                cases.append((self.w, 'egress', proto, '10.1.93.105', dns, 53, True))
            cases.append((self.w, 'egress', proto, '10.1.93.105', '8.8.8.8', 53, False))
        self.assertEqual(len(cases), 29)
        for policy, direction, proto, src, dst, port, expect in cases:
            with self.subTest(direction=direction, src=src, dst=dst, port=port):
                self.assertEqual(allows(policy, direction, proto, src, dst, port), expect)

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

    def test_audit_dates_are_separate_arguments(self):
        args = audit.time_args(1709164799, 1709164801)
        self.assertEqual(args, ['-ts', '02/28/2024', '23:59:59', '-te', '02/29/2024', '00:00:01'])

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
