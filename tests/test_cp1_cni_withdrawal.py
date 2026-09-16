"""Owned recovery and real pinned-helper adapter contract regressions."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_cp1_cni_observer import fixture, native, object_text, o
from test_cp1_cni_membership import m, BOOT, member, MemoryJournal
from cp1_cni_membership_io import FirewalldMembershipIO
import cp1_cni_withdrawal as w


def owned_fixture(mode='ACTIVE', values=None):
    record, activation, units = fixture(mode, values)
    q = record['queries']
    result = dict(record, schema='fds.cp1-cni-owned-withdrawal.v1', queries={})
    for suffix in ('', '_after'):
        for key in ('boot', 'version', 'state'):
            result['queries'][key + suffix] = q[key + suffix]
        for plane in ('runtime', 'permanent'):
            for kind, source in (('policy', 'policies'), ('ipset', 'ipsets/' + o.NAME)):
                result['queries'][plane + '/' + kind + suffix] = q[plane + '/' + source + suffix]
                if plane == 'permanent':
                    result['queries'][kind + '-description' + suffix] = q['permanent/' + kind + '-description' + suffix]
        raw = native(mode, values)
        raw['nftables'] = [item for item in raw['nftables'] if next(iter(item.values())).get('family') == 'inet']
        result['queries']['native' + suffix] = dict(q['native' + suffix], stdout=json.dumps(raw))
    return result, activation, units


class WithdrawalTests(unittest.TestCase):
    def clock(self, **changes):
        return dict({'boot_id': BOOT, 'wall': 1001, 'boottime': 111, 'monotonic': 110}, **changes)

    def test_expired_lease_stopped_guard_and_foreign_drift_allow_owned_disable_only(self):
        for fault in ('expired', 'guard', 'foreign', 'new_boot', 'api_down'):
            record, activation, _ = owned_fixture()
            clock = self.clock()
            if fault == 'expired':
                activation['lease'].update(wall_start=0, wall_end=500, boot_start=0, boot_end=500)
            if fault == 'new_boot':
                activation['lease']['boot_id'] = 'a'*8 + '-1234-1234-1234-123456789abc'
            calls = []
            def normal(): raise ValueError(fault)
            def narrow(): return w.owned_snapshot(record, activation, clock)
            def runner(argv, **kwargs):
                calls.append(argv)
                after, _, _ = owned_fixture('DISABLED')
                record['queries'] = after['queries']
                return {'rc': 0}
            io = FirewalldMembershipIO(activation['lease'], normal, runner, lambda: BOOT,
                                      recovery_observer=narrow, withdrawal_observer=normal)
            with self.subTest(fault=fault):
                io.disable()
                self.assertEqual(calls, [['firewall-cmd', '--policy=' + o.NAME, '--add-disable']])
                proof = narrow()
                self.assertFalse(proof['full_recovery_complete'])
                self.assertIsNone(proof['fixed_state_sha256'])
                with self.assertRaises(ValueError): m.snapshot(proof, activation['lease'])
                with self.assertRaises(ValueError): io.snapshot()

    def test_foreign_description_and_broader_owned_allow_never_mutated(self):
        for fault in ('description', 'rule', 'native', 'permanent'):
            record, activation, _ = owned_fixture()
            if fault == 'description': record['queries']['policy-description']['stdout'] = 'foreign'
            elif fault == 'rule':
                record['queries']['runtime/policy']['stdout'] = record['queries']['runtime/policy']['stdout'].replace(o.RICH_RULE, 'rule accept')
            elif fault == 'native':
                raw = json.loads(record['queries']['native']['stdout'])
                next(x['rule'] for x in raw['nftables'] if x.get('rule', {}).get('chain') == o.CHAIN + '_allow')['expr'].pop(0)
                record['queries']['native']['stdout'] = json.dumps(raw)
            else:
                record['queries']['permanent/policy']['stdout'] = record['queries']['permanent/policy']['stdout'].replace('disable: yes', 'disable: no')
            calls = []
            io = FirewalldMembershipIO(activation['lease'], lambda: None, lambda *a, **k: calls.append(a), lambda: BOOT,
                                      recovery_observer=lambda: w.owned_snapshot(record, activation, self.clock()))
            with self.subTest(fault=fault), self.assertRaises(ValueError): io.disable()
            self.assertEqual(calls, [])

    def test_whole_disabled_snapshot_after_expiry_never_proves_guard_or_new_grant(self):
        record, activation, _ = fixture('DISABLED')
        activation['lease'].update(wall_start=0, wall_end=500, boot_start=0, boot_end=500)
        live = o.snapshot_from_records(record, activation, None, self.clock(), withdrawal=True)
        self.assertEqual(m.snapshot(live, activation['lease'], require_guard=False), [])
        with self.assertRaises(ValueError): m.snapshot(live, activation['lease'])
        active, _, _ = fixture('ACTIVE')
        with self.assertRaisesRegex(ValueError, 'disabled policy'):
            o.snapshot_from_records(active, activation, None, self.clock(), withdrawal=True)

    def test_timeout_requires_disabled_native_proof_and_no_second_mutation(self):
        record, activation, _ = owned_fixture()
        calls = []
        def runner(*args, **kwargs):
            calls.append(args)
            raise TimeoutError('no result')
        io = FirewalldMembershipIO(activation['lease'], lambda: None, runner, lambda: BOOT,
                                  recovery_observer=lambda: w.owned_snapshot(record, activation, self.clock()))
        with self.assertRaisesRegex(ValueError, 'not observed'): io.disable()
        self.assertEqual(len(calls), 1)

    def test_full_fixed_drift_and_boot_change_still_block_membership_removal(self):
        for fault in ('foreign', 'boot'):
            record, activation, _ = fixture('DISABLED')
            if fault == 'foreign': record['queries']['runtime/direct']['stdout'] = 'changed'
            else: activation['lease']['boot_id'] = 'a'*8 + '-1234-1234-1234-123456789abc'
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                o.snapshot_from_records(record, activation, None, self.clock(), withdrawal=True)

    def test_manager_adapter_and_record_observers_withdraw_after_guard_loss(self):
        record, activation, units = fixture()
        clock = self.clock()
        state = m.initial_state(activation['lease'], o.snapshot_from_records(record, activation, units, clock), clock)
        mode = ['ACTIVE']
        calls = []
        def narrow():
            capture, _, _ = owned_fixture(mode[0])
            return w.owned_snapshot(capture, activation, clock)
        def whole():
            capture, _, _ = fixture(mode[0])
            return o.snapshot_from_records(capture, activation, None, clock, withdrawal=True)
        def broken_guard(): raise ValueError('guard no longer armed')
        def runner(argv, **kwargs):
            calls.append(argv); mode[0] = 'DISABLED'
            return {'rc': 0}
        io = FirewalldMembershipIO(activation['lease'], broken_guard, runner, lambda: BOOT,
            wall=lambda: clock['wall'], boottime=lambda: clock['boottime'],
            recovery_observer=narrow, withdrawal_observer=whole)
        journal = MemoryJournal()
        manager = m.Manager(state, io, journal)
        result = manager.withdraw('guard lost')
        self.assertEqual(result['phase'], 'MEMBERSHIP_WITHDRAWN_REVIEW_REQUIRED')
        self.assertTrue(result['membership_withdrawn'])
        self.assertFalse(result['full_recovery_complete'])
        self.assertEqual(calls, [['firewall-cmd', '--policy=' + o.NAME, '--add-disable']])

    def test_owned_collector_avoids_api_units_and_global_ruleset_and_restores_budget(self):
        captured, _, _ = owned_fixture()
        calls = []; counts = {}
        class Node:
            COMMAND_STATE = {'deadline': 123}
            @staticmethod
            def command(argv, timeout):
                calls.append(argv)
                self.assertLessEqual(timeout, 3)
                key = tuple(argv); count = counts.get(key, 0); counts[key] = count+1
                suffix = '_after' if count else ''
                if argv[0] == 'cat': name = 'boot'
                elif argv[0] == 'nft': name = 'native'
                elif '--version' in argv: name = 'version'
                elif '--state' in argv: name = 'state'
                elif '--get-description' in argv:
                    self.assertIn('--permanent', argv)
                    name = ('policy' if argv[-2].startswith('--policy=') else 'ipset') + '-description'
                else:
                    name = ('permanent/' if '--permanent' in argv else 'runtime/') + ('policy' if argv[-1].startswith('--info-policy=') else 'ipset')
                return copy.deepcopy(captured['queries'][name + suffix])
        result = w.collect_owned(Node)
        self.assertTrue(result['complete'], result['errors'])
        self.assertEqual(Node.COMMAND_STATE, {'deadline': 123})
        self.assertIn(['nft', '-j', 'list', 'table', 'inet', 'firewalld'], calls)
        self.assertTrue(all(a[0] not in ('kubectl', 'systemctl', 'crictl') for a in calls))

    def test_pinned_real_helper_omits_false_truncation_flags(self):
        path = Path(o.__file__).parent / 'node-firewall.py'
        spec = importlib.util.spec_from_file_location('withdrawal_pinned_helper', path)
        node = importlib.util.module_from_spec(spec); spec.loader.exec_module(node)
        with patch.object(node, 'binary', side_effect=lambda x: '/usr/bin/' + x), \
             patch.object(node.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='running\n', stderr='')):
            result = node.command(['firewall-cmd', '--state'])
        self.assertNotIn('stdout_truncated', result)
        self.assertEqual(o.success(result), 'running')
        node.command = lambda *a, **kw: result
        observer = o.LocalObserver(node, fixture()[1], lambda p: b'', lambda p: '', self.clock)
        self.assertFalse(observer.unit_command(['systemctl', 'show'], 1)['truncated'])
        for change in ({'stdout_truncated': True}, {'stderr_truncated': None}, {'stderr': 'warning'}, {'rc': True}):
            with self.subTest(change=change), self.assertRaises(ValueError): o.success(dict(result, **change))

    def test_opaque_xt_cannot_claim_whole_fixed_state_coverage(self):
        raw = native()
        raw['nftables'].append({'rule': {'family': 'ip', 'table': 'filter', 'chain': 'cali-INPUT',
                                        'expr': [{'xt': {'type': 'match', 'name': 'set'}}]}})
        with self.assertRaisesRegex(ValueError, 'XT_COMPAT_COVERAGE_REQUIRED'): o.native_view(raw, 'ACTIVE')


if __name__ == '__main__': unittest.main()
