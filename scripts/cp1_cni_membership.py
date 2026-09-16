"""Managed CP1 membership transaction and receipt contract, with no host IO.

An adapter must authenticate/collect observations, verify complete fixed state,
enforce exclusive policy ownership and execute bounded firewall operations.
This module is not an installer, independent systemd recovery service or node
controller. Its receipts never grant CP1 acceptance or authorize Join.
"""
import copy
import hashlib
import ipaddress
import json
import math
import re

HOST = 'k8s-control01'
BLOCK = ipaddress.ip_network('10.244.246.64/26')
STALE = 30
RESERVE = 180
MAX_LEASE = 7200
MAX_GENERATIONS = 256
MAX_OPERATIONS = 4096
SCOPE = {'host': HOST, 'block': str(BLOCK), 'family': 'ipv4', 'layer4': 'ALL',
         'source': 'POD_IP_AND_IIF', 'destination': 'POD_IP_AND_OIF',
         'zone': 'public', 'purpose': 'JOIN_PREPARATION_MANAGED_MEMBERSHIP'}
MEMBER_KEYS = {'namespace', 'pod', 'uid', 'ip', 'interface', 'ifindex', 'peer_ifindex', 'sandbox_id'}


def need(value, message):
    if not value:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2,
                       allow_nan=False) + '\n').encode()


def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def same(left, right):
    return encoded(left) == encoded(right)


def digest(value):
    need(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value), 'digest absent/invalid')


def number(value):
    need(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'invalid clock value')
    return value


def boot(value):
    need(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value),
         'invalid boot identity')


def members(value):
    need(isinstance(value, list) and len(value) <= 62, 'membership is not a bounded list')
    ips, names, uids, indexes, sandboxes = set(), set(), set(), set(), set()
    result = []
    for row in value:
        need(isinstance(row, dict) and set(row) == MEMBER_KEYS, 'member fields differ')
        for field in ('namespace', 'pod', 'uid'):
            need(isinstance(row[field], str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,253}', row[field]),
                 'member identifier invalid')
        address = ipaddress.ip_address(row['ip'])
        need(address in BLOCK and address not in (BLOCK.network_address, BLOCK.broadcast_address),
             'member address outside owned block')
        need(str(address) == row['ip'] and re.fullmatch(r'cali[0-9a-f]{11}', row['interface']),
             'member needs canonical IP and concrete cali veth')
        need(type(row['ifindex']) is int and row['ifindex'] > 0 and
             type(row['peer_ifindex']) is int and row['peer_ifindex'] > 0, 'veth peer identity absent')
        need(isinstance(row['sandbox_id'], str) and re.fullmatch(r'[a-f0-9]{64}', row['sandbox_id']),
             'sandbox identity absent')
        for seen, item in ((ips, row['ip']), (names, row['interface']), (uids, row['uid']),
                           (indexes, row['ifindex']), (sandboxes, row['sandbox_id'])):
            need(item not in seen, 'duplicate member identity')
            seen.add(item)
        result.append(copy.deepcopy(row))
    return sorted(result, key=lambda r: r['interface'])


def entry(member):
    return member['ip'] + '/32,' + member['interface']


