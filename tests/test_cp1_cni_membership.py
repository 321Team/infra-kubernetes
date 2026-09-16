"""Transaction/receipt regressions. Firewall and clock IO are modeled explicitly."""
import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cp1_cni_membership as m
from cp1_cni_membership_io import FirewalldMembershipIO

BOOT = '12345678-1234-1234-1234-123456789abc'
OK = {'rc': 0, 'stdout': 'success\n', 'stderr': '', 'truncated': False}


def member(i=0):
    return {'namespace': 'test', 'pod': 'pod-' + str(i), 'uid': 'uid-' + str(i),
            'ip': '10.244.246.' + str(80+i), 'interface': 'cali' + format(i, '011x'),
            'ifindex': 10+i, 'peer_ifindex': 2, 'sandbox_id': format(i+1, '064x')}


def lease():
    return {'schema': 'fds.cp1-cni-membership-lease.v1', 'scope': copy.deepcopy(m.SCOPE),
            'run_id': '20260917T000000Z-abcdef012345', 'boot_id': BOOT,
            'source_sha256': 'a'*64, 'policy_sha256': 'b'*64, 'original_input_sha256': 'c'*64,
            'activation_sha256': 'd'*64, 'fixed_state_sha256': 'e'*64,
            'wall_start': 900, 'wall_end': 8100, 'boot_start': 10, 'boot_end': 7210}


def raw_fixture():
    r = member()
    def listing(items):
        return {'metadata': {'resourceVersion': '100'}, 'items': items}
    return {'pods': listing([{'metadata': {'name': r['pod'], 'namespace': r['namespace'], 'uid': r['uid']},
              'spec': {'nodeName': m.HOST}, 'status': {'phase': 'Running', 'podIP': r['ip'],
              'podIPs': [{'ip': r['ip']}], 'conditions': [{'type': 'Ready', 'status': 'False'}]}}]),
            'blocks': listing([{'spec': {'cidr': str(m.BLOCK), 'affinity': 'host:' + m.HOST}}]),
            'affinities': listing([{'spec': {'cidr': str(m.BLOCK), 'node': m.HOST, 'state': 'confirmed'}}]),
            'links': [{'ifname': r['interface'], 'ifindex': r['ifindex'], 'link_index': r['peer_ifindex'],
                       'linkinfo': {'info_kind': 'veth'}, 'flags': ['UP']}],
            'routes': [{'dst': r['ip'], 'dev': r['interface'], 'scope': 'link'}],
            'sandbox_peers': [{'pod_uid': r['uid'], 'pod': r['pod'], 'namespace': r['namespace'],
                 'state': 'SANDBOX_READY', 'sandbox_id': r['sandbox_id'], 'host_ifindex': r['ifindex'],
                 'ifindex': r['peer_ifindex'], 'iflink': r['ifindex'], 'ipv4': [r['ip']],
                 'pid_start_before': 111, 'pid_start_after': 111, 'netns_before': 999, 'netns_after': 999}]}


class Crash(BaseException):
    pass


class MemoryJournal:
    def __init__(self):
        self.saved = None
        self.fail = False

    def save(self, state):
        if self.fail:
            raise OSError('disk full')
        self.saved = copy.deepcopy(state)


class ModelIO:
    def __init__(self, l, journal):
        self.lease, self.journal = l, journal
        self.now = {'boot_id': BOOT, 'wall': 1000, 'boottime': 110}
        self.live = {'boot_id': BOOT, 'owned': True, 'run_id': l['run_id'],
                     'fixed_state_sha256': l['fixed_state_sha256'], 'policy_enabled': True,
                     'guard_armed': True, 'guard_deadline': l['boot_end'],
                     'native_entries': [], 'daemon_entries': []}
        self.calls, self.crash, self.split = [], None, False
        self.result = copy.deepcopy(OK)

    def clock(self):
        return copy.deepcopy(self.now)

    def advance(self, seconds):
        self.now['wall'] += seconds
        self.now['boottime'] += seconds

    def snapshot(self):
        return copy.deepcopy(self.live)

    def change(self, action, item, *, deadline=None):
        # This assertion verifies the write-ahead contract at the IO boundary.
        pending = self.journal.saved['pending']
        assert pending and pending['action'] == action and pending['entry'] == item
        self.calls.append((action, item))
        crash, self.crash = self.crash, None
        if crash == 'before':
            raise Crash()
        for field in ('native_entries', 'daemon_entries'):
            if self.split and field == 'native_entries':
                continue
            values = self.live[field]
            self.live[field] = sorted(values + [item] if action == 'add' else [x for x in values if x != item])
        if crash == 'after':
            raise Crash()
        return copy.deepcopy(self.result)

    def disable(self):
        if not self.live['owned']:
            raise ValueError('foreign policy')
        self.calls.append(('disable', None))
        self.live['policy_enabled'] = False


