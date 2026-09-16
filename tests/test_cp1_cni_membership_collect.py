"""Collector command contracts and raw-capture-to-consumer integration (MOCK)."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_cp1_cni_membership import BOOT, member, raw_fixture, setup, m

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('managed_collector_test', ROOT / 'scripts/collect-cp1-cni-membership.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)


def make_node(edit_after=None, edit_peer=None, edit_status=None, bad_query=None):
    raw = raw_fixture()
    counts, calls, cleaned = {}, [], []
    metadata = raw['pods']['items'][0]['metadata']
    sandbox = member()['sandbox_id']
    status = {'id': sandbox, 'metadata': copy.deepcopy(metadata), 'state': 'SANDBOX_READY'}
    peer = {'ifname': 'eth0', 'ifindex': 2, 'link_index': 10, 'linkinfo': {'info_kind': 'veth'},
            'addr_info': [{'family': 'inet', 'local': member()['ip']}]}
    if edit_peer: edit_peer(peer)
    if edit_status: edit_status(status)
    def result(argv):
        calls.append(argv)
        key = tuple(argv)
        counts[key] = counts.get(key, 0) + 1
        if argv[0] == 'kubectl':
            kind = {'pods': 'pods', 'ipamblocks.crd.projectcalico.org': 'blocks',
                    'blockaffinities.crd.projectcalico.org': 'affinities'}[argv[2]]
            value = copy.deepcopy(raw[kind])
            if counts[key] == 2 and edit_after: edit_after(kind, value)
        elif argv[0] == 'ip':
            value = copy.deepcopy(raw['links' if 'link' in argv else 'routes'])
        elif argv[:2] == ['crictl', 'pods']:
            value = {'items': [{'id': sandbox, 'metadata': metadata, 'state': 'SANDBOX_READY'}]}
        elif argv[:2] == ['crictl', 'inspectp']:
            value = {'status': status, 'info': {'pid': 123}}
        elif argv[0] == 'nsenter':
            value = [peer]
        elif argv[0] == 'stat':
            value = '999'
        elif argv[1].endswith('/stat'):
            value = '123 (odd (command)) ' + ' '.join(['S'] + ['0']*18 + ['111'])
        else:
            value = BOOT
        response = {'rc': 0, 'stdout': value if isinstance(value, str) else json.dumps(value),
                    'stderr': '', 'stdout_truncated': False, 'stderr_truncated': False}
        if bad_query: bad_query(argv, response)
        return response
    class Cache:
        def cleanup(self): cleaned.append(True)
    class Backend:
        def __init__(self, hostname): self.cache = Cache()
        def identity(self): calls.append(['identity'])
        def kubectl(self, argv): return result(['kubectl'] + argv)
    class Node:
        BaseBackend = Backend
        COMMAND_STATE = {}
        @staticmethod
        def binary(name): return '/usr/sbin/' + name
        @staticmethod
        def command(argv, timeout):
            assert timeout == 5
            return result(argv)
    return Node, calls, cleaned


class CollectorTests(unittest.TestCase):
    def test_late_sandbox_pid_peer_and_netns_changes_are_not_copied_from_first_sample(self):
        for target in ('pid','peer','netns'):
            counts={}
            def alter(argv,result):
                key=tuple(argv);counts[key]=counts.get(key,0)+1
                if target=='pid' and argv[:2]==['crictl','inspectp'] and counts[key]==2:
                    value=json.loads(result['stdout']);value['info']['pid']=999;result['stdout']=json.dumps(value)
                if target=='peer' and argv[0]=='nsenter' and counts[key]==2:
                    value=json.loads(result['stdout']);value[0]['link_index']=999;result['stdout']=json.dumps(value)
                if target=='netns' and argv[0]=='stat' and counts[key]==3:result['stdout']='1000'
            node,_,_=make_node(bad_query=alter)
            with self.subTest(target=target):
                result=self.capture(node)
                self.assertFalse(result['complete']);self.assertTrue(result['errors'])

    def test_boolean_exit_code_and_nonboolean_truncation_flags_are_rejected(self):
        for key,value in [('rc',False),('stdout_truncated',0),('stderr_truncated',None)]:
            def alter(argv,result):result[key]=value
            node,_,_=make_node(bad_query=alter)
            with self.subTest(key=key):self.assertFalse(self.capture(node)['complete'])

    def capture(self, node):
        with patch.object(c.time, 'time', return_value=1000), patch.object(c.time, 'clock_gettime', return_value=110):
            return c.collect(node)

    def test_collector_capture_to_manager_to_consumer(self):
        node, calls, cleaned = make_node()
        capture = self.capture(node)
        self.assertTrue(capture['complete'], capture['errors'])
        manager, io, _ = setup()
        obs = m.observation_from_capture(capture, manager.lease, io.snapshot(), io.clock())
        state = manager.reconcile(obs)
        live = io.snapshot(); live.update(generation_sha256=m.validate_generation_chain(state), target_registered=False)
        result = m.consume(state, obs, live, io.clock(), target='k8s-control03', stage='AFTER_TARGET',
                           expected_lease_sha256=m.fingerprint(manager.lease))
        self.assertFalse(result['authorizes_join'])
        self.assertEqual(cleaned, [True])
        self.assertIsNone(node.COMMAND_STATE['deadline'])
        for argv in calls:
            self.assertIn(argv[0], ('identity', 'kubectl', 'ip', 'crictl', 'cat', 'stat', 'nsenter'))
            self.assertFalse({'apply', 'exec', 'add', 'delete', 'replace', 'flush'} & set(argv))

    def test_resource_version_readiness_and_unallocated_pending_churn_does_not_force_retry(self):
        def edit(kind, value):
            value['metadata']['resourceVersion'] = '999'
            if kind == 'pods':
                value['items'][0]['status']['conditions'][0]['status'] = 'True'
                value['items'].append({'metadata': {'name': 'pending', 'namespace': 'test', 'uid': 'pending'},
                                      'spec': {'nodeName': m.HOST}, 'status': {'phase': 'Pending'}})
        node, _, _ = make_node(edit_after=edit)
        result = self.capture(node)
        self.assertTrue(result['complete'], result['errors'])
        self.assertNotEqual(result['raw']['pods'], result['raw_after']['pods'])

    def test_uid_or_ip_or_ownership_change_during_capture_is_incomplete(self):
        def uid(kind, value):
            if kind == 'pods': value['items'][0]['metadata']['uid'] = 'replaced'
        def ip(kind, value):
            if kind == 'pods': value['items'][0]['status']['podIP'] = '10.244.246.81'
        def ownership(kind, value):
            if kind == 'blocks': value['items'][0]['spec']['affinity'] = 'host:other'
        for edit in (uid, ip, ownership):
            node, _, _ = make_node(edit_after=edit)
            result = self.capture(node)
            with self.subTest(edit=edit):
                self.assertFalse(result['complete'])
                self.assertTrue(result['errors'])

    def test_peer_address_and_sandbox_identity_mismatch_are_incomplete(self):
        nodes = [make_node(edit_peer=lambda p: p['addr_info'][0].update(local='10.244.246.81'))[0],
                 make_node(edit_status=lambda s: s['metadata'].update(uid='other'))[0]]
        for node in nodes:
            result = self.capture(node)
            self.assertFalse(result['complete'])

    def test_truncated_query_and_unsupported_cri_layout_preserve_errors(self):
        def truncated(argv, response):
            if argv[:2] == ['crictl', 'pods']: response['stdout_truncated'] = True
        def unsupported(argv, response):
            if argv[:2] == ['crictl', 'inspectp']:
                parsed = json.loads(response['stdout']); parsed['info']['pid'] = '123'
                response['stdout'] = json.dumps(parsed)
        for edit in (truncated, unsupported):
            node, _, cleaned = make_node(bad_query=edit)
            result = self.capture(node)
            self.assertFalse(result['complete']); self.assertTrue(result['errors'])
            self.assertEqual(cleaned, [True])

    def test_incomplete_or_stale_capture_cannot_become_manager_observation(self):
        node, _, _ = make_node(); capture = self.capture(node)
        manager, io, _ = setup()
        io.advance(30)
        with self.assertRaisesRegex(ValueError, 'stale'):
            m.observation_from_capture(capture, manager.lease, io.snapshot(), io.clock())
        capture['complete'] = False
        with self.assertRaisesRegex(ValueError, 'complete read-only'):
            m.observation_from_capture(capture, manager.lease, io.snapshot(), io.clock())

    def test_isolated_python_cli_import_and_help(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(c.__file__)), '--help'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--output', result.stdout)
        self.assertNotIn('--apply', result.stdout)

    def test_duplicate_json_and_pid_stat_parser(self):
        with self.assertRaises(ValueError): c.parse('{"a":1,"a":2}')
        with self.assertRaises(ValueError): c.parse('{"a":NaN}')
        self.assertEqual(c.pid_start('123 (odd (command)) ' + ' '.join(['S'] + ['0']*18 + ['111'])), 111)
        with self.assertRaises(ValueError): c.pid_start('bad data')


if __name__ == '__main__':
    unittest.main()
