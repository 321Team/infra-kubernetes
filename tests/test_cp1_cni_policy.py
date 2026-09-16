"""Read-only collection and exact-membership proposal regressions; no host mutation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import test_node_firewall as original_tests
from test_node_firewall import load
from test_cp1_cni_handoff import fixture as original_fixture, NOW

p = load('cp1_policy_proposal_test', 'scripts/prepare-cp1-cni-policy.py')


def fixture():
    class FullState(original_tests.FakeBackend):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.state.update(default_zone='public', active_zone='public')
    # Add observations before the pinned original producer creates the receipt;
    # never retrofit new fields or hashes onto an already emitted receipt.
    with patch.object(original_tests, 'FakeBackend', FullState):
        old = original_fixture()
    peers = [('10.244.246.80', 'cali0123456789a'), ('10.244.246.81', 'cali0123456789b')]
    pods, links, routes = [], [], []
    for i, (ip, iface) in enumerate(peers):
        pods.append({'metadata': {'name': 'pod-' + str(i), 'namespace': 'test', 'uid': 'pod-uid-' + str(i)},
                     'spec': {'nodeName': p.HOST}, 'status': {'phase': 'Running', 'podIP': ip,
                         'podIPs': [{'ip': ip}], 'conditions': [{'type': 'Ready', 'status': 'True'}]}})
        links.append({'ifname': iface, 'ifindex': 10 + i, 'link_index': 2,
                      'linkinfo': {'info_kind': 'veth'}, 'flags': ['UP']})
        routes.append({'dst': ip, 'dev': iface, 'scope': 'link'})
    block = {'items': [{'spec': {'cidr': str(p.BLOCK), 'affinity': 'host:' + p.HOST}}]}
    affinity = {'items': [{'spec': {'cidr': str(p.BLOCK), 'node': p.HOST, 'state': 'confirmed'}}]}
    raw = {'version': '2.4.3', 'boot': '12345678-1234-1234-1234-123456789abc',
           'firewalld_config': 'FirewallBackend=nftables\n', 'pods': {'items': pods},
           'blocks': block, 'affinities': affinity, 'links': links, 'routes': routes,
           'networkpolicies': {'items': []}, 'calico_networkpolicies': {'items': []},
           'native': {'nftables': [{'table': {'family': 'inet', 'name': 'firewalld'}}]},
           'runtime/zones': 'public', 'permanent/zones': 'public',
           'runtime/zones/public': 'public\n  target: default\n  interfaces: ens160\n  sources:',
           'permanent/zones/public': 'public\n  target: default\n  interfaces:\n  sources:',
           'runtime/ipsets': '', 'permanent/ipsets': ''}
    for plane in ('runtime', 'permanent'):
        for kind in ('zones', 'ipsets'):
            key = plane + '/' + kind
            raw[key + '_after'] = raw[key]
            for name in raw[key].split():
                raw[key + '_after/' + name] = raw[key + '/' + name]
    for key in ('pods', 'blocks', 'links', 'routes', 'affinities', 'networkpolicies', 'calico_networkpolicies',
                'version', 'boot', 'firewalld_config', 'native'):
        raw[key + '_after'] = copy.deepcopy(raw[key])
    queries = {k: {'rc': 0, 'stdout': v if isinstance(v, str) else json.dumps(v), 'stderr': ''}
               for k, v in raw.items()}
    stable = {k: copy.deepcopy(old['contract']['before'][k]) for k in ('firewall', 'protection')}
    state = {'stable': stable, 'sha256': p.fingerprint(stable)}
    capture = {'schema': 'fds.cp1-cni-policy-inspection.v1', 'mode': 'READ_ONLY',
               'started_at': '2026-09-17T00:58:00+00:00', 'ended_at': '2026-09-17T00:59:00+00:00',
               'queries': queries, 'errors': [], 'state': {'before': state, 'after': copy.deepcopy(state)}}
    return {'capture': capture, 'original': old['original'], 'original_input_sha256': old['original_input_sha256'],
            'capture_input_sha256': p.fingerprint(capture), 'current': NOW}


class PolicyProposalTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def edit_json(self, name, edit):
        q = self.data['capture']['queries'][name]
        value = json.loads(q['stdout'])
        edit(value)
        q['stdout'] = json.dumps(value)

    def reject(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            p.prepare(**self.data)

    def test_exact_public_policy_preserves_zone_design_and_never_authorizes(self):
        old = copy.deepcopy(self.data)
        with patch('subprocess.run', side_effect=AssertionError('planning must be offline')):
            result = p.prepare(**self.data)
        self.assertEqual(old, self.data)
        self.assertEqual(result['status'], 'DRAFT_NOT_EXECUTABLE')
        for key in ('authorizes_apply', 'authorizes_cp2_change', 'authorizes_join', 'existing_handoff_contract_compatible'):
            self.assertIs(result[key], False)
        self.assertIs(result['scope']['dynamic_cali_prefix'], False)
        self.assertEqual(set(result['documents']), {'ipsets/' + p.IPSET + '.xml', 'policies/' + p.POLICY + '.xml'})
        root = ET.fromstring(result['documents']['policies/' + p.POLICY + '.xml'])
        self.assertEqual(root.attrib, {'target': 'CONTINUE', 'priority': '-1'})
        self.assertEqual(root.find('ingress-zone').attrib, {'name': 'public'})
        self.assertEqual(root.find('egress-zone').attrib, {'name': 'public'})
        self.assertEqual(len(root.findall('rule')), 1)
        rule = root.find('rule')
        self.assertEqual(rule.find('source').attrib, {'ipset': p.IPSET})
        self.assertEqual(rule.find('destination').attrib, {'ipset': p.IPSET})
        self.assertEqual([x.tag for x in rule], ['source', 'destination', 'accept'])
        ipset = ET.fromstring(result['documents']['ipsets/' + p.IPSET + '.xml'])
        self.assertEqual(ipset.attrib, {'type': 'hash:net,iface'})
        self.assertEqual([e.text for e in ipset.findall('entry')],
                         ['10.244.246.64/26,cali0123456789a', '10.244.246.64/26,cali0123456789b'])

    def test_existing_contract_does_not_silently_accept_new_mechanism(self):
        from test_cp1_cni_handoff import h
        proposal = p.prepare(**self.data)
        with self.assertRaisesRegex(ValueError, 'contract schema'):
            h.validate(proposal, self.data['original'], self.data['original_input_sha256'], {}, {}, NOW)

    def test_no_apply_mode_is_exposed(self):
        result = subprocess.run([sys.executable, str(Path(p.__file__)), 'apply'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)

    def test_collection_is_read_only_and_reuses_original_identity(self):
        calls, identities, snapshots = [], [], []
        state = self.data['capture']['state']['before']
        def command(argv):
            calls.append(argv)
            if '--get-zones' in argv:
                value = 'public'
            elif '--get-ipsets' in argv:
                value = 'existing'
            else:
                value = '{}'
            return {'rc': 0, 'stdout': value, 'stderr': ''}
        class Cache:
            def cleanup(self):
                pass
        class Backend:
            def __init__(self, hostname):
                self.cache = Cache()
                identities.append(hostname)
            def identity(self):
                identities.append('identity_checked')
            def cp1_state(self):
                snapshots.append(True)
                return copy.deepcopy(state)
            def kubectl(self, args):
                return command(['kubectl'] + args)
        class Node:
            BaseBackend = Backend
            COMMAND_STATE = {}
        Node.command = staticmethod(command)
        result = p.collect(Node, lambda: NOW)
        self.assertEqual(identities, [p.HOST, 'identity_checked'])
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(result['mode'], 'READ_ONLY')
        for argv in calls:
            if argv[0] == 'firewall-cmd':
                self.assertTrue(all(a in ('--version', '--permanent', '--get-zones', '--get-ipsets') or
                                    a.startswith(('--info-zone=', '--info-ipset=')) for a in argv[1:]))
            elif argv[0] == 'kubectl':
                self.assertEqual(argv[1], 'get')
            else:
                self.assertIn(argv[0], ('cat', 'ip', 'nft'))
                self.assertFalse({'add', 'delete', 'replace', 'flush', 'set'} & set(argv))

    def test_capture_error_blocks_plan(self):
        self.data['capture']['errors'] = ['failed state query']
        self.reject('capture has errors')

    def test_truncation_and_warning_block_plan(self):
        for field, value in [('rc', 1), ('stdout_truncated', True), ('stderr_truncated', True), ('stderr', 'warning')]:
            with self.subTest(field=field):
                self.data = fixture()
                self.data['capture']['queries']['native'][field] = value
                self.reject('incomplete/uncertain')

    def test_archived_capture_can_prepare_draft_without_forcing_recapture(self):
        self.data['capture']['started_at'] = '2026-09-16T00:58:00+00:00'
        self.data['capture']['ended_at'] = '2026-09-16T00:59:00+00:00'
        result = p.prepare(**self.data)
        self.assertGreater(result['capture_age_seconds'], 86400)
        self.assertIs(result['fresh_for_activation'], False)
        self.assertIs(result['authorizes_apply'], False)

    def test_long_capture_rejected(self):
        self.data['capture']['started_at'] = '2026-09-17T00:00:00+00:00'
        self.reject('capture incomplete')

    def test_unsupported_version_rejected(self):
        self.data['capture']['queries']['version']['stdout'] = '2.3.0'
        self.data['capture']['queries']['version_after']['stdout'] = '2.3.0'
        self.reject('unverified firewalld')

    def test_iptables_backend_rejected(self):
        self.data['capture']['queries']['firewalld_config']['stdout'] = 'FirewallBackend=iptables'
        self.data['capture']['queries']['firewalld_config_after']['stdout'] = 'FirewallBackend=iptables'
        self.reject('nftables backend')

    def test_original_receipt_is_not_rewritten_or_cni_stripped(self):
        self.data['capture']['state']['before']['stable']['firewall']['nm_zone'] = 'other'
        self.reject('changed during capture')

    def test_owned_name_collision_rejected(self):
        self.data['capture']['queries']['runtime/ipsets']['stdout'] = p.IPSET
        self.data['capture']['queries']['runtime/ipsets/' + p.IPSET] = {'rc': 0, 'stdout': 'existing', 'stderr': ''}
        self.reject('ipset name collision')

    def test_foreign_block_rejected(self):
        self.edit_json('blocks', lambda v: v['items'][0]['spec'].update(affinity='host:k8s-control02'))
        self.reject('block ownership')

    def test_unconfirmed_affinity_rejected(self):
        self.edit_json('affinities', lambda v: v['items'][0]['spec'].update(state='pending'))
        self.reject('confirmed CP1 affinity')

    def test_duplicate_ip_and_foreign_ip_rejected(self):
        for ip in ('10.244.246.81', '10.244.247.80'):
            with self.subTest(ip=ip):
                self.data = fixture()
                self.edit_json('pods', lambda v: v['items'][0]['status'].update(podIP=ip, podIPs=[{'ip': ip}]))
                self.reject('duplicate|outside')

    def test_nonready_and_deleting_pods_rejected(self):
        self.edit_json('pods', lambda v: v['items'][0]['status'].update(phase='Pending'))
        self.reject('not Ready')
        self.data = fixture()
        self.edit_json('pods', lambda v: v['items'][0]['metadata'].update(deletionTimestamp='now'))
        self.reject('not Ready')

    def test_physical_or_tunnel_route_rejected(self):
        for iface in ('ens160', 'vxlan.calico', 'cali+'):
            with self.subTest(iface=iface):
                self.data = fixture()
                self.edit_json('routes', lambda v: v[0].update(dev=iface))
                self.reject('concrete cali')

    def test_gateway_ecmp_other_table_rejected(self):
        for change in ({'gateway': '10.1.93.103'}, {'nexthops': []}, {'table': 100}):
            with self.subTest(change=change):
                self.data = fixture()
                self.edit_json('routes', lambda v: v[0].update(change))
                self.reject('indirect Pod route')

    def test_reused_veth_or_pod_uid_after_capture_rejected(self):
        self.edit_json('links_after', lambda v: v[0].update(ifindex=100))
        self.reject('membership changed')

    def test_network_policy_change_and_label_change_rejected(self):
        self.edit_json('networkpolicies_after', lambda v: v['items'].append(
            {'metadata': {'namespace': 'test', 'name': 'deny', 'uid': 'np-1'}, 'spec': {'podSelector': {}}}))
        self.reject('network policy changed')
        self.data = fixture()
        self.edit_json('pods_after', lambda v: v['items'][0]['metadata'].update(labels={'new': 'label'}))
        self.reject('membership changed')
        self.data = fixture()
        self.edit_json('pods_after', lambda v: v['items'][0]['metadata'].update(uid='replacement'))
        self.reject('membership changed')

    def test_unmapped_cali_interface_rejected(self):
        self.edit_json('links', lambda v: v.append(dict(v[0], ifname='cali0123456789c', ifindex=12)))
        self.reject('unmapped cali')

    def test_wildcard_xml_generation_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exact cali'):
            p.documents([{'interface': 'cali+'}, {'interface': 'cali*'}])

    def test_inventory_missing_definition_rejected(self):
        self.data['capture']['queries']['runtime/zones']['stdout'] += ' other'
        self.reject('inventory definition missing')

    def test_collection_repeats_inventory_native_and_identity_in_one_capture(self):
        calls = []
        class Backend:
            def __init__(self, _):
                self.cache = type('Cache', (), {'cleanup': lambda _: None})()
            def identity(self):
                pass
            def cp1_state(self):
                return self_test.data['capture']['state']['before']
            def kubectl(self, args):
                return command(['kubectl'] + args)
        def command(argv):
            calls.append(argv)
            result = 'public' if '--get-zones' in argv else ('existing' if '--get-ipsets' in argv else '{}')
            return {'rc': 0, 'stdout': result, 'stderr': ''}
        class Node:
            COMMAND_STATE = {}
            BaseBackend = Backend
        self_test = self
        Node.command = staticmethod(command)
        report = p.collect(Node, lambda: NOW)
        for key in ('runtime/zones', 'permanent/zones', 'runtime/ipsets', 'permanent/ipsets',
                    'native', 'version', 'boot', 'firewalld_config'):
            self.assertIn(key, report['queries'])
            self.assertIn(key + '_after', report['queries'])
        for plane in ('runtime', 'permanent'):
            self.assertIn(plane + '/ipsets_after/existing', report['queries'])
            self.assertIn(plane + '/zones_after/public', report['queries'])
        self.assertEqual(calls.count(['nft', '-j', 'list', 'ruleset']), 2)

    def test_second_sample_failure_preserved_and_plan_rejected(self):
        self.data['capture']['queries']['native_after'].update(rc=124, stderr='COMMAND_TIMEOUT')
        self.reject('incomplete/uncertain')

    def test_legacy_single_sample_does_not_satisfy_new_consistency_check(self):
        del self.data['capture']['queries']['runtime/ipsets_after']
        self.reject('paired inventory absent')

    def test_boot_version_and_config_change_rejected(self):
        for key in ('boot', 'version', 'firewalld_config'):
            with self.subTest(key=key):
                self.data = fixture()
                self.data['capture']['queries'][key + '_after']['stdout'] += ' changed'
                self.reject('changed/missing during capture')

    def test_zone_definition_and_ipset_membership_drift_rejected(self):
        self.data['capture']['queries']['runtime/zones_after/public']['stdout'] += '\n  services: ssh https'
        self.reject('zone/ipset inventory changed')
        self.data = fixture()
        q = self.data['capture']['queries']
        for suffix in ('', '_after'):
            q['runtime/ipsets' + suffix]['stdout'] = 'foreign'
            q['runtime/ipsets' + suffix + '/foreign'] = {'rc': 0, 'stderr': '',
                'stdout': 'foreign\n  entries: 192.0.2.' + ('1' if not suffix else '2')}
        self.reject('zone/ipset inventory changed')

    def test_inventory_addition_removal_duplicate_unlisted_definition_rejected(self):
        for case in ('addition', 'removal', 'duplicate', 'unlisted'):
            with self.subTest(case=case):
                self.data = fixture()
                q = self.data['capture']['queries']
                if case == 'addition':
                    q['runtime/zones_after']['stdout'] += ' other'
                    q['runtime/zones_after/other'] = {'rc': 0, 'stderr': '', 'stdout': 'other'}
                elif case == 'removal':
                    q['runtime/zones_after']['stdout'] = ''
                    del q['runtime/zones_after/public']
                elif case == 'duplicate':
                    q['runtime/zones']['stdout'] += ' public'
                else:
                    q['runtime/zones/hidden'] = {'rc': 0, 'stderr': '', 'stdout': 'hidden'}
                self.reject('inventory changed|public zone absent|unsafe object inventory|unlisted inventory')

    def other_zone(self, interfaces='', sources=''):
        q = self.data['capture']['queries']
        for suffix in ('', '_after'):
            q['runtime/zones' + suffix]['stdout'] += ' other'
            q['runtime/zones' + suffix + '/other'] = {'rc': 0, 'stderr': '',
                'stdout': 'other\n  target: default\n  interfaces: ' + interfaces + '\n  sources: ' + sources}

    def test_exact_prefix_and_all_interface_zone_assignment_rejected(self):
        for pattern in ('cali0123456789a', 'cali+', '+', 'cali0123456789+'):
            with self.subTest(pattern=pattern):
                self.data = fixture()
                self.other_zone(interfaces=pattern)
                self.reject('veth assigned to non-public')

    def test_source_based_classification_requires_explicit_analysis(self):
        for source in ('10.244.246.64/26', 'ipset:trusted', '192.0.2.0/24'):
            with self.subTest(source=source):
                self.data = fixture()
                self.other_zone(sources=source)
                self.reject('source-based zone')

    def test_unrelated_explicit_interface_does_not_claim_candidate(self):
        self.other_zone(interfaces='eth9')
        result = p.prepare(**self.data)
        self.assertEqual(result['status'], 'DRAFT_NOT_EXECUTABLE')
        self.assertFalse(result['consistency']['native_semantics_verified'])

    def test_zone_classification_missing_duplicated_and_wrong_header_rejected(self):
        for raw in ('public\n  sources:', 'other\n  interfaces:\n  sources:',
                    'public\n  interfaces:\n  interfaces: cali+\n  sources:'):
            with self.subTest(raw=raw):
                self.data = fixture()
                for suffix in ('', '_after'):
                    self.data['capture']['queries']['runtime/zones' + suffix + '/public']['stdout'] = raw
                self.reject('fields absent|header differs|duplicate zone field')

    def native(self):
        return {'nftables': [
            {'table': {'family': 'inet', 'name': 'firewalld', 'handle': 1}},
            {'rule': {'family': 'inet', 'table': 'firewalld', 'chain': 'filter_FORWARD', 'handle': 3,
                      'expr': [{'counter': {'packets': 1, 'bytes': 80}}, {'accept': None}]}},
            {'counter': {'family': 'inet', 'table': 'firewalld', 'name': 'named', 'handle': 4,
                         'packets': 10, 'bytes': 800}}]}

    def install_native(self, first, last):
        for key, value in (('native', first), ('native_after', last)):
            self.data['capture']['queries'][key]['stdout'] = json.dumps(value)

    def test_counter_changes_allowed_but_original_raw_hashes_preserved(self):
        first = self.native(); last = copy.deepcopy(first)
        last['nftables'][1]['rule']['expr'][0]['counter'].update(packets=2, bytes=160)
        last['nftables'][2]['counter'].update(packets=11, bytes=880)
        self.install_native(first, last)
        saved = copy.deepcopy(self.data)
        result = p.prepare(**self.data)
        self.assertNotEqual(result['native_before_sha256'], result['native_after_sha256'])
        self.assertEqual(self.data, saved)
        self.assertFalse(result['consistency']['atomic_snapshot'])
        self.assertFalse(result['consistency']['transient_aba_changes_excluded'])

    def test_native_verdict_handle_and_rule_order_change_rejected(self):
        for case in ('verdict', 'handle', 'order'):
            with self.subTest(case=case):
                self.data = fixture()
                first = self.native(); last = copy.deepcopy(first)
                if case == 'verdict':
                    last['nftables'][1]['rule']['expr'][-1] = {'drop': None}
                elif case == 'handle':
                    last['nftables'][1]['rule']['handle'] = 99
                else:
                    last['nftables'].reverse()
                self.install_native(first, last)
                self.reject('native structure changed')

    def test_native_set_elements_timeouts_and_quota_changes_not_hidden(self):
        for key, before, after in [('elem', ['192.0.2.1'], ['192.0.2.2']), ('timeout', 30, 60), ('used', 4, 8)]:
            with self.subTest(key=key):
                self.data = fixture()
                first = self.native()
                first['nftables'].append({'set': {'family': 'inet', 'table': 'firewalld', 'name': 'foreign', key: before}})
                last = copy.deepcopy(first); last['nftables'][-1]['set'][key] = after
                self.install_native(first, last)
                self.reject('native structure changed')

    def test_invalid_counter_statistics_rejected(self):
        for value in (True, -1, '10'):
            with self.subTest(value=value):
                self.data = fixture()
                first = self.native()
                first['nftables'][1]['rule']['expr'][0]['counter']['packets'] = value
                self.install_native(first, first)
                self.reject('invalid counter statistics')

    def test_missing_native_pair_rejected(self):
        del self.data['capture']['queries']['native_after']
        self.reject('paired native observation absent')

    def test_json_duplicates_and_nonfinite_rejected(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                p.parse(raw)

    def test_cli_plan_binds_input_bytes_and_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture, original, out = root / 'capture.json', root / 'original.json', root / 'out.json'
            p.output(capture, self.data['capture']); p.output(original, self.data['original'])
            self.assertEqual(p.load_checked(capture, p.digest(capture.read_bytes())), self.data['capture'])
            with self.assertRaisesRegex(ValueError, 'input size/hash'):
                p.load_checked(capture, '0' * 64)
            p.output(out, {'existing': True})
            with self.assertRaises(FileExistsError):
                p.output(out, {'replace': True})
            self.assertEqual(json.loads(out.read_text()), {'existing': True})


if __name__ == '__main__':
    unittest.main()