def setup():
    l, j = lease(), MemoryJournal()
    io = ModelIO(l, j)
    state = m.initial_state(l, io.snapshot(), io.clock())
    j.save(state)
    return m.Manager(state, io, j), io, j


def observed(io, rows=None):
    return {'schema': 'fds.cp1-cni-membership-observation.v1', 'complete': True,
            'boot_id': BOOT, 'host': m.HOST, 'block': str(m.BLOCK), 'block_owner': m.HOST,
            'affinity_confirmed': True, 'wall': io.now['wall'], 'boottime': io.now['boottime'],
            'peer_verification': 'LOCAL_SANDBOX_AND_HOST_VETH',
            'fixed_state_sha256': io.lease['fixed_state_sha256'],
            'members': m.members([member()] if rows is None else rows)}


class EligibilityTests(unittest.TestCase):
    def test_not_ready_and_pending_with_ip_are_eligible(self):
        raw = raw_fixture()
        for phase in ('Running', 'Pending'):
            raw['pods']['items'][0]['status']['phase'] = phase
            self.assertEqual(m.select_members(raw), [member()])

    def test_unallocated_pending_does_not_invalidate_existing_member(self):
        raw = raw_fixture()
        pending = copy.deepcopy(raw['pods']['items'][0])
        pending['metadata'].update(name='new', uid='new')
        pending['status'] = {'phase': 'Pending'}
        raw['pods']['items'].append(pending)
        self.assertEqual(m.select_members(raw), [member()])

    def test_deleting_terminal_host_network_and_other_node_are_excluded(self):
        edits = [lambda p: p['metadata'].update(deletionTimestamp='now'),
                 lambda p: p['status'].update(phase='Succeeded'),
                 lambda p: p['spec'].update(hostNetwork=True),
                 lambda p: p['spec'].update(nodeName='k8s-control02')]
        for edit in edits:
            with self.subTest(edit=edit):
                raw = raw_fixture(); edit(raw['pods']['items'][0])
                self.assertEqual(m.select_members(raw), [])

    def test_ownership_peer_route_and_api_ambiguity_fail_closed(self):
        edits = [lambda r: r['blocks']['items'][0]['spec'].update(affinity='host:other'),
                 lambda r: r['affinities']['items'][0]['spec'].update(state='pending'),
                 lambda r: r['pods'].update(metadata={}),
                 lambda r: r['sandbox_peers'][0].update(pid_start_after=112),
                 lambda r: r['sandbox_peers'][0].update(netns_after=1000),
                 lambda r: r['sandbox_peers'][0].update(iflink=99),
                 lambda r: r['links'][0].update(link_index=True),
                 lambda r: r['routes'][0].update(gateway='10.1.1.1'),
                 lambda r: r['routes'].append(copy.deepcopy(r['routes'][0])),
                 lambda r: r['sandbox_peers'].append(copy.deepcopy(r['sandbox_peers'][0]))]
        for edit in edits:
            with self.subTest(edit=edit):
                raw = raw_fixture(); edit(raw)
                with self.assertRaises(ValueError):
                    m.select_members(raw)

    def test_scope_wildcard_duplicate_and_network_address_rejected(self):
        for values in (['10.244.246.80/32,cali+'], ['10.244.246.64/26,cali00000000000'],
                       ['10.244.246.64/32,cali00000000000'], ['10.244.246.128/32,cali00000000000'],
                       [m.entry(member())]*2):
            with self.subTest(values=values), self.assertRaises(ValueError):
                m.entries(values)


