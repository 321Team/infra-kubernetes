"""Fault injection uses an independent firewall-cmd simulator, never a live host."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load('core_under_test', SCRIPTS / 'cni_diag_core.py')
node = load('node_under_test', SCRIPTS / 'cp1-cni-diagnostic-node.py')
controller = load('controller_under_test', SCRIPTS / 'run-cp1-cni-diagnostic.py')


def peers():
    return [{'name': n, 'uid': 'pod-' + n, 'namespace_uid': 'ns-uid', 'node': core.NODE,
             'ip': ip, 'host_network': False, 'pod_ifindex': 2, 'pod_iflink': idx,
             'host_ifindex': idx, 'interface': iface}
            for n, ip, idx, iface in [('a', '10.244.246.80', 21, 'caliaaa'),
                                     ('b', '10.244.246.81', 22, 'calibbb')]]


def baseline():
    public = core.empty_zone()
    public.update(services='dhcpv6-client ssh', forward='yes',
                  **{'rich rules': [core.rule('rule family="ipv4" source address="10.1.93.109/32" '
                                             'port port="6443" protocol="tcp" accept')]})
    permanent = {'zones': {'public': public}, 'policies': {}, 'direct': ''}
    runtime = copy.deepcopy(permanent)
    runtime['zones']['public']['interfaces'] = 'ens160'
    runtime['zones']['public']['rich rules'] += core.six_rules()
    runtime['zones']['public']['rich rules'].sort()
    return {'default': 'public', 'runtime': runtime, 'permanent': permanent}


class Crash(BaseException):
    pass


class Simulator:
    def __init__(self, directory):
        self.live = baseline()
        self.j = {'baseline': baseline(), 'expected': baseline()}
        self.saved = copy.deepcopy(self.j)
        self.directory = directory
        for name, text in core.xml_documents('test-owner', peers()).items():
            (directory / name).write_text(text)
        self.trace = []
        self.crash_at = None
        self.timing = 'after'
        self.closed = False
        self.boot_changed = False

    def snapshot(self):
        return copy.deepcopy(self.live)

    def save(self, journal):
        # Roundtrip exercises actual durable-state JSON normalization and fresh process recovery.
        self.saved = json.loads(json.dumps(journal))

    def guard(self, cleanup=False):
        if self.closed and not cleanup:
            raise ValueError('expired')

    def management(self):
        assert all(r in self.live['runtime']['zones']['public']['rich rules'] for r in core.six_rules())
        assert self.live['runtime']['policies'][core.POLICY]['disable'] == 'yes'

    def verify_peers(self, value, bound):
        assert value == peers()
        assert self.live['permanent']['zones'][core.ZONE]['interfaces'] == ''
        if bound:
            assert self.live['runtime']['zones'][core.ZONE]['interfaces'] == 'caliaaa calibbb'
        assert self.live['runtime']['policies'][core.POLICY]['disable'] == 'yes'

    def verify_cleanup_interface(self, interface, value):
        assert interface in {p['interface'] for p in value}

    def command(self, args, cleanup=False):
        self.guard(cleanup)
        self.trace.append(list(args))
        n = len(self.trace)
        if self.crash_at == n and self.timing == 'before':
            raise Crash()
        plane = 'permanent' if '--permanent' in args else 'runtime'
        state = self.live[plane]
        if '--reload' in args:
            # Model documented daemon behavior independently of Transaction's state transformer.
            self.live['runtime'] = copy.deepcopy(self.live['permanent'])
            self.live['runtime']['zones']['public']['interfaces'] = 'ens160'
        elif any(a.startswith('--new-zone-from-file=') for a in args):
            path = next(a.split('=', 1)[1] for a in args if a.startswith('--new-zone-from-file='))
            obj = ET.parse(path).getroot()
            assert obj.tag == 'zone' and obj.find('forward') is None
            assert not obj.findall('interface') and not obj.findall('source')
            state['zones'][core.ZONE] = {'target': 'default', 'ingress-priority': '0',
                'egress-priority': '0', 'icmp-block-inversion': 'no', 'interfaces': '',
                'sources': '', 'services': '', 'ports': '', 'protocols': '', 'forward': 'no',
                'masquerade': 'no', 'forward-ports': '', 'source-ports': '', 'icmp-blocks': '', 'rich rules': []}
        elif any(a.startswith('--new-policy-from-file=') for a in args):
            path = next(a.split('=', 1)[1] for a in args if a.startswith('--new-policy-from-file='))
            obj = ET.parse(path).getroot()
            rich = []
            for r in obj.findall('rule'):
                rich.append(core.rule('rule family="%s" source address="%s" destination address="%s" '
                                     'port port="%s" protocol="%s" accept' %
                                     (r.attrib['family'], r.find('source').attrib['address'],
                                      r.find('destination').attrib['address'], r.find('port').attrib['port'],
                                      r.find('port').attrib['protocol'])))
            state['policies'][core.POLICY] = {'target': obj.attrib['target'], 'priority': obj.attrib['priority'],
                'disable': 'yes' if obj.find('disable') is not None else 'no',
                'ingress-zones': obj.find('ingress-zone').attrib['name'],
                'egress-zones': obj.find('egress-zone').attrib['name'], 'services': '', 'ports': '',
                'protocols': '', 'masquerade': 'no', 'forward-ports': '', 'source-ports': '',
                'icmp-blocks': '', 'rich rules': sorted(rich)}
        elif any(a.startswith('--delete-policy=') for a in args):
            del state['policies'][core.POLICY]
        elif any(a.startswith('--delete-zone=') for a in args):
            assert core.POLICY not in state['policies']
            del state['zones'][core.ZONE]
        elif '--policy=' + core.POLICY in args:
            state['policies'][core.POLICY]['disable'] = 'yes' if '--add-disable' in args else 'no'
        else:
            zone = next(a.split('=', 1)[1] for a in args if a.startswith('--zone='))
            for a in args:
                if a.startswith('--add-rich-rule='):
                    state['zones'][zone]['rich rules'].append(core.rule(a.split('=', 1)[1]))
                    state['zones'][zone]['rich rules'].sort()
                elif a.startswith('--add-interface='):
                    values = state['zones'][zone]['interfaces'].split() + [a.split('=', 1)[1]]
                    state['zones'][zone]['interfaces'] = ' '.join(sorted(values))
                elif a.startswith('--remove-interface='):
                    values = state['zones'][zone]['interfaces'].split()
                    values.remove(a.split('=', 1)[1])
                    state['zones'][zone]['interfaces'] = ' '.join(values)
        if self.crash_at == n and self.timing == 'after':
            raise Crash()


class ContractTests(unittest.TestCase):
    def test_exact_pair_xml_and_no_zone_allowances(self):
        docs = core.xml_documents('owned', peers())
        policy = ET.fromstring(docs[core.POLICY + '.xml'])
        self.assertIsNotNone(policy.find('disable'))
        self.assertEqual([(r.find('source').attrib['address'], r.find('destination').attrib['address'])
                          for r in policy.findall('rule')], [('10.244.246.80/32', '10.244.246.81/32'),
                                                            ('10.244.246.81/32', '10.244.246.80/32')])
        zone = ET.fromstring(docs[core.ZONE + '.xml'])
        self.assertEqual({e.tag for e in zone}, {'short', 'description'})

    def test_duplicate_or_outside_ips_rejected(self):
        for ip in ('10.244.246.80', '10.244.247.1', 'not-an-ip'):
            p = peers(); p[1]['ip'] = ip
            with self.assertRaises(ValueError):
                core.pair_rules(p)

    def test_veth_uid_route_and_peer_identity(self):
        p = peers()
        links = [{'ifname': v['interface'], 'ifindex': v['host_ifindex'], 'link_index': 2,
                  'linkinfo': {'info_kind': 'veth'}} for v in p]
        routes = [{'dst': v['ip'], 'dev': v['interface']} for v in p]
        core.validate_peers(p, 'ns-uid', links, routes, [])
        for change in ('uid', 'node', 'ifindex', 'route', 'physical', 'existing'):
            pp, ll, rr = copy.deepcopy(p), copy.deepcopy(links), copy.deepcopy(routes)
            existing = []
            if change == 'uid': pp[0]['namespace_uid'] = 'replacement'
            if change == 'node': pp[0]['node'] = 'k8s-control02'
            if change == 'ifindex': ll[0]['ifindex'] += 1
            if change == 'route': rr[0]['dev'] = 'vxlan.calico'
            if change == 'physical': pp[0]['interface'] = 'ens160'
            if change == 'existing': existing = ['caliaaa']
            with self.assertRaises(ValueError, msg=change):
                core.validate_peers(pp, 'ns-uid', ll, rr, existing)

    def test_unrelated_runtime_overlay_and_sources_rejected(self):
        for field, val in [('ports', '9090/tcp'), ('sources', '10.244.0.0/16'), ('interfaces', 'ens160 vxlan.calico')]:
            state = baseline(); state['runtime']['zones']['public'][field] = val
            with self.assertRaises(ValueError): core.validate_reload_baseline(state)

    def test_clock_rollback_does_not_extend_work(self):
        record = {'boot_id': 'boot', 'boot_start': 50, 'wall_end': 2200}
        budget = core.Budget(record, 'boot', wall=lambda: 500, clock=lambda: 1071)
        with self.assertRaises(ValueError): budget.remaining()
        self.assertEqual(budget.remaining(True), 179)

    def test_wall_forward_suspend_and_reboot_fail_closed(self):
        record = {'boot_id': 'boot', 'boot_start': 50, 'wall_end': 2200}
        for boot, wall, clock in [('boot', 2300, 60), ('boot', 1000, 1300), ('new', 1000, 60)]:
            with self.assertRaises(ValueError):
                core.Budget(record, boot, wall=lambda: wall, clock=lambda: clock).remaining()

    def test_parser_native_headers_and_rich_rules(self):
        sample = '''public (default, active)
  target: default
  interfaces: ens160
  rich rules:
\trule family="ipv4" source address="10.1.93.109/32" port port="6443" protocol="tcp" accept

other (disabled)
  disable: yes
  rich rules:
'''
        parsed = core.parse_objects(sample)
        self.assertEqual(set(parsed), {'public', 'other'})
        self.assertEqual(len(parsed['public']['rich rules']), 1)
        with self.assertRaises(ValueError): core.parse_objects('public\n  target: default\n  target: ACCEPT\n')

    def test_default_plan_no_mutation(self):
        with patch.object(controller, 'load', side_effect=AssertionError('must not load controller')):
            with patch('sys.argv', ['run.py']), patch('builtins.print') as out:
                controller.main()
        self.assertEqual(json.loads(out.call_args.args[0])['mode'], 'PLAN_ONLY')

    def test_workload_restricted_and_exact_scope(self):
        objects = node.manifests('fds-cni-test')
        self.assertEqual([o['kind'] for o in objects], ['Namespace', 'Pod', 'Pod', 'Service'])
        for pod in objects[1:3]:
            spec = pod['spec']; c = spec['containers'][0]
            self.assertFalse(spec['automountServiceAccountToken'])
            self.assertFalse(spec['hostNetwork'])
            self.assertEqual(c['securityContext']['capabilities']['drop'], ['ALL'])
            self.assertFalse(c['securityContext']['allowPrivilegeEscalation'])
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertIn('@sha256:', c['image'])

    def test_persistent_recovery_service_and_retry(self):
        docs = node.unit_documents(Path('/var/lib/fds/20260915120000-abcdef012345'), 900)
        service = next(v for k, v in docs.items() if k.endswith('.service'))
        timer = next(v for k, v in docs.items() if k.endswith('.timer'))
        self.assertIn('WantedBy=multi-user.target', service)
        self.assertIn(' recover ', service)
        self.assertIn('OnUnitInactiveSec=30s', timer)
        self.assertIn('OnActiveSec=900s', timer)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.io = Simulator(Path(self.tmp.name))
        self.tx = core.Transaction(self.io, self.io.j)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_lifecycle_always_removes_objects_and_restores_six(self):
        self.tx.prepare(peers(), Path(self.tmp.name))
        self.assertEqual(self.io.live['runtime']['policies'][core.POLICY]['disable'], 'no')
        self.assertEqual(self.io.live['permanent']['policies'][core.POLICY]['disable'], 'yes')
        self.tx.withdraw()
        self.assertEqual(self.io.live, baseline())
        self.assertTrue(self.io.saved['firewall_restored'])

    def test_expiry_allows_only_cleanup_and_original_restoration(self):
        self.tx.prepare(peers(), Path(self.tmp.name))
        self.io.closed = True
        with self.assertRaises(ValueError):
            self.tx.prepare(peers(), Path(self.tmp.name))
        self.tx.withdraw()
        self.assertEqual(self.io.live, baseline())

    def test_foreign_drift_disables_own_policy_without_reload_or_overwrite(self):
        self.tx.prepare(peers(), Path(self.tmp.name))
        self.io.live['runtime']['zones']['public']['ports'] = '9000/tcp'
        before_count = len(self.io.trace)
        with self.assertRaisesRegex(ValueError, 'Firewall drift'): self.tx.withdraw()
        self.assertEqual(self.io.live['runtime']['policies'][core.POLICY]['disable'], 'yes')
        self.assertEqual(self.io.live['runtime']['zones']['public']['ports'], '9000/tcp')
        self.assertEqual(self.io.trace[before_count:], [['--policy=' + core.POLICY, '--add-disable']])

    def test_replaced_policy_not_touched(self):
        self.tx.prepare(peers(), Path(self.tmp.name))
        self.io.live['runtime']['policies'][core.POLICY]['ports'] = '9090/tcp'
        before_count = len(self.io.trace)
        with self.assertRaises(ValueError): self.tx.withdraw()
        self.assertEqual(len(self.io.trace), before_count)

    def test_reboot_loads_disabled_permanent_objects_then_fully_recovers(self):
        self.tx.prepare(peers(), Path(self.tmp.name))
        self.io.live['runtime'] = copy.deepcopy(self.io.live['permanent'])
        self.io.live['runtime']['zones']['public']['interfaces'] = 'ens160'
        self.io.boot_changed = True
        core.Transaction(self.io, copy.deepcopy(self.io.saved)).withdraw()
        self.assertEqual(self.io.live, baseline())

    def test_recovery_idempotent(self):
        self.tx.prepare(peers(), Path(self.tmp.name)); self.tx.withdraw()
        count = len(self.io.trace)
        core.Transaction(self.io, copy.deepcopy(self.io.saved)).withdraw()
        self.assertEqual(len(self.io.trace), count)

    def test_no_controller_ack_never_enables_policy(self):
        def missing(): raise ValueError('No SSH acknowledgement')
        self.io.management = missing
        with self.assertRaises(ValueError): self.tx.prepare(peers(), Path(self.tmp.name))
        self.assertFalse(any('--remove-disable' in a for a in self.io.trace))
        core.Transaction(self.io, copy.deepcopy(self.io.saved)).withdraw()
        self.assertEqual(self.io.live, baseline())

    def test_disappeared_veth_cleanup_does_not_recreate_link(self):
        self.tx.prepare(peers(), Path(self.tmp.name))
        self.io.live['runtime']['zones'][core.ZONE]['interfaces'] = 'calibbb'
        self.io.interface_gone = lambda interface, p: interface == 'caliaaa'
        self.tx.withdraw()
        self.assertEqual(self.io.live, baseline())

    def test_reused_veth_never_automatically_unbound(self):
        self.tx.prepare(peers(), Path(self.tmp.name))
        def reused(*args): raise ValueError('Interface reused')
        self.io.verify_cleanup_interface = reused
        with self.assertRaises(ValueError): self.tx.withdraw()
        self.assertEqual(self.io.live['runtime']['policies'][core.POLICY]['disable'], 'yes')
        self.assertEqual(self.io.live['runtime']['zones'][core.ZONE]['interfaces'], 'caliaaa calibbb')

    def test_recovery_failure_can_retry_without_duplicate_changes(self):
        self.tx.prepare(peers(), Path(self.tmp.name))
        self.io.crash_at = len(self.io.trace) + 4
        with self.assertRaises(Crash): self.tx.withdraw()
        self.io.crash_at = None
        core.Transaction(self.io, copy.deepcopy(self.io.saved)).withdraw()
        self.assertEqual(self.io.live, baseline())


def fault_case(position, timing):
    def test(self):
        self.io.crash_at, self.io.timing = position, timing
        with self.assertRaises(Crash):
            self.tx.prepare(peers(), Path(self.tmp.name))
            self.tx.withdraw()
        self.io.crash_at = None
        self.io.closed = True
        # A new process recovers from persisted state, not the crashed Python object.
        core.Transaction(self.io, copy.deepcopy(self.io.saved)).withdraw()
        self.assertEqual(self.io.live, baseline())
    return test


# 24 actual mutation positions: create/load/restore/bind/enable and full withdrawal.
for position in range(1, 25):
    for timing in ('before', 'after'):
        setattr(TransactionTests, 'test_crash_%02d_%s' % (position, timing), fault_case(position, timing))


def nft_fixture():
    p = peers()
    match = lambda left, right: {'match': {'op': '==', 'left': left, 'right': right}}
    chain = 'filter_FWD_policy_' + core.POLICY
    rules = [{'rule': {'chain': 'filter_FORWARD_POLICIES_pre', 'expr': [
        match({'meta': {'key': 'iifname'}}, {'set': [x['interface'] for x in p]}),
        match({'meta': {'key': 'oifname'}}, {'set': [x['interface'] for x in p]}),
        {'jump': {'target': chain}}]}}]
    for a, b in ((0, 1), (1, 0)):
        rules.append({'rule': {'chain': chain + '_allow', 'expr': [
            match({'payload': {'protocol': 'ip', 'field': 'saddr'}}, p[a]['ip']),
            match({'payload': {'protocol': 'ip', 'field': 'daddr'}}, p[b]['ip']),
            match({'payload': {'protocol': 'tcp', 'field': 'dport'}}, 8080), {'accept': None}]}})
    return {'nftables': rules}


class NativeTests(unittest.TestCase):
    def test_valid_exact_dispatch(self):
        self.assertEqual(core.validate_native_policy(nft_fixture(), peers())['status'], 'STATIC_MATCH')

    def test_physical_or_one_sided_dispatch_rejected(self):
        for bad in ('ens160', None):
            f = nft_fixture()
            if bad:
                f['nftables'][0]['rule']['expr'][0]['match']['right']['set'].append(bad)
            else:
                del f['nftables'][0]['rule']['expr'][1]
            with self.assertRaises(ValueError): core.validate_native_policy(f, peers())

    def test_broad_prefix_and_wrong_port_rejected(self):
        for change in ('prefix', 'port'):
            f = nft_fixture(); expr = f['nftables'][1]['rule']['expr']
            if change == 'prefix': expr[0]['match']['right'] = {'prefix': {'addr': '10.244.246.64', 'len': 26}}
            else: expr[2]['match']['right'] = 8081
            with self.assertRaises(ValueError): core.validate_native_policy(f, peers())

    def test_zone_bypass_accept_in_policy_rejected(self):
        f = nft_fixture()
        f['nftables'].append({'rule': {'chain': 'filter_FWD_policy_' + core.POLICY,
                                     'expr': [{'accept': None}]}})
        with self.assertRaises(ValueError): core.validate_native_policy(f, peers())


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.head = 'a' * 40
        self.hashes = {name: 'b' * 64 for name in controller.FILES}
        review = 'https://github.com/321Team/infra-kubernetes/pull/999#pullrequestreview-123'
        self.decision = {'schema': 'fds.cp1-cni-diagnostic-decision.v1', 'source_commit': self.head,
            'execution_approved': True, 'review_url': review, 'execution_decision_url': review,
            'operator': 'fixture-only', 'backup_reference': 'fixture-only', 'console_reference': 'fixture-only',
            'cp1_hep_transition_not_started': True, 'same_cp1_change_conflict_clear': True,
            'qualification_review_url': review}
        self.qualification = {'schema': 'fds.cp1-cni-qualification.v1', 'stage': 'RUNTIME_ISOLATED',
            'executor_bundle_sha256': controller.fingerprint(self.hashes), 'review_url': review,
            'packages': ['firewalld-2.4.3-4.el10.noarch', 'nftables-1.1.5-6.el10.x86_64',
                         'NetworkManager-1.58.0-1.el10.x86_64', 'iptables-nft-1.8.11-16.el10.x86_64'],
            'cases': {case: {'verdict': 'PASS', 'fresh_direct_flow': True, 'ingress_arrival_observed': True,
                            'packet_capture_sha256': 'c' * 64, 'nft_trace_sha256': 'd' * 64}
                      for case in core.QUALIFICATION_CASES},
            'recovery': {case: 'PASS' for case in ('disconnect', 'killed_after_mutation', 'deadline', 'reboot',
                                                 'cleanup_retry', 'nm_ifcfg_unchanged')},
            'all_owned_objects_absent': True}

    def validate(self):
        controller.execution_inputs(self.decision, self.qualification, self.head, self.hashes)

    def test_complete_contract_parses_but_is_not_runtime_proof(self):
        self.validate()
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                controller.verify_evidence_files(self.qualification, Path(t))

    def test_mock_cannot_substitute_for_runtime_qualification(self):
        self.qualification['stage'] = 'MOCK'
        with self.assertRaises(ValueError): self.validate()

    def test_wrong_reviewed_head_rejected(self):
        self.decision['source_commit'] = 'f' * 40
        with self.assertRaises(ValueError): self.validate()

    def test_changed_executor_bundle_rejected(self):
        self.hashes[controller.FILES[0]] = 'e' * 64
        with self.assertRaises(ValueError): self.validate()

    def test_sender_timeout_without_arrival_is_not_qualification(self):
        self.qualification['cases']['physical_spoof_ingress']['ingress_arrival_observed'] = False
        with self.assertRaises(ValueError): self.validate()

    def test_incomplete_recovery_evidence_rejected(self):
        self.qualification['recovery']['reboot'] = 'NOT_TESTED'
        with self.assertRaises(ValueError): self.validate()

    def test_no_implicit_execution_from_design_approval(self):
        self.decision['execution_approved'] = False
        with self.assertRaises(ValueError): self.validate()


class AdapterTests(unittest.TestCase):
    def test_no_disarm_before_cleanup_confirmation(self):
        io = type('IO', (), {'result': {'cleanup_complete': False}})()
        with self.assertRaises(ValueError): node.disarm(io)

    def test_capture_does_not_change_privileged_trial_pod(self):
        for pod in node.manifests('fds-cni-test')[1:3]:
            self.assertNotIn('NET_RAW', pod['spec']['containers'][0]['securityContext']['capabilities'].get('add', []))

    def test_management_ack_timeout_fails_before_enable(self):
        io = node.Linux.__new__(node.Linux)
        io.result = {'health_before': {'ok': True}}
        io.health = lambda: {'ok': True}
        io.cleanup_mode = False
        io.save = lambda d: None
        io.timeout = lambda limit: limit
        with patch.object(node.time, 'monotonic', side_effect=[0, 100]):
            with self.assertRaisesRegex(ValueError, 'acknowledgement'):
                io.management()

    def test_fresh_port_checks_kernel_before_probing_and_never_flushes(self):
        io = node.Linux.__new__(node.Linux)
        calls = []
        def cmd(args):
            calls.append(args)
            return 'existing-flow' if len(calls) == 1 else ''
        io.cmd = cmd
        with patch.object(node.secrets, 'randbelow', side_effect=[1, 2]):
            self.assertEqual(io.fresh_port('10.244.246.80', '10.244.246.81', 8080), 40002)
        self.assertTrue(all(c[1] == '-L' for c in calls))
        self.assertEqual(calls[-1][-3:], ['40002', '--dport', '8080'])


if __name__ == '__main__':
    unittest.main()
