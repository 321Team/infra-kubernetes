"""Negative coverage cases for information opaque in nft JSON."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cp1_cni_dataplane as d
import cp1_cni_observer as o


def fixture():
    raw = {'nftables': [
        {'table': {'family': 'ip', 'name': 'filter', 'handle': 1}},
        {'table': {'family': 'ip6', 'name': 'filter', 'handle': 2}},
        {'chain': {'family': 'ip', 'table': 'filter', 'name': 'FORWARD', 'handle': 3, 'policy': 'accept'}},
        {'chain': {'family': 'ip6', 'table': 'filter', 'name': 'FORWARD', 'handle': 4, 'policy': 'accept'}},
        {'rule': {'family': 'ip', 'table': 'filter', 'chain': 'FORWARD', 'handle': 5, 'expr': [
            {'xt': {'type': 'match', 'name': 'comment'}}, {'xt': {'type': 'match', 'name': 'set'}},
            {'counter': {'packets': 100, 'bytes': 200}}, {'accept': None}]}}]}
    v4 = '*filter\n:FORWARD ACCEPT [11:22]\n[33:44] -A FORWARD -m comment --comment "literal [123:456]" -m set --match-set cali40pool src -j ACCEPT\nCOMMIT\n'
    v6 = '*filter\n:FORWARD ACCEPT [0:0]\nCOMMIT\n'
    sets = 'create cali40pool hash:net family inet hashsize 1024 maxelem 1048576\nadd cali40pool 10.244.0.0/16\n'
    return raw, v4, v6, sets


class CoverageTests(unittest.TestCase):
    def test_hidden_extension_payload_and_ipset_entries_are_part_of_digest(self):
        raw, v4, v6, sets = fixture(); base = d.covered(raw, v4, v6, sets)
        for changed4, changedsets in [(v4.replace(' src ', ' dst '), sets),
                                      (v4, sets.replace('10.244.0.0/16', '0.0.0.0/0')),
                                      (v4.replace('[123:456]', '[999:999]'), sets)]:
            self.assertNotEqual(base, d.covered(raw, changed4, v6, changedsets))

    def test_only_real_counters_ignored(self):
        raw, v4, v6, sets = fixture(); before = d.covered(raw, v4, v6, sets)
        raw['nftables'][-1]['rule']['expr'][2]['counter'].update(packets=200, bytes=400)
        self.assertEqual(before, d.covered(raw, v4.replace('[11:22]', '[80:90]').replace('[33:44]', '[55:66]'), v6, sets))
        raw['nftables'][-1]['rule']['handle'] += 1
        self.assertNotEqual(before, d.covered(raw, v4, v6, sets))

    def test_missing_set_unknown_options_and_partial_save_rejected(self):
        raw, v4, v6, sets = fixture()
        for broken in ('', sets.replace('family inet', 'family inet timeout 90'),
                       sets + 'add cali40pool 10.244.0.0/16\n', sets.replace('hash:net', 'list:set')):
            with self.subTest(broken=broken), self.assertRaises(ValueError): d.covered(raw, v4, v6, broken)
        for broken in (v4.replace('COMMIT', ''), v4 + '-F\n', v4.replace(':FORWARD ACCEPT', ':FORWARD DROP')):
            with self.subTest(broken=broken), self.assertRaises(ValueError): d.covered(raw, broken, v6, sets)

    def test_missing_native_or_saved_rule_cannot_cover_xt(self):
        raw, v4, v6, sets = fixture()
        with self.assertRaisesRegex(ValueError, 'rule count'):
            d.covered(raw, '*filter\n:FORWARD ACCEPT [0:0]\nCOMMIT\n', v6, sets)
        raw['nftables'].pop()
        with self.assertRaisesRegex(ValueError, 'rule count'): d.covered(raw, v4, v6, sets)

    def test_extension_order_count_and_unknown_extension_fail(self):
        raw, v4, v6, sets = fixture()
        for first in ({'type': 'match', 'name': 'unknown'}, {'type': 'target', 'name': 'DNAT'},
                      {'type': 'match', 'name': 'set'}):
            bad = copy.deepcopy(raw); bad['nftables'][-1]['rule']['expr'][0]['xt'] = first
            with self.subTest(first=first), self.assertRaises(ValueError): d.covered(bad, v4, v6, sets)

    def test_xt_in_unsaved_family_or_table_rejected(self):
        raw, v4, v6, sets = fixture()
        raw['nftables'][2]['chain']['table'] = 'foreign'
        raw['nftables'][-1]['rule']['table'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'opaque xt outside'): d.covered(raw, v4, v6, sets)

    def test_synthesized_empty_accept_builtin_only(self):
        raw, v4, v6, sets = fixture()
        d.covered(raw, v4.replace(':FORWARD', ':INPUT ACCEPT [0:0]\n:FORWARD'), v6, sets)
        for extra in (':INPUT DROP [0:0]', ':foreign ACCEPT [0:0]'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                d.covered(raw, v4.replace(':FORWARD', extra + '\n:FORWARD'), v6, sets)

    def test_observer_requires_full_streams_and_retains_hidden_payload(self):
        raw, v4, v6, sets = fixture()
        with self.assertRaisesRegex(ValueError, 'XT_COMPAT_COVERAGE_REQUIRED'): o.native_view(raw, 'BASELINE')
        compat = dict(iptables_save=v4, ip6tables_save=v6, ipsets=sets)
        result = o.native_view(raw, 'BASELINE', compatibility=compat)
        self.assertIn('kernel_ipsets', result['fixed']); self.assertIn('xtables', result['fixed'])
        compat['ipsets'] = sets.replace('10.244.0.0/16', '0.0.0.0/0')
        self.assertNotEqual(result, o.native_view(raw, 'BASELINE', compatibility=compat))
        compat.pop('ip6tables_save')
        with self.assertRaises(ValueError): o.native_view(raw, 'BASELINE', compatibility=compat)

    def test_paired_hidden_drift_rejected_even_when_native_unchanged(self):
        raw, v4, v6, sets = fixture()
        q = {k + s: {'rc': 0, 'stderr': '', 'stdout': v} for s in ('', '_after') for k, v in
             [('native_json', json.dumps(raw)), ('iptables_save', v4), ('ip6tables_save', v6), ('ipsets', sets)]}
        d.paired(q)
        q['ipsets_after']['stdout'] = sets.replace('10.244.0.0/16', '0.0.0.0/0')
        with self.assertRaisesRegex(ValueError, 'changed during paired'): d.paired(q)

    def test_duplicate_json_and_counter_boolean_rejected(self):
        with self.assertRaises(ValueError): d.parse_json('{"nftables":[],"nftables":[]}')
        with self.assertRaises(ValueError): d.parse_json('{"x":NaN}')
        raw, v4, v6, sets = fixture(); raw['nftables'][-1]['rule']['expr'][2]['counter']['packets'] = True
        with self.assertRaises(ValueError): d.covered(raw, v4, v6, sets)

    def test_warning_error_and_truncation_not_silently_accepted(self):
        for changed in ({'rc': True}, {'stderr': 'warning'}, {'stdout_truncated': True}, {'stderr_truncated': None}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                d.output(dict({'rc': 0, 'stdout': 'ok', 'stderr': ''}, **changed))

    def test_whole_observer_binds_compatibility_and_blocks_later_hidden_drift(self):
        from test_cp1_cni_observer import fixture as observed, snapshot
        from cp1_cni_membership import fingerprint
        record, activation, guard = observed()
        raw, v4, v6, sets = fixture()
        compat = dict(iptables_save=v4, ip6tables_save=v6, ipsets=sets)
        merged = json.loads(record['queries']['native']['stdout'])
        merged['nftables'].extend(raw['nftables'])
        for suffix in ('', '_after'):
            record['queries']['native' + suffix]['stdout'] = json.dumps(merged)
            for key, value in compat.items():
                record['queries'][key + suffix] = {'rc': 0, 'stdout': value, 'stderr': ''}
        activation['fixed']['native'] = o.native_view(merged, 'ACTIVE', compatibility=compat)['fixed']
        activation['lease']['fixed_state_sha256'] = fingerprint(activation['fixed'])
        self.assertTrue(snapshot(record, activation, guard)['owned'])
        for suffix in ('', '_after'):
            record['queries']['iptables_save' + suffix]['stdout'] = v4.replace(' src ', ' dst ')
        with self.assertRaisesRegex(o.StateMismatch, 'whole fixed-state drift'):
            snapshot(record, activation, guard)

    def test_local_observer_and_withdrawal_request_compatibility_streams(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from test_cp1_cni_observer import fixture as observed
        record, activation, guard = observed()
        node = SimpleNamespace(__file__=str(Path(o.__file__).parent / 'node-firewall.py'))
        observer = o.LocalObserver(node, activation, lambda p: b'', lambda p: '', lambda: {})
        with patch.object(o, 'collect', return_value=record) as collect, \
             patch.object(o, 'query_units', return_value=guard), \
             patch.object(o, 'snapshot_from_records', return_value={}) as verify:
            observer()
            collect.assert_called_with(node, o.protection, compatibility=True, delta_api=False)
            observer.withdrawal()
            collect.assert_called_with(node, o.protection, compatibility=True, delta_api=False)
            self.assertTrue(verify.call_args.kwargs['withdrawal'])


if __name__ == '__main__': unittest.main()