def select_members(raw):
    """Project complete collector results into eligible endpoints.

    sandbox_peers is a LOCAL collector contract, not a Kubernetes CRD: each
    record binds CRI sandbox Pod metadata to two PID-start/netns samples and
    the peer's addresses/ifindex/iflink. This module does not collect those
    facts or pretend that a boolean peer_verified substitutes for them.
    """
    for kind in ('pods', 'blocks', 'affinities'):
        need(isinstance(raw[kind].get('items'), list) and raw[kind].get('metadata', {}).get('resourceVersion'),
             'complete API list/resourceVersion absent')
    blocks = [v['spec'] for v in raw['blocks']['items'] if v['spec'].get('cidr') == str(BLOCK)]
    affinities = [v['spec'] for v in raw['affinities']['items'] if v['spec'].get('cidr') == str(BLOCK)]
    need(len(blocks) == len(affinities) == 1 and blocks[0].get('affinity') == 'host:' + HOST and
         blocks[0].get('deleted', False) is False and affinities[0].get('node') == HOST and
         affinities[0].get('state') == 'confirmed' and affinities[0].get('deleted', False) in (False, 'false'),
         'confirmed CP1 block ownership absent')
    links = {r['ifname']: r for r in raw['links']}
    need(len(links) == len(raw['links']), 'duplicate link names')
    result = []
    for pod in raw['pods']['items']:
        spec, meta, status = pod['spec'], pod['metadata'], pod.get('status', {})
        if spec.get('nodeName') != HOST or spec.get('hostNetwork') is True or meta.get('deletionTimestamp'):
            continue
        if status.get('phase') in ('Succeeded', 'Failed'):
            continue
        need(status.get('phase') in ('Pending', 'Running'), 'unknown Pod phase')
        ip = status.get('podIP')
        if not ip:
            need(not status.get('podIPs'), 'ambiguous unallocated Pod IP')
            continue
        need(status.get('podIPs') == [{'ip': ip}], 'single-stack Pod IP required')
        routes = [r for r in raw['routes'] if r.get('dst') in (ip, ip + '/32')]
        need(len(routes) == 1, 'missing/ambiguous Pod route')
        route = routes[0]
        need(route.get('table', 'main') in ('main', 254) and route.get('type', 'unicast') == 'unicast' and
             not any(k in route for k in ('gateway', 'via', 'nexthops', 'encap')), 'non-local Pod route')
        link = links.get(route.get('dev'), {})
        need(link.get('linkinfo', {}).get('info_kind') == 'veth' and 'UP' in link.get('flags', []),
             'Pod route is not an UP veth')
        peers = [r for r in raw['sandbox_peers'] if r.get('pod_uid') == meta['uid']]
        need(len(peers) == 1, 'ambiguous/missing local sandbox')
        peer = peers[0]
        need(peer.get('namespace') == meta['namespace'] and peer.get('pod') == meta['name'] and
             peer.get('state') == 'SANDBOX_READY', 'sandbox Pod identity/state differs')
        for before, after in (('pid_start_before', 'pid_start_after'), ('netns_before', 'netns_after')):
            need(type(peer.get(before)) is int and type(peer.get(after)) is int and
                 peer[before] > 0 and peer[before] == peer[after],
                 'sandbox PID/netns changed while observing')
        for value in (peer.get('host_ifindex'), peer.get('ifindex'), peer.get('iflink'),
                      link.get('ifindex'), link.get('link_index')):
            need(type(value) is int and value > 0, 'invalid peer link index')
        need(peer.get('host_ifindex') == link.get('ifindex') and peer.get('ifindex') == link.get('link_index') and
             peer.get('iflink') == link.get('ifindex') and peer.get('ipv4') == [ip], 'veth peer/IP differs')
        result.append({'namespace': meta['namespace'], 'pod': meta['name'], 'uid': meta['uid'], 'ip': ip,
                       'interface': link['ifname'], 'ifindex': link['ifindex'], 'peer_ifindex': peer['ifindex'],
                       'sandbox_id': peer['sandbox_id']})
    return members(result)


def entries(value):
    need(isinstance(value, list) and len(value) <= 62 and len(value) == len(set(value)),
         'duplicate/unbounded entries')
    for item in value:
        need(isinstance(item, str) and re.fullmatch(r'[0-9.]+/32,cali[0-9a-f]{11}', item),
             'entry is not exact IP/veth')
        address = ipaddress.ip_interface(item.split(',')[0])
        need(address.ip in BLOCK and address.ip not in (BLOCK.network_address, BLOCK.broadcast_address) and
             str(address) == item.split(',')[0], 'entry outside owned block')
    return sorted(value)


