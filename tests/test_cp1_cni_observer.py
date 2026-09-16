"""Whole-state observer regressions using explicit firewalld/nft query fixtures."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_cp1_cni_membership import m, lease, member, BOOT, setup, observed
import cp1_cni_observer as o
from test_cp1_cni_guard import unit_fixture


def snapshot(record, activation, units):
    return o.snapshot_from_records(record, activation, units,
                                   {'boot_id': BOOT, 'wall': 1001, 'boottime': 111, 'monotonic': 110})


def native(mode='ACTIVE', values=None):
    result = {'nftables': [{'table': {'family': 'inet', 'name': 'firewalld', 'handle': 1}},
                          {'chain': {'family': 'inet', 'table': 'firewalld', 'name': 'filter_FORWARD_POLICIES', 'handle': 2}},
                          {'table': {'family': 'ip', 'name': 'calico', 'handle': 3}}]}
    if mode == 'BASELINE': return result
    elements = []
    for value in values or []:
        address, interface = value.split(',')
        elements.append({'concat': [{'prefix': {'addr': address.split('/')[0], 'len': 32}}, interface]})
    result['nftables'].append({'set': {'family': 'inet', 'table': 'firewalld', 'name': o.NAME,
                                      'type': ['ipv4_addr', 'ifname'], 'flags': ['interval'], 'handle': 4, 'elem': elements}})
    if mode == 'DISABLED': return result
    for i, name in enumerate([o.CHAIN, *o.SUBCHAINS]):
        result['nftables'].append({'chain': {'family': 'inet', 'table': 'firewalld', 'name': name, 'handle': 10+i}})
    def rule(chain, expr):
        return {'rule': {'family': 'inet', 'table': 'firewalld', 'chain': chain, 'expr': expr}}
    for name in o.SUBCHAINS:
        result['nftables'].append(rule(o.CHAIN, [{'jump': {'target': name}}]))
    matches = [{'match': {'left': {'concat': [{'payload': {'protocol': 'ip', 'field': addr}},
                                            {'meta': {'key': interface}}]}, 'op': '==', 'right': '@' + o.NAME}}
               for addr, interface in (('saddr', 'iifname'), ('daddr', 'oifname'))]
    result['nftables'].append(rule(o.CHAIN + '_allow', matches + [{'accept': None}]))
    result['nftables'].append(rule('filter_FORWARD_POLICIES', [{'jump': {'target': o.CHAIN}}]))
    return result


def policy(disabled):
    return {'target': 'CONTINUE', 'priority': '-1', 'ingress-zones': 'public', 'egress-zones': 'public',
            'disable': 'yes' if disabled else 'no', 'rich rules': [o.RICH_RULE], 'masquerade': 'no',
            **{k: '' for k in ('services', 'ports', 'protocols', 'icmp-blocks', 'forward-ports', 'source-ports')}}


def object_text(values):
    text = []
    for name, fields in values.items():
        text.append(name)
        for key, value in fields.items():
            if key != 'rich rules': text.append('  ' + key + ': ' + value)
        if 'rich rules' in fields:
            text.append('  rich rules:')
            text.extend('    ' + rule for rule in fields['rich rules'])
    return '\n'.join(text)


def fixture(mode='ACTIVE', values=None):
    l = lease(); values = [] if values is None else values
    host = {'default_zone': 'public', 'management_zone': 'public', 'management_connection': BOOT}
    inventory = {}
    q = {}
    for plane in ('runtime', 'permanent'):
        disabled = plane == 'permanent' or mode == 'DISABLED'
        ipset = {'type': 'hash:net,iface', 'options': 'family=inet', 'entries':
                 ' '.join(values) if plane == 'runtime' else ''}
        zones = {'public': {'target': 'default', 'interfaces': 'ens160', 'sources': '', 'rich rules': []}}
        for suffix in ('', '_after'):
            q[plane + '/zones' + suffix] = object_text(zones)
            q[plane + '/policies' + suffix] = object_text({o.NAME: policy(disabled)})
            q[plane + '/ipsets' + suffix] = o.NAME
            q[plane + '/ipsets/' + o.NAME + suffix] = object_text({o.NAME: ipset})
            q[plane + '/direct' + suffix] = ''
            for kind in ('policy', 'ipset'):
                q[plane + '/' + kind + '-description' + suffix] = l['run_id']
        neutral = dict(ipset, entries='', **{'rich rules': []})
        inventory[plane] = {'zones': zones, 'policies': {o.NAME: policy(True)},
                            'ipsets': {o.NAME: neutral}, 'direct': ''}
    for suffix in ('', '_after'):
        q.update({'native' + suffix: json.dumps(native(mode, values)), 'host' + suffix: json.dumps(host),
                  'protection' + suffix: json.dumps({'calico_image': 'pinned', 'namespace_policies': []}),
                  'version' + suffix: '2.4.3', 'state' + suffix: 'running', 'selinux' + suffix: 'Enforcing',
                  'configuration' + suffix: 'FirewallBackend=nftables\n', 'boot' + suffix: BOOT})
    fixed = {'inventory': inventory, 'native': o.native_view(native(mode, []), mode)['fixed'],
             'host': host, 'protection': {'calico_image': 'pinned', 'namespace_policies': []},
             'configuration': 'FirewallBackend=nftables', 'version': '2.4.3'}
    l['fixed_state_sha256'] = m.fingerprint(fixed)
    record = {'schema': 'fds.cp1-cni-state-capture.v1', 'complete': True, 'errors': [], 'boot_id': BOOT,
              'wall': 1000, 'ended_wall': 1001, 'boottime': 110, 'ended_boottime': 111,
              'queries': {key: {'rc': 0, 'stdout': value, 'stderr': '', 'stdout_truncated': False,
                                'stderr_truncated': False} for key, value in q.items()}}
    return record, {'lease': l, 'fixed': fixed}, unit_fixture(l)


class NativeTests(unittest.TestCase):
    def test_active_disabled_and_empty_set(self):
        pair = m.entry(member())
        for mode in ('ACTIVE', 'DISABLED'):
            for values in ([], [pair]):
                result = o.native_view(native(mode, values), mode)
                self.assertEqual(result['entries'], values)
                self.assertEqual(result['fixed'], o.native_view(native(mode), mode)['fixed'])

    def test_missing_destination_wrong_hook_wide_prefix_and_foreign_reference_rejected(self):
        def allow(raw): return next(x['rule'] for x in raw['nftables'] if x.get('rule', {}).get('chain') == o.CHAIN + '_allow')
        edits = [lambda r: allow(r)['expr'].pop(1),
                 lambda r: r['nftables'][-1]['rule'].update(chain='filter_INPUT_POLICIES'),
                 lambda r: r['nftables'][3]['set']['elem'][0]['concat'][0]['prefix'].update(len=26),
                 lambda r: r['nftables'].append({'rule': {'family': 'ip', 'table': 'other', 'chain': 'unsafe',
                        'expr': [{'jump': {'target': o.CHAIN}}]}}),
                 lambda r: allow(r)['expr'].append({'accept': None}),
                 lambda r: allow(r)['expr'].append({'counter': {'packets': 0, 'bytes': 0}, 'accept': None})]
        for edit in edits:
            raw = native(values=[m.entry(member())]); edit(raw)
            with self.subTest(edit=edit), self.assertRaises(ValueError): o.native_view(raw, 'ACTIVE')

    def test_disabled_policy_must_not_have_compiled_accept(self):
        with self.assertRaisesRegex(ValueError, 'still compiled'):
            o.native_view(native(), 'DISABLED')

    def test_counter_changes_normalized_but_foreign_elements_and_handles_kept(self):
        raw = native()
        raw['nftables'].insert(0, {'rule': {'family': 'ip', 'table': 'other', 'chain': 'x', 'handle': 100,
                                          'expr': [{'counter': {'packets': 2, 'bytes': 3}}, {'drop': None}]}})
        old = o.native_view(raw, 'ACTIVE')['fixed']
        raw['nftables'][0]['rule']['expr'][0]['counter']['packets'] += 9
        self.assertEqual(old, o.native_view(raw, 'ACTIVE')['fixed'])
        raw['nftables'][0]['rule']['handle'] += 1
        self.assertNotEqual(old, o.native_view(raw, 'ACTIVE')['fixed'])


class ObserverTests(unittest.TestCase):
    def test_snapshot_derived_from_whole_state_queries(self):
        record, activation, guard = fixture(values=[m.entry(member())])
        live = snapshot(record, activation, guard)
        self.assertEqual(live['native_entries'], [m.entry(member())])
        self.assertTrue(live['owned']); self.assertTrue(live['policy_enabled'])
        self.assertEqual(m.snapshot(live, activation['lease']), live['native_entries'])

    def test_validated_disable_changes_only_policy_variant(self):
        active, activation, guard = fixture()
        disabled, _, _ = fixture('DISABLED')
        a = snapshot(active, activation, guard)
        b = snapshot(disabled, activation, guard)
        self.assertEqual(a['fixed_state_sha256'], b['fixed_state_sha256'])
        self.assertFalse(b['policy_enabled'])

    def test_description_fake_boolean_and_permanent_membership_cannot_prove_ownership(self):
        for key, value in (('permanent/policy-description', 'foreign'),
                           ('permanent/ipsets/' + o.NAME, object_text({o.NAME: {'type': 'hash:net,iface',
                               'options': 'family=inet', 'entries': m.entry(member())}}))):
            record, activation, guard = fixture(); record.update(owned=True, guard_armed=True)
            record['queries'][key]['stdout'] = value
            with self.subTest(key=key), self.assertRaises(ValueError): snapshot(record, activation, guard)

    def test_native_daemon_membership_and_paired_sample_drift_rejected(self):
        for mode in ('native_only', 'paired'):
            record, activation, guard = fixture()
            record['queries']['native' + ('_after' if mode == 'paired' else '')]['stdout'] = json.dumps(native(values=[m.entry(member())]))
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'native/daemon'):
                snapshot(record, activation, guard)

    def test_environment_warning_query_failure_and_slow_capture_rejected(self):
        edits = [lambda r: r['queries']['version_after'].update(stdout='other'),
                 lambda r: r['queries']['native'].update(stderr='warning'),
                 lambda r: r['queries']['runtime/zones'].update(stdout_truncated=True),
                 lambda r: r.update(ended_boottime=140), lambda r: r.update(errors=['query failed'])]
        for edit in edits:
            record, activation, guard = fixture(); edit(record)
            with self.subTest(edit=edit), self.assertRaises(ValueError): snapshot(record, activation, guard)

    def test_calico_rule_churn_is_explicitly_blocked_until_delta_model_exists(self):
        record, activation, guard = fixture()
        raw = json.loads(record['queries']['native']['stdout'])
        raw['nftables'].append({'chain': {'family': 'ip', 'table': 'calico', 'name': 'new-endpoint-chain'}})
        record['queries']['native']['stdout'] = json.dumps(raw)
        with self.assertRaisesRegex(ValueError, 'whole fixed-state drift'):
            snapshot(record, activation, guard)

    def test_foreign_policy_or_protection_drift_not_hidden(self):
        for key, value in (('protection', '{"calico_image":"changed","namespace_policies":[]}'),
                           ('runtime/direct', 'ipv4 filter INPUT 0 -j ACCEPT')):
            record, activation, guard = fixture(); record['queries'][key]['stdout'] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'whole fixed-state drift'):
                snapshot(record, activation, guard)

    def test_public_classification_ambiguity_is_not_ignored(self):
        record, activation, guard = fixture()
        value = o.objects(record['queries']['runtime/zones']['stdout'])
        value['trusted'] = {'interfaces': 'cali+', 'sources': '', 'rich rules': []}
        record['queries']['runtime/zones']['stdout'] = object_text(value)
        with self.assertRaisesRegex(ValueError, 'non-public'):
            snapshot(record, activation, guard)

    def test_collector_uses_paired_local_read_commands_and_concrete_protection(self):
        record, _, _ = fixture(); calls = []; seen = {}
        class Cache:
            def cleanup(self): pass
        class Backend:
            def __init__(self, hostname): self.cache = Cache()
            def identity(self): calls.append(['identity'])
        class Node:
            COMMAND_STATE = {}
            BaseBackend = Backend
        def command(argv, timeout):
            self.assertLessEqual(timeout, 5); calls.append(argv)
            if '--get-description' in argv:
                self.assertIn('--permanent', argv, 'official CLI allows description only in Permanent')
            joined = ' '.join(argv); count = seen.get(joined, 0); seen[joined] = count+1
            suffix = '_after' if count else ''
            if argv[0] == 'nft': key = 'native'
            elif argv[0] == 'getenforce': key = 'selinux'
            elif argv[0] == 'nmcli':
                return dict(record['queries']['boot'], stdout=BOOT)
            elif argv[0] == 'cat': key = 'configuration' if argv[1].endswith('.conf') else 'boot'
            elif '--version' in argv: key = 'version'
            elif '--state' in argv: key = 'state'
            elif '--get-default-zone' in argv or '--get-zone-of-interface=ens160' in argv:
                return dict(record['queries']['boot'], stdout='public')
            else:
                prefix = 'permanent/' if '--permanent' in argv else 'runtime/'
                if '--list-all-zones' in argv: key = prefix + 'zones'
                elif '--list-all-policies' in argv: key = prefix + 'policies'
                elif '--get-ipsets' in argv: key = prefix + 'ipsets'
                elif '--direct' in argv: key = prefix + 'direct'
                elif '--get-description' in argv:
                    key = prefix + ('policy' if any(a.startswith('--policy=') for a in argv) else 'ipset') + '-description'
                else: key = prefix + 'ipsets/' + o.NAME
            return copy.deepcopy(record['queries'][key + suffix])
        Node.command = staticmethod(command)
        compatibility_calls = []
        def compatibility_command(node, argv, timeout):
            compatibility_calls.append(argv)
            self.assertLessEqual(timeout, 5)
            return {'rc': 0, 'stdout': 'read-only query fixture', 'stderr': ''}
        with patch.object(o, 'compatibility_command', side_effect=compatibility_command):
            captured = o.collect(Node, lambda backend: {'calico_image': 'pinned', 'namespace_policies': []},
                                 compatibility=True)
        self.assertTrue(captured['complete'], captured['errors'])
        self.assertIn('native_after', captured['queries'])
        for key in ('iptables_save', 'ip6tables_save', 'ipsets'):
            self.assertIn(key, captured['queries']); self.assertIn(key + '_after', captured['queries'])
        self.assertEqual(compatibility_calls.count(['iptables-save', '-c']), 2)
        self.assertEqual(compatibility_calls.count(['ip6tables-save', '-c']), 2)
        self.assertEqual(compatibility_calls.count(['ipset', 'save']), 2)
        self.assertTrue(all('reload' not in a and '--add-disable' not in a for a in calls))

    def test_unit_query_failure_preserves_capture_and_cannot_reuse_prior_success(self):
        record, activation, _ = fixture()
        node = SimpleNamespace(__file__=str(Path(o.__file__).parent / 'node-firewall.py'))
        observer = o.LocalObserver(node, activation, lambda p: b'', lambda p: '', lambda: {})
        observer.evidence = {'prior_success': True}
        with patch.object(o, 'collect', return_value=record), \
             patch.object(o, 'query_units', side_effect=OSError('unit read failed')):
            with self.assertRaises(OSError): observer()
        self.assertEqual(observer.evidence['capture'], record)
        self.assertNotIn('prior_success', observer.evidence)
        self.assertFalse(observer.evidence['verification_error']['authorizes_apply'])


if __name__ == '__main__': unittest.main()