class TransactionTests(unittest.TestCase):
    def test_actual_producer_chain_consumed_without_altering_original_binding(self):
        manager, io, journal = setup()
        before = copy.deepcopy(manager.lease)
        for rows in ([member()], [member(1)], []):
            io.advance(1)
            obs = observed(io, rows)
            state = manager.reconcile(obs)
            live = io.snapshot(); live.update(generation_sha256=m.validate_generation_chain(state), target_registered=False)
            result = m.consume(state, obs, live, io.clock(), target='k8s-control02', stage='BEFORE_TARGET',
                               expected_lease_sha256=m.fingerprint(before))
            self.assertEqual(result['status'], 'CONSISTENT_REVIEW_REQUIRED')
            self.assertFalse(result['authorizes_join'])
            self.assertFalse(result['production_consumer_connected'])
        self.assertEqual(manager.lease, before)
        self.assertEqual(journal.saved, state)
        self.assertEqual(io.calls, [('add', m.entry(member())), ('remove', m.entry(member())),
                                   ('add', m.entry(member(1))), ('remove', m.entry(member(1)))])

    def test_identity_reuse_removes_before_readding_same_pair(self):
        manager, io, _ = setup(); manager.reconcile(observed(io))
        replacement = member(); replacement.update(uid='new-uid', sandbox_id='f'*64, ifindex=50)
        io.advance(1); state = manager.reconcile(observed(io, [replacement]))
        self.assertEqual([a for a, _ in io.calls], ['add', 'remove', 'add'])
        self.assertEqual(state['members'], [replacement])
        m.validate_generation_chain(state)

    def test_crash_before_and_after_each_remove_add_boundary_resumes(self):
        for operation in ('remove', 'add'):
            for boundary in ('before', 'after'):
                with self.subTest(operation=operation, boundary=boundary):
                    manager, io, journal = setup(); manager.reconcile(observed(io))
                    original_change = io.change
                    def interrupt(action, item, **kwargs):
                        if action == operation:
                            io.crash = boundary
                            io.change = original_change
                        return original_change(action, item, **kwargs)
                    io.change = interrupt
                    io.advance(1)
                    with self.assertRaises(Crash):
                        manager.reconcile(observed(io, [member(1)]))
                    self.assertIsNotNone(journal.saved['pending'])
                    restarted = m.Manager(journal.saved, io, journal)
                    io.advance(1)
                    state = restarted.reconcile(observed(io, [member(1)]))
                    self.assertEqual(state['expected_entries'], [m.entry(member(1))])
                    self.assertTrue(any(o['outcome'] == ('APPLIED_OBSERVED' if boundary == 'after' else 'NOT_APPLIED_OBSERVED')
                                        for o in state['operations']))
                    m.validate_generation_chain(state)

    def test_native_daemon_partial_update_does_not_commit_generation(self):
        manager, io, journal = setup(); io.split = True
        with self.assertRaisesRegex(ValueError, 'native/daemon'):
            manager.reconcile(observed(io))
        self.assertEqual(journal.saved['phase'], 'WITHDRAWING')
        self.assertEqual(journal.saved['generations'], [])
        self.assertIsNotNone(journal.saved['pending'])

    def test_handled_command_failure_disables_and_withdraws_without_full_recovery_claim(self):
        manager, io, _ = setup(); io.result['rc'] = 1
        # Removal result is successful; initial addition mutates then errors.
        original = io.change
        def command(action, item, **kwargs):
            result = original(action, item, **kwargs)
            io.result = copy.deepcopy(OK)
            return result
        io.change = command
        with self.assertRaisesRegex(ValueError, 'uncertain command'):
            manager.reconcile_or_withdraw(observed(io))
        self.assertEqual(manager.state['phase'], 'MEMBERSHIP_WITHDRAWN_REVIEW_REQUIRED')
        self.assertFalse(io.live['policy_enabled'])
        self.assertEqual(io.live['native_entries'], [])
        self.assertFalse(manager.state['full_recovery_complete'])
        self.assertEqual(len(manager.state['withdrawn_pending']), 1)

    def test_expired_boot_changed_missing_guard_fixed_drift_all_reject_additions(self):
        edits = [lambda io: io.advance(7020), lambda io: io.now.update(boot_id='a'*8 + BOOT[8:]),
                 lambda io: io.live.update(guard_armed=False),
                 lambda io: io.live.update(fixed_state_sha256='f'*64)]
        for edit in edits:
            with self.subTest(edit=edit):
                manager, io, _ = setup(); obs = observed(io); edit(io)
                with self.assertRaises(ValueError):
                    manager.reconcile(obs)
                self.assertEqual(io.calls, [])

    def test_stale_observation_withdraws_and_cannot_reactivate(self):
        manager, io, _ = setup(); manager.reconcile(observed(io))
        obs = observed(io); io.advance(m.STALE)
        with self.assertRaisesRegex(ValueError, 'stale'):
            manager.reconcile_or_withdraw(obs)
        self.assertFalse(io.live['policy_enabled'])
        with self.assertRaisesRegex(ValueError, 'sticky'):
            manager.reconcile(observed(io))

    def test_pending_unknown_third_state_rejected(self):
        manager, io, journal = setup(); io.crash = 'before'
        with self.assertRaises(Crash):
            manager.reconcile(observed(io))
        io.live.update(native_entries=[m.entry(member(1))], daemon_entries=[m.entry(member(1))])
        restarted = m.Manager(journal.saved, io, journal)
        with self.assertRaisesRegex(ValueError, 'third state'):
            restarted.reconcile(observed(io))

    def test_journal_failure_prevents_mutation_and_does_not_prevent_owned_disable(self):
        manager, io, journal = setup(); journal.fail = True
        with self.assertRaises(OSError):
            manager.reconcile(observed(io))
        self.assertEqual(io.calls, [])
        with self.assertRaisesRegex(ValueError, 'journal unavailable'):
            manager.withdraw('disk full')
        self.assertEqual(io.calls, [('disable', None)])
        self.assertFalse(manager.state['full_recovery_complete'])

    def test_foreign_policy_never_disabled(self):
        manager, io, _ = setup(); io.live['owned'] = False
        with self.assertRaisesRegex(ValueError, 'foreign'):
            manager.withdraw('drift')
        self.assertEqual(io.calls, [])

    def test_drift_disables_owned_grant_but_does_not_overwrite_other_state(self):
        manager, io, _ = setup(); manager.reconcile(observed(io))
        io.live['fixed_state_sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'fixed state'):
            manager.withdraw('drift')
        self.assertEqual(io.calls[-1], ('disable', None))
        self.assertEqual(io.live['native_entries'], [m.entry(member())])
        self.assertFalse(manager.state['full_recovery_complete'])

    def test_guard_tracks_heartbeat_clock_and_manager_liveness(self):
        manager, io, _ = setup()
        self.assertEqual(m.guard_action(manager.state, io.clock()), 'RECOVER')
        state = manager.reconcile(observed(io))
        self.assertEqual(m.guard_action(state, io.clock()), 'WATCH')
        self.assertEqual(m.guard_action(state, io.clock(), False), 'RECOVER')
        io.advance(30)
        self.assertEqual(m.guard_action(state, io.clock()), 'RECOVER')

    def test_heartbeat_does_not_grow_chain_or_exhaust_generation_bound(self):
        manager, io, _ = setup(); manager.reconcile(observed(io))
        with patch.object(m, 'MAX_GENERATIONS', 1):
            for _ in range(3):
                io.advance(1); manager.reconcile(observed(io))
            self.assertEqual(len(manager.state['generations']), 1)
            self.assertEqual(len(manager.state['observations']), 1)
            with self.assertRaisesRegex(ValueError, 'generation bound'):
                manager.reconcile(observed(io, [member(1)]))
        self.assertEqual(len(io.calls), 1)

    def test_consumer_rejects_insufficient_reserve_changed_member_and_wrong_binding(self):
        for scenario in ('reserve', 'member', 'binding', 'target', 'native', 'generation'):
            with self.subTest(scenario=scenario):
                manager, io, _ = setup(); state = manager.reconcile(observed(io))
                live = io.snapshot(); live.update(generation_sha256=m.validate_generation_chain(state), target_registered=False)
                binding = m.fingerprint(manager.lease)
                if scenario == 'reserve': io.advance(7000)
                obs = observed(io, [member(1)] if scenario == 'member' else None)
                if scenario == 'binding': binding = 'f'*64
                if scenario == 'target': live['target_registered'] = True
                if scenario == 'native': live['native_entries'] = []
                if scenario == 'generation': live['generation_sha256'] = 'f'*64
                with self.assertRaises(ValueError):
                    m.consume(state, obs, live, io.clock(), target='k8s-control02', stage='BEFORE_TARGET',
                              expected_lease_sha256=binding)

    def test_chain_rejects_broken_link_missing_raw_result_and_unauthorized_addition(self):
        edits = [lambda s: s['generations'][0].update(previous_sha256='f'*64),
                 lambda s: s['operations'][0]['result'].update(stdout='success\nsuccess\n'),
                 lambda s: s['operations'][0].update(observation_sha256='f'*64),
                 lambda s: s.update(authorizes_join=True)]
        for edit in edits:
            manager, io, _ = setup(); state = manager.reconcile(observed(io)); edit(state)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                m.validate_generation_chain(state)

    def test_consumer_cannot_use_generation_from_its_future(self):
        manager, io, _ = setup(); old = io.clock(); io.advance(10)
        state = manager.reconcile(observed(io))
        live = io.snapshot(); live.update(generation_sha256=m.validate_generation_chain(state), target_registered=False)
        obs = observed(io); obs.update(wall=old['wall'], boottime=old['boottime'])
        with self.assertRaisesRegex(ValueError, 'predates committed'):
            m.consume(state, obs, live, old, target='k8s-control02', stage='BEFORE_TARGET',
                      expected_lease_sha256=m.fingerprint(manager.lease))

    def test_rehashed_out_of_order_generation_clock_still_fails(self):
        manager, io, _ = setup(); manager.reconcile(observed(io))
        io.advance(1); state = manager.reconcile(observed(io, [member(1)]))
        state['generations'][0]['clock']['wall'] += 2
        state['generations'][1]['previous_sha256'] = m.fingerprint(state['generations'][0])
        with self.assertRaisesRegex(ValueError, 'clock/prefix order'):
            m.validate_generation_chain(state)

    def test_failed_command_cannot_be_reclassified_as_a_successful_active_receipt(self):
        for result in ({'rc': 0, 'stdout': 'success', 'stderr': 'warning', 'truncated': False},
                       {'rc': 0, 'stdout': 'success', 'stderr': '', 'truncated': True},
                       {'rc': True, 'stdout': 'success', 'stderr': '', 'truncated': False},
                       {'rc': 0, 'stdout': 'ALREADY_ENABLED', 'stderr': '', 'truncated': False}):
            manager, io, _ = setup(); io.result = result
            with self.subTest(result=result), self.assertRaises(ValueError):
                manager.reconcile(observed(io))
            self.assertEqual(manager.state['generations'], [])

    def test_wall_rollback_and_boottime_suspend_gap_are_rejected(self):
        for key, amount in (('wall', -1), ('boottime', 30)):
            manager, io, _ = setup(); manager.reconcile(observed(io)); obs = observed(io)
            io.now[key] += amount
            with self.subTest(key=key), self.assertRaises(ValueError):
                manager.reconcile(obs)

    def test_journal_operation_bound_stops_before_further_mutation(self):
        manager, io, _ = setup(); manager.reconcile(observed(io))
        with patch.object(m, 'MAX_OPERATIONS', 1), self.assertRaisesRegex(ValueError, 'operation bound'):
            manager.reconcile(observed(io, [member(1)]))
        self.assertEqual(io.calls, [('add', m.entry(member()))])


class AdapterTests(unittest.TestCase):
    def make(self, runner):
        _, self.io, _ = setup()
        return FirewalldMembershipIO(self.io.lease, self.io.snapshot, runner, lambda: BOOT,
                                    lambda: self.io.now['wall'], lambda: self.io.now['boottime'],
                                    recovery_observer=self.io.snapshot, withdrawal_observer=self.io.snapshot)

    def test_exact_argv_and_timeout_bounded_by_fresh_observation(self):
        calls = []
        adapter = self.make(lambda argv, **kw: calls.append((argv, kw)) or OK)
        adapter.change('add', m.entry(member()), deadline={'wall': 1002, 'boottime': 112})
        self.assertEqual(calls, [(['firewall-cmd', '--ipset=fds-cp1-managed',
                                  '--add-entry=10.244.246.80/32,cali00000000000'], {'timeout': 2})])

    def test_expired_observation_after_slow_snapshot_blocks_command(self):
        calls = []
        adapter = self.make(lambda *a, **kw: calls.append(a))
        def slow():
            self.io.advance(31)
            return self.io.snapshot()
        adapter.observer = slow
        with self.assertRaisesRegex(ValueError, 'deadline exhausted'):
            adapter.change('add', m.entry(member()), deadline={'wall': 1030, 'boottime': 140})
        self.assertEqual(calls, [])

    def test_disable_timeout_is_resolved_only_by_independent_observation(self):
        def runner(*args, **kwargs):
            self.io.live['policy_enabled'] = False
            raise TimeoutError('lost response')
        adapter = self.make(runner)
        self.assertEqual(adapter.disable()['exception'], 'TimeoutError')

    def test_disable_failure_without_native_confirmation_is_not_success(self):
        adapter = self.make(lambda *a, **kw: {'rc': 1})
        with self.assertRaisesRegex(ValueError, 'not observed'):
            adapter.disable()


if __name__ == '__main__':
    unittest.main()