def validate_lease(value):
    need(value.get('schema') == 'fds.cp1-cni-membership-lease.v1' and same(value.get('scope'), SCOPE),
         'lease scope/schema differs')
    need(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', value.get('run_id', '')), 'run ID differs')
    boot(value.get('boot_id'))
    for key in ('source_sha256', 'policy_sha256', 'original_input_sha256',
                'activation_sha256', 'fixed_state_sha256'):
        digest(value.get(key))
    for key in ('wall_start', 'wall_end', 'boot_start', 'boot_end'):
        number(value.get(key))
    wall_duration = value['wall_end'] - value['wall_start']
    boot_duration = value['boot_end'] - value['boot_start']
    need(RESERVE < wall_duration <= MAX_LEASE and wall_duration == boot_duration, 'unbounded/unequal lease')
    return value


def remaining(lease, clock, reserve=0):
    validate_lease(lease)
    boot(clock.get('boot_id'))
    need(clock['boot_id'] == lease['boot_id'], 'boot changed: recovery only')
    wall, elapsed = number(clock['wall']), number(clock['boottime'])
    need(wall >= lease['wall_start'] and elapsed >= lease['boot_start'], 'clock predates lease')
    result = min(lease['wall_end'] - wall, lease['boot_end'] - elapsed) - reserve
    need(result > 0, 'lease exhausted')
    return result


def observation(value, lease, clock):
    remaining(lease, clock, RESERVE)
    need(value.get('schema') == 'fds.cp1-cni-membership-observation.v1' and value.get('complete') is True,
         'complete observation required')
    need(value.get('boot_id') == lease['boot_id'] and value.get('host') == HOST and
         value.get('block') == str(BLOCK) and value.get('block_owner') == HOST and
         value.get('affinity_confirmed') is True, 'observation ownership/boot differs')
    for key, current in (('wall', clock['wall']), ('boottime', clock['boottime'])):
        captured = number(value[key])
        need(0 <= current - captured < STALE, 'stale/future observation')
    need(value.get('peer_verification') == 'LOCAL_SANDBOX_AND_HOST_VETH' and
         value.get('fixed_state_sha256') == lease['fixed_state_sha256'], 'peer/fixed-state verification absent')
    # Ready is intentionally not part of eligibility. A trusted collector must
    # exclude deleting/terminal/hostNetwork Pods and Pods without an allocated IP.
    result = members(value.get('members'))
    need(same(result, value['members']), 'observation members not canonical')
    return result


def snapshot(value, lease, require_guard=True):
    need(value.get('boot_id') == lease['boot_id'], 'snapshot boot differs')
    need(value.get('owned') is True and value.get('run_id') == lease['run_id'], 'exclusive ownership absent')
    need(value.get('fixed_state_sha256') == lease['fixed_state_sha256'], 'fixed state drift')
    need(type(value.get('policy_enabled')) is bool, 'policy state absent')
    if require_guard:
        need(value.get('guard_armed') is True and value.get('guard_deadline') == lease['boot_end'],
             'independent guard unavailable')
    native, daemon = entries(value.get('native_entries')), entries(value.get('daemon_entries'))
    need(native == daemon, 'native/daemon membership differs')
    return native


def observation_from_capture(capture, lease, actual, clock):
    """Bridge the read-only collector to a trusted full-state observer.

    These are local data contracts, not authentication of arbitrary uploaded
    JSON. The privileged adapter must verify file/source origin separately.
    """
    need(capture.get('schema') == 'fds.cp1-cni-membership-raw.v1' and
         capture.get('mode') == 'READ_ONLY' and capture.get('complete') is True and
         capture.get('errors') == [] and capture.get('authorizes_apply') is False,
         'complete read-only capture required')
    snapshot(actual, lease)
    before = select_members(capture['raw'])
    after = select_members(capture['raw_after'])
    need(same(before, after) and same(after, capture['members']), 'capture membership differs')
    for key in ('wall', 'boottime'):
        need(number(capture[key]) <= number(capture['ended_' + key]) <= number(clock[key]),
             'capture clock order differs')
    value = {'schema': 'fds.cp1-cni-membership-observation.v1', 'complete': True,
             'boot_id': capture['boot_id'], 'host': HOST, 'block': str(BLOCK), 'block_owner': HOST,
             'affinity_confirmed': True, 'wall': capture['wall'], 'boottime': capture['boottime'],
             'peer_verification': 'LOCAL_SANDBOX_AND_HOST_VETH',
             'fixed_state_sha256': actual['fixed_state_sha256'], 'members': after}
    observation(value, lease, clock)
    return value


def initial_state(lease, actual, clock):
    """Consume an activation adapter's empty, enabled owned set.

    Installation is a distinct prerequisite. This input is not an activation
    receipt and does not validate the original receipt or packet semantics.
    """
    remaining(lease, clock, RESERVE)
    # An empty set grants no pair. Bootstrap can precede timer enablement;
    # reconcile/additions still require an independently verified live guard.
    need(snapshot(actual, lease, require_guard=False) == [] and actual['policy_enabled'] is True, 'empty active owned set required')
    return {'schema': 'fds.cp1-cni-membership-state.v1', 'lease': copy.deepcopy(lease),
            'phase': 'INITIALIZED', 'expected_entries': [], 'members': [], 'pending': None,
            'operations': [], 'generations': [], 'latest_observation': None,
            'observations': {}, 'withdrawn_pending': [],
            'last_clock': copy.deepcopy(clock), 'withdraw_reason': None,
            'membership_withdrawn': False, 'full_recovery_complete': False,
            'authorizes_cp1_acceptance': False, 'authorizes_cp2_change': False, 'authorizes_join': False}


def validate_state(state):
    need(state.get('schema') == 'fds.cp1-cni-membership-state.v1', 'state schema differs')
    validate_lease(state['lease'])
    need(state['expected_entries'] == entries(state['expected_entries']), 'state entries not canonical')
    need(same(members(state['members']), state['members']), 'state members not canonical')
    need(state['phase'] in ('INITIALIZED', 'RECONCILING', 'ACTIVE_REVIEW_REQUIRED',
                           'WITHDRAWING', 'MEMBERSHIP_WITHDRAWN_REVIEW_REQUIRED'), 'unknown phase')
    for key in ('full_recovery_complete', 'authorizes_cp1_acceptance', 'authorizes_cp2_change', 'authorizes_join'):
        need(state.get(key) is False, 'membership state cannot grant ' + key)


def seed_observation(state, observed, actual, clock):
    """Seed a completed collection before starting the independent watchdog.

    No startup grace and no deadline renewal: unseeded state remains RECOVER.
    The activation adapter must persist this result before arming the timer.
    """
    validate_state(state)
    need(state['phase'] == 'INITIALIZED' and not state['operations'] and not state['generations'] and
         state['pending'] is None and not state['expected_entries'] and not state['members'] and
         state['latest_observation'] is None, 'bootstrap state already used')
    observation(observed, state['lease'], clock)
    need(snapshot(actual, state['lease'], require_guard=False) == [] and actual['policy_enabled'] is True, 'bootstrap grant not empty')
    result = copy.deepcopy(state)
    result['latest_observation'] = copy.deepcopy(observed)
    result['last_clock'] = copy.deepcopy(clock)
    return result


def guard_action(state, clock, service_alive=True):
    """Pure decision for an independent guard; does not run a systemd service."""
    validate_state(state)
    if state['phase'] in ('WITHDRAWING', 'MEMBERSHIP_WITHDRAWN_REVIEW_REQUIRED'):
        return 'RECOVER'
    try:
        remaining(state['lease'], clock, RESERVE)
        need(service_alive is True, 'manager stopped')
        last = state['latest_observation']
        need(last is not None, 'no completed observation')
        observation(last, state['lease'], clock)
    except (ValueError, TypeError, KeyError):
        return 'RECOVER'
    return 'WATCH'


class Manager:
    """Write-ahead transaction. Caller holds the journal's exclusive lock.

    io.clock(), io.snapshot(), io.change(action, entry), io.disable() must be
    bounded. snapshot must attest full fixed state and exclusive ownership,
    not just read firewall-cmd's in-memory membership list. Journal.save must
    fsync the file and its directory before returning.
    """
    def __init__(self, state, io, journal):
        validate_state(state)
        self.state, self.io, self.journal = copy.deepcopy(state), io, journal
        self.lease = self.state['lease']

    def save(self):
        self.journal.save(self.state)

    def clock(self, active=True):
        current = self.io.clock()
        if active:
            remaining(self.lease, current, RESERVE)
            last = self.state['last_clock']
            need(current['wall'] >= last['wall'] and current['boottime'] >= last['boottime'],
                 'clock moved backward')
        return current

    def actual(self, guard=True):
        value = self.io.snapshot()
        found = snapshot(value, self.lease, require_guard=guard)
        return value, found

    def reconcile_pending(self, require_guard=True):
        actual, found = self.actual(require_guard)
        pending = self.state['pending']
        if pending is None:
            need(found == self.state['expected_entries'], 'unjournaled membership drift')
            return actual
        need(found in (pending['before'], pending['after']), 'pending operation has unknown third state')
        applied = found == pending['after']
        self.state['operations'].append(dict(pending, outcome='APPLIED_OBSERVED' if applied else 'NOT_APPLIED_OBSERVED',
                                             result=None, verified_entries=found))
        self.state['expected_entries'] = found
        self.state['pending'] = None
        self.save()
        return actual

    def change(self, action, item, desired=None):
        actual = self.reconcile_pending(require_guard=desired is not None)
        current = self.clock(active=desired is not None)
        need(actual['policy_enabled'] is (desired is not None), 'policy state differs for operation')
        if desired is not None:
            # Revalidate observation age before EACH addition, not only once at
            # the beginning of a potentially long reconciliation.
            observation(desired, self.lease, current)
            need(len(self.state['operations']) < MAX_OPERATIONS, 'operation bound exhausted')
            self.state['observations'][fingerprint(desired)] = copy.deepcopy(desired)
        before = self.state['expected_entries'][:]
        need((action == 'remove' and item in before) or (action == 'add' and item not in before),
             'operation presence differs')
        after = sorted([v for v in before if v != item] if action == 'remove' else before + [item])
        pending = {'sequence': len(self.state['operations']) + 1, 'action': action, 'entry': item,
                   'before': before, 'after': after, 'clock': copy.deepcopy(current),
                   'observation_sha256': fingerprint(desired) if desired is not None else None}
        self.state['pending'] = pending
        self.save()  # No external mutation may precede durable intent.
        deadline = {'wall': min(desired['wall'] + STALE, self.lease['wall_end'] - RESERVE),
                    'boottime': min(desired['boottime'] + STALE, self.lease['boot_end'] - RESERVE)
                    } if desired is not None else None
        result = self.io.change(action, item, deadline=deadline, expected_observation=desired)
        need(isinstance(result, dict) and set(result) == {'rc', 'stdout', 'stderr', 'truncated'},
             'operation result incomplete')
        need(type(result['rc']) is int and result['rc'] == 0 and result['truncated'] is False and
             result['stdout'].strip() == 'success' and not result['stderr'].strip(), 'uncertain command result')
        post, found = self.actual(guard=desired is not None)
        need(post['policy_enabled'] is (desired is not None) and found == after,
             'post-operation state differs')
        fresh = None
        if desired is not None:
            fresh = self.io.check_members(desired)
            need(same(observation(fresh, self.lease, self.clock()), desired['members']),
                 'eligible identity changed after operation')
            pending['identity_after'] = copy.deepcopy(fresh)
            pending['verified_clock'] = copy.deepcopy(self.clock())
        self.state['operations'].append(dict(pending, outcome='COMMAND_AND_STATE_VERIFIED',
                                             result=copy.deepcopy(result), verified_entries=found))
        self.state['expected_entries'], self.state['pending'] = after, None
        self.state['last_clock'] = pending.get('verified_clock', current)
        if fresh is not None:
            self.state['latest_observation'] = copy.deepcopy(fresh)
        self.save()
        return fresh

    def reconcile(self, observed):
        need(self.state['phase'] not in ('WITHDRAWING', 'MEMBERSHIP_WITHDRAWN_REVIEW_REQUIRED'),
             'withdrawal is sticky: a new activation is required')
        try:
            current = self.clock()
            desired = observation(observed, self.lease, current)
            actual = self.reconcile_pending()
            need(actual['policy_enabled'] is True, 'policy no longer enabled')
            old_by_entry = {entry(r): r for r in self.state['members']}
            new_by_entry = {entry(r): r for r in desired}
            # UID/sandbox/ifindex reuse forces remove then re-add even when the
            # IP/interface string stays equal. This is bounded reconciliation,
            # not a claim that existing conntrack flows are immediately revoked.
            reused = {k for k in old_by_entry.keys() & new_by_entry.keys()
                      if not same(old_by_entry[k], new_by_entry[k])}
            previous = self.state['generations'][-1] if self.state['generations'] else None
            changed = (previous is None or not same(previous['members'], desired) or
                       len(self.state['operations']) != previous['operation_count'])
            need(not changed or len(self.state['generations']) < MAX_GENERATIONS,
                 'generation bound exhausted')
            self.state['phase'] = 'RECONCILING'
            self.state['observations'][fingerprint(observed)] = copy.deepcopy(observed)
            self.state['latest_observation'] = copy.deepcopy(observed)
            self.save()
            working = observed
            for item in sorted(set(self.state['expected_entries']) - new_by_entry.keys() | reused):
                if item in self.state['expected_entries']:
                    working = self.change('remove', item, working)
            for item in sorted(new_by_entry):
                if item not in self.state['expected_entries']:
                    working = self.change('add', item, working)
            current = self.clock()
            observation(working, self.lease, current)
            actual, found = self.actual()
            need(actual['policy_enabled'] and found == sorted(new_by_entry), 'final membership differs')
            fresh = self.io.check_members(working)
            current = self.clock()
            need(same(observation(fresh, self.lease, current), desired), 'final eligible identity changed')
            observation(working, self.lease, current)
            observed = fresh
            self.state['observations'][fingerprint(observed)] = copy.deepcopy(observed)
            previous = self.state['generations'][-1] if self.state['generations'] else None
            changed = previous is None or not same(previous['members'], desired) or len(self.state['operations']) != previous['operation_count']
            if changed:
                generation = {'schema': 'fds.cp1-cni-membership-generation.v1',
                    'sequence': len(self.state['generations']) + 1,
                    'previous_sha256': fingerprint(previous) if previous else self.lease['activation_sha256'],
                    'lease_sha256': fingerprint(self.lease), 'members': desired, 'entries': found,
                    'observation_sha256': fingerprint(observed), 'clock': current,
                    'operation_count': len(self.state['operations']),
                    'operations_sha256': fingerprint(self.state['operations'])}
                self.state['generations'].append(generation)
            self.state.update(members=desired, phase='ACTIVE_REVIEW_REQUIRED',
                              latest_observation=copy.deepcopy(observed), last_clock=current)
            used = {r['observation_sha256'] for r in self.state['operations']} | {
                r['observation_sha256'] for r in self.state['generations']}
            self.state['observations'] = {k: v for k, v in self.state['observations'].items() if k in used}
            self.save()
        except Exception as error:
            # Record the error before the caller's independent recovery adapter
            # disables policy. Never silently continue additions after uncertainty.
            self.state.update(phase='WITHDRAWING', withdraw_reason=str(error))
            self.save()
            raise
        return copy.deepcopy(self.state)

    def reconcile_or_withdraw(self, observed):
        """Fail closed on a handled error; process death still needs the guard.

        Keep the original error even if membership withdrawal succeeds. The
        resulting receipt is never ACTIVE and never full-recovery complete.
        """
        try:
            return self.reconcile(observed)
        except Exception as error:
            self.withdraw(str(error))
            raise

    def withdraw(self, reason):
        """Disable the owned grant before touching membership.

        Intentionally returns MEMBERSHIP_WITHDRAWN_REVIEW_REQUIRED. Removing
        permanent definitions, restoring original rules, management checks and
        terminating/disarming units belong to the full recovery adapter.
        """
        self.state.update(phase='WITHDRAWING', withdraw_reason=str(reason))
        save_error = None
        try:
            self.save()
        except Exception as error:
            # Storage failure must not stop the independently owned policy's
            # fail-closed disable. A failed journal can never yield completion.
            save_error = error
        # Fixed-state drift/boot change must not prevent an independent adapter
        # from disabling its provably owned policy. The adapter verifies that
        # narrow ownership itself and refuses any unproven foreign operation.
        self.io.disable()
        actual = self.io.snapshot()
        need(actual.get('owned') is True and actual.get('run_id') == self.lease['run_id'] and
             actual.get('policy_enabled') is False, 'owned policy withdrawal not verified')
        if save_error is not None:
            raise ValueError('owned policy disabled; journal unavailable; recovery review required') from save_error
        # A different boot or fixed-state drift requires full recovery review;
        # disabling the grant alone must never be reported as full recovery.
        found = snapshot(actual, self.lease, require_guard=False)
        if self.state['pending'] is not None:
            self.state['withdrawn_pending'].append({'intent': copy.deepcopy(self.state['pending']),
                                                   'observed_after_disable': found})
        self.state['pending'] = None
        self.state['expected_entries'] = found
        self.save()
        for item in found:
            self.change('remove', item)
        final = self.io.snapshot()
        need(snapshot(final, self.lease, require_guard=False) == [] and final['policy_enabled'] is False,
             'membership withdrawal incomplete')
        self.state.update(phase='MEMBERSHIP_WITHDRAWN_REVIEW_REQUIRED', membership_withdrawn=True)
        self.save()
        return copy.deepcopy(self.state)


def validate_generation_chain(state):
    validate_state(state)
    need(state['phase'] == 'ACTIVE_REVIEW_REQUIRED' and state['pending'] is None, 'generation not active/stable')
    lease = state['lease']
    previous, count = lease['activation_sha256'], 0
    replay = []
    operations = state['operations']
    need(isinstance(operations, list) and len(operations) <= MAX_OPERATIONS, 'operation list absent/unbounded')
    last_clock = {'wall': lease['wall_start'], 'boottime': lease['boot_start']}
    for i, operation in enumerate(operations, 1):
        for key in ('wall', 'boottime'):
            need(number(operation['clock'][key]) >= last_clock[key], 'operation clock moved backward')
        last_clock = operation['clock']
        need(operation['sequence'] == i and type(operation['sequence']) is int and
             operation['before'] == replay, 'operation prefix/sequence differs')
        item = operation['entry']; entries([item])
        action = operation['action']
        need((action == 'add' and item not in replay) or (action == 'remove' and item in replay),
             'invalid operation transition')
        after = sorted(replay + [item] if action == 'add' else [v for v in replay if v != item])
        need(after == operation['after'], 'operation after differs')
        outcome = operation['outcome']
        need(outcome in ('COMMAND_AND_STATE_VERIFIED', 'APPLIED_OBSERVED', 'NOT_APPLIED_OBSERVED'),
             'unknown operation outcome')
        if outcome == 'COMMAND_AND_STATE_VERIFIED':
            result = operation['result']
            need(set(result) == {'rc', 'stdout', 'stderr', 'truncated'} and type(result['rc']) is int and
                 result['rc'] == 0 and result['truncated'] is False and
                 result['stdout'].strip() == 'success' and not result['stderr'].strip(),
                 'operation command result differs')
        else:
            need(operation['result'] is None, 'reconciled operation must retain missing command result')
        replay = replay if outcome == 'NOT_APPLIED_OBSERVED' else after
        need(operation['verified_entries'] == replay, 'operation verification differs')
        observed = state['observations'].get(operation['observation_sha256'])
        need(observed is not None and fingerprint(observed) == operation['observation_sha256'],
             'operation observation absent/different')
        eligible = observation(observed, lease, operation['clock'])
        if outcome == 'COMMAND_AND_STATE_VERIFIED':
            verified_clock = operation.get('verified_clock')
            need(isinstance(verified_clock,dict) and
                 all(number(verified_clock[k]) >= operation['clock'][k] for k in ('wall','boottime')),
                 'post-mutation identity clock absent/different')
            need(same(observation(operation.get('identity_after',{}),lease,verified_clock),eligible),
                 'post-mutation identity proof differs')
        if action == 'add':
            need(item in [entry(r) for r in eligible], 'addition not authorized by observation')
    generations = state['generations']
    need(isinstance(generations, list) and 0 < len(generations) <= MAX_GENERATIONS, 'generation chain absent/unbounded')
    last_clock = {'wall': lease['wall_start'], 'boottime': lease['boot_start']}
    for i, generation in enumerate(generations, 1):
        need(generation.get('schema') == 'fds.cp1-cni-membership-generation.v1' and
             type(generation['sequence']) is int and generation['sequence'] == i and
             generation['previous_sha256'] == previous and generation['lease_sha256'] == fingerprint(lease),
             'generation chain link differs')
        rows = members(generation['members'])
        need(same(rows, generation['members']) and generation['entries'] == sorted(entry(r) for r in rows),
             'generation membership differs')
        upto = generation['operation_count']
        need(type(upto) is int and count <= upto <= len(operations), 'generation operation range differs')
        need(generation['operations_sha256'] == fingerprint(operations[:upto]), 'generation operation digest differs')
        observed_entries = operations[upto - 1]['verified_entries'] if upto else []
        need(observed_entries == generation['entries'], 'generation does not match executed prefix')
        remaining(lease, generation['clock'], RESERVE)
        for key in ('wall', 'boottime'):
            need(number(generation['clock'][key]) >= last_clock[key] and
                 (not upto or generation['clock'][key] >= operations[upto - 1]['clock'][key]) and
                 (upto == len(operations) or generation['clock'][key] <= operations[upto]['clock'][key]),
                 'generation clock/prefix order differs')
        last_clock = generation['clock']
        observed = state['observations'].get(generation['observation_sha256'])
        need(observed is not None and fingerprint(observed) == generation['observation_sha256'] and
             same(observation(observed, lease, generation['clock']), rows), 'generation observation differs')
        previous, count = fingerprint(generation), upto
    need(count == len(operations) and replay == state['expected_entries'] == generations[-1]['entries'] and
         same(state['members'], generations[-1]['members']), 'latest generation/state differs')
    return previous


def consume(state, observed, live, clock, *, target, stage, expected_lease_sha256):
    """Validate manager-produced history and fresh adapter assertions.

    This is a consumer CONTRACT, not run-node-firewall.py integration or human
    acceptance. Input hashes/booleans do not authenticate a live host or review.
    """
    need(target in ('k8s-control02', 'k8s-control03') and stage in ('BEFORE_TARGET', 'AFTER_TARGET'),
         'consumer target/stage differs')
    head = validate_generation_chain(state)
    lease = state['lease']
    digest(expected_lease_sha256)
    need(fingerprint(lease) == expected_lease_sha256, 'external lease binding differs')
    remaining(lease, clock, 660 if stage == 'BEFORE_TARGET' else RESERVE)
    for key in ('wall', 'boottime'):
        need(number(clock[key]) >= number(state['last_clock'][key]) >=
             number(state['generations'][-1]['clock'][key]), 'consumer clock predates committed state')
    desired = observation(observed, lease, clock)
    need(same(desired, state['members']), 'fresh membership differs from committed generation')
    need(snapshot(live, lease) == state['expected_entries'] and live['policy_enabled'] is True,
         'live membership differs')
    need(live.get('generation_sha256') == head and live.get('target_registered') is False,
         'live generation/target pre-Join state differs')
    return {'status': 'CONSISTENT_REVIEW_REQUIRED', 'stage': stage, 'target': target,
            'generation_sha256': head, 'lease_sha256': fingerprint(lease),
            'runtime_independently_verified': False, 'production_consumer_connected': False,
            'authorizes_cp1_acceptance': False, 'authorizes_cp2_change': False, 'authorizes_join': False}
