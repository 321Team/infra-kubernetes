"""Whole-state CP1 membership observer. No firewall mutations.

Ownership, membership and guard assertions are derived from query records,
not accepted as caller-supplied booleans. A root-owned activation record and
authenticated transport remain prerequisites; this module does not install it.
"""
import copy
import json
from pathlib import Path
import re
import shlex
import time
import hashlib
import importlib.util

from cp1_cni_membership import need, encoded, fingerprint, same, entries, boot, number
from cp1_cni_guard import verified_guard, query_units

NAME = 'fds-cp1-managed'
CHAIN = 'filter_FWD_policy_' + NAME
SUBCHAINS = [CHAIN + '_' + suffix for suffix in ('pre', 'log', 'deny', 'allow', 'post')]
ROOT = Path(__file__).resolve().parents[1]
RICH_RULE = 'rule family=ipv4 source ipset=' + NAME + ' destination ipset=' + NAME + ' accept'


class StateMismatch(ValueError):
    def __init__(self, expected, actual):
        self.differences = []
        def walk(left, right, path):
            if len(self.differences) >= 32 or same(left, right):
                return
            if isinstance(left, dict) and isinstance(right, dict):
                for key in sorted(set(left) | set(right)):
                    if key not in left or key not in right:
                        self.differences.append({'path': path + [key], 'kind': 'added' if key not in left else 'removed'})
                    else:
                        walk(left[key], right[key], path + [key])
                    if len(self.differences) >= 32: break
            elif isinstance(left, list) and isinstance(right, list):
                if len(left) != len(right):
                    self.differences.append({'path': path, 'kind': 'length_changed', 'before': len(left), 'after': len(right)})
                for index in range(min(len(left), len(right))):
                    walk(left[index], right[index], path + [index])
                    if len(self.differences) >= 32: break
            else:
                self.differences.append({'path': path, 'kind': 'value_changed'})
        walk(expected, actual, [])
        super().__init__('whole fixed-state drift; differences=' + json.dumps(self.differences, sort_keys=True))


def parse_json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate query JSON key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('non-finite query JSON')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def success(result):
    need(type(result.get('rc')) is int and result['rc'] == 0 and
         result.get('stdout_truncated', False) is False and result.get('stderr_truncated', False) is False and
         not result.get('stderr', '').strip(), 'incomplete/uncertain state query')
    need(isinstance(result.get('stdout'), str), 'state query text absent')
    return result['stdout'].strip()


def objects(raw):
    result, fields, rich = {}, None, False
    for line in raw.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            name = line.split()[0]
            need(re.fullmatch(r'[A-Za-z0-9_-]+', name) and name not in result, 'invalid/duplicate object')
            fields = result[name] = {'rich rules': []}
            rich = False
        elif rich and line.strip().startswith('rule '):
            fields['rich rules'].append(' '.join(shlex.split(line.strip())))
        else:
            need(fields is not None and ':' in line and not rich, 'unknown object continuation')
            key, value = line.strip().split(':', 1)
            need(key not in fields or key == 'rich rules' and not fields[key], 'duplicate object field')
            if key == 'rich rules':
                need(not value.strip(), 'inline rich rule')
                rich = True
            else:
                fields[key] = value.strip()
    need(len(result) <= 128, 'object inventory bound')
    return result


def native_view(raw, mode, *, withdrawal_only=False, compatibility=None):
    """Retain all foreign objects/rule order; vary only the verified owned delta.

    Handles and counters are retained (counter values normalized). No generic
    recursive removal of CNI keys, set elements, timeouts or quota data.
    """
    need(mode in ('BASELINE', 'DISABLED', 'ACTIVE'), 'native mode differs')
    supplemental = {}
    if compatibility is not None:
        from cp1_cni_dataplane import covered
        need(set(compatibility) == {'iptables_save', 'ip6tables_save', 'ipsets'},
             'all compatibility streams required')
        proof = covered(raw, compatibility['iptables_save'], compatibility['ip6tables_save'], compatibility['ipsets'])
        supplemental = {key: proof[key] for key in ('xtables', 'kernel_ipsets')}
    native = copy.deepcopy(raw)
    need(isinstance(native.get('nftables'), list) and native['nftables'], 'native rules absent')
    remainder, own_sets, own_chains, own_rules, dispatch = [], [], [], [], []
    def normalize_counter(expression):
        if set(expression) == {'counter'} and isinstance(expression['counter'], dict):
            value = expression['counter']
            if set(value) == {'packets', 'bytes'}:
                need(all(type(n) is int and n >= 0 for n in value.values()), 'invalid rule counters')
                value.update(packets=0, bytes=0)
    for item in native['nftables']:
        need(isinstance(item, dict) and len(item) == 1, 'ambiguous native object')
        kind, value = next(iter(item.items()))
        need(isinstance(value, dict), 'invalid native object body')
        if kind == 'rule':
            need(isinstance(value.get('expr'), list), 'native expression missing')
            for expr in value['expr']:
                need(isinstance(expr, dict) and len(expr) == 1, 'invalid native expression')
                normalize_counter(expr)
                # xt statements omit extension payloads. An unchanged JSON
                # representation cannot prove unchanged ipset contents or
                # xtables match parameters. Do not grant using this digest.
                need(withdrawal_only or compatibility is not None or 'xt' not in expr,
                     'XT_COMPAT_COVERAGE_REQUIRED: nft JSON alone is incomplete')
        if kind == 'counter':
            need(all(type(value.get(k)) is int and value[k] >= 0 for k in ('packets', 'bytes')),
                 'invalid named counter')
            value.update(packets=0, bytes=0)
        owned = NAME in encoded(item).decode()
        if not owned:
            remainder.append(item)
            continue
        need(value.get('family') == 'inet' and value.get('table') == 'firewalld', 'owned name outside inet/firewalld')
        if kind == 'set' and value.get('name') == NAME:
            own_sets.append(value)
        elif kind == 'chain' and value.get('name') in [CHAIN, *SUBCHAINS]:
            own_chains.append(value)
        elif kind == 'rule' and value.get('chain') in [CHAIN, *SUBCHAINS]:
            own_rules.append(value)
        elif kind == 'rule' and value.get('chain') == 'filter_FORWARD_POLICIES':
            expr = [e for e in value['expr'] if 'counter' not in e]
            need(expr and expr[-1] == {'jump': {'target': CHAIN}}, 'foreign reference to owned policy')
            seen = set()
            for e in expr[:-1]:
                match = e.get('match', {})
                key = match.get('left', {}).get('meta', {}).get('key')
                need(set(e) == {'match'} and set(match) == {'left', 'op', 'right'} and
                     key in ('iifname', 'oifname') and key not in seen and match['op'] == '==' and
                     isinstance(match['right'], str) and re.fullmatch(r'[A-Za-z0-9_.:+*-]{1,16}', match['right']),
                     'unexpected owned policy dispatch')
                seen.add(key)
            dispatch.append(value)
        else:
            raise ValueError('unrecognized reference to owned name')
    if mode == 'BASELINE':
        need(not own_sets and not own_chains and not own_rules and not dispatch, 'owned object collision')
        return {'fixed': {'nftables': remainder, **supplemental}, 'entries': [], 'owned': []}
    need(len(own_sets) == 1, 'one native owned set required')
    value = own_sets[0]
    need(set(value) <= {'family', 'table', 'name', 'type', 'flags', 'handle', 'elem'} and
         value['type'] == ['ipv4_addr', 'ifname'] and value.get('flags') == ['interval'],
         'owned set type/options differ')
    found = []
    for item in value.get('elem', []):
        need(isinstance(item, dict) and set(item) == {'concat'} and len(item['concat']) == 2,
             'owned element is not a pair')
        address, interface = item['concat']
        if isinstance(address, dict):
            need(set(address) == {'prefix'} and set(address['prefix']) == {'addr', 'len'} and
                 type(address['prefix']['len']) is int and address['prefix']['len'] == 32,
                 'native owned prefix wider than /32')
            address = address['prefix']['addr']
        need(isinstance(address, str) and isinstance(interface, str), 'native pair type differs')
        found.append(address + '/32,' + interface)
    found = entries(found)
    # The owned set definition remains in the fixed digest. Only its validated
    # elements vary. Entire policy chains are validated before projecting out.
    definition = copy.deepcopy(value); definition['elem'] = []
    if mode == 'DISABLED':
        need(not own_chains and not own_rules and not dispatch, 'disabled policy still compiled/referenced')
    else:
        need(len(own_chains) == 6 and {r['name'] for r in own_chains} == {CHAIN, *SUBCHAINS},
             'owned chain inventory differs')
        need(all(set(r) <= {'family', 'table', 'name', 'handle'} for r in own_chains),
             'owned chain has hook/policy/unknown fields')
        base = [r for r in own_rules if r['chain'] == CHAIN]
        need([[e for e in r['expr'] if 'counter' not in e] for r in base] ==
             [[{'jump': {'target': name}}] for name in SUBCHAINS], 'owned chain control flow differs')
        accept = [r for r in own_rules if r['chain'] != CHAIN]
        need(len(accept) == 1 and accept[0]['chain'] == CHAIN + '_allow', 'unexpected owned allow/deny rules')
        expr = [e for e in accept[0]['expr'] if 'counter' not in e]
        family = {'match': {'left': {'meta': {'key': 'nfproto'}}, 'op': '==', 'right': 'ipv4'}}
        if expr and expr[0] == family:
            expr = expr[1:]
        matches = []
        for addr, interface in (('saddr', 'iifname'), ('daddr', 'oifname')):
            matches.append({'match': {'left': {'concat': [{'payload': {'protocol': 'ip', 'field': addr}},
                                                           {'meta': {'key': interface}}]},
                                      'op': '==', 'right': '@' + NAME}})
        need(expr in (matches + [{'accept': None}], list(reversed(matches)) + [{'accept': None}]),
             'owned ACCEPT must match both IP/interface pairs only')
        need(dispatch, 'owned policy unreachable')
        need(all(set(r) <= {'family', 'table', 'chain', 'handle', 'expr', 'comment'}
                 for r in own_rules + dispatch), 'unknown owned rule field')
    return {'fixed': {'nftables': remainder + [{'set': definition}], **supplemental}, 'entries': found,
            'owned': own_chains + own_rules + dispatch}


def policy_definition(value, disabled):
    need(value.get('target') == 'CONTINUE' and value.get('priority') == '-1' and
         value.get('ingress-zones') == value.get('egress-zones') == 'public' and
         value.get('disable') == ('yes' if disabled else 'no') and value.get('rich rules') == [RICH_RULE],
         'owned policy definition differs')
    empty = {'services', 'ports', 'protocols', 'icmp-blocks', 'forward-ports', 'source-ports'}
    need(empty <= set(value) and all(value[k] == '' for k in empty) and value.get('masquerade') == 'no',
         'owned policy has additional grant')
    need(set(value) == empty | {'target', 'priority', 'ingress-zones', 'egress-zones', 'disable',
                                'masquerade', 'rich rules'}, 'unknown owned policy field')


def snapshot_from_records(record, activation, units, clock, *, withdrawal=False, membership_capture=None, bootstrap=False):
    """Derive the Manager snapshot from an actual whole-state capture.

    activation contains independently reviewed baseline projections. It must
    be read from the staged, immutable activation receipt, never a CLI flag.
    """
    need(record.get('schema') == 'fds.cp1-cni-state-capture.v1' and record.get('complete') is True,
         'complete whole-state capture required')
    need(record.get('errors') == [], 'whole-state query errors remain')
    lease = activation['lease']
    projection = activation.get('projection_schema')
    need(projection in (None, 'fds.cp1-cni-pod-delta.v1'), 'unknown activation projection')
    need(type(bootstrap) is bool and not (bootstrap and withdrawal), 'ambiguous observer mode')
    if bootstrap:
        # Empty-set seeding must be possible before the watchdog is enabled.
        # The normal grant observer still requires a live independent guard.
        from cp1_cni_guard import validate_owned
        from cp1_cni_unit_files import inactive
        need(projection == 'fds.cp1-cni-pod-delta.v1', 'bootstrap requires peer-bound Pod projection')
        validate_owned(units, lease)
        need(units.get('links') == {}, 'bootstrap units must be unarmed')
        for properties in units['properties'].values():
            inactive(properties)
    delta_rows = None
    if projection:
        from cp1_cni_delta import capture_members
        need(membership_capture is not None, 'Pod delta requires a fresh local peer capture')
        delta_rows = capture_members(membership_capture, lease['boot_id'], clock)
    # A disabled grant can be emptied after expiry/guard failure. Whole
    # fixed-state and same-boot proof remain mandatory for that operation.
    guard = ({'run_id': lease['run_id'], 'armed': False, 'deadline': None}
             if withdrawal or bootstrap else verified_guard(units, lease, clock))
    boot(record['boot_id'])
    need(record['boot_id'] == lease['boot_id'], 'observer boot differs')
    for key in ('wall', 'boottime'):
        need(0 <= number(record['ended_' + key]) - number(record[key]) < 30, 'whole-state capture too slow')
        need(record['ended_' + key] <= number(clock[key]) and 0 <= clock[key] - record[key] < 30,
             'whole-state capture stale/future')
    q = {key: success(value) for key, value in record['queries'].items()}
    need(q['version'] == '2.4.3' and q['state'] == 'running' and q['selinux'] == 'Enforcing',
         'unsupported runtime environment')
    for key in ('version', 'state', 'selinux', 'configuration'):
        need(q[key] == q[key + '_after'], 'environment changed during observation: ' + key)
    need(q['boot'] == q['boot_after'] == record['boot_id'], 'query boot identity differs')
    config = [line.strip() for line in q['configuration'].splitlines()
              if line.strip() and not line.lstrip().startswith('#')]
    backend = [line.split('=', 1)[1].strip() for line in config if line.split('=', 1)[0].strip() == 'FirewallBackend']
    need(backend in ([], ['nftables']), 'nftables backend required')
    projected, actual_entries, enabled = {}, None, None
    for suffix in ('', '_after'):
        plane_data = {}
        for plane in ('runtime', 'permanent'):
            prefix = plane + '/'
            plane_data[plane] = {kind: objects(q[prefix + kind + suffix]) for kind in ('zones', 'policies')}
            sets = q[prefix + 'ipsets' + suffix].split()
            need(len(sets) == len(set(sets)) and len(sets) <= 128 and NAME in sets and
                 all(re.fullmatch(r'[A-Za-z0-9_-]+', s) for s in sets), 'ipset inventory differs')
            plane_data[plane]['ipsets'] = {s: q[prefix + 'ipsets/' + s + suffix] for s in sorted(sets)}
            own = plane_data[plane]['policies'].get(NAME, {})
            disabled = own.get('disable') == 'yes'
            policy_definition(own, disabled)
            if plane == 'permanent': need(disabled, 'permanent policy must be disabled')
            else: enabled_now = not disabled
            # Description is a Permanent-only CLI query in firewalld 2.4.3.
            # Runtime ownership additionally requires matching definitions and
            # exact compiled semantics; no unsupported Runtime description call.
            if plane == 'permanent':
                for kind in ('policy', 'ipset'):
                    need(q[prefix + kind + '-description' + suffix] == lease['run_id'], 'owned description differs')
            set_info = objects(q[prefix + 'ipsets/' + NAME + suffix])[NAME]
            need(set(set_info) == {'type', 'options', 'entries', 'rich rules'} and
                 set_info['type'] == 'hash:net,iface' and set_info['options'] == 'family=inet' and
                 set_info['rich rules'] == [], 'owned daemon set definition differs')
            values = entries(set_info['entries'].split())
            if plane == 'permanent': need(values == [], 'permanent membership must be empty')
            else: daemon_entries = values
            # Keep every foreign definition. Normalize only this owned dynamic
            # field and enabled flag, after validating both expected variants.
            neutral = copy.deepcopy(set_info); neutral['entries'] = ''
            plane_data[plane]['ipsets'][NAME] = neutral
            own['disable'] = 'yes'
            zones = plane_data[plane]['zones']
            need('public' in zones, 'public zone absent')
            for name, zone in zones.items():
                need('sources' in zone and zone['sources'] == '', 'source zone classification unsupported')
                if name != 'public':
                    for interface in zone.get('interfaces', '').split():
                        prefix_if = interface.rstrip('+*')
                        need(not ('cali'.startswith(prefix_if) or prefix_if.startswith('cali')),
                             'cali scope assigned to non-public zone')
            plane_data[plane]['direct'] = q[prefix + 'direct' + suffix]
        compatibility_keys = ('iptables_save', 'ip6tables_save', 'ipsets')
        compatibility = ({key: q[key + suffix] for key in compatibility_keys}
                         if any(key + suffix in q for key in compatibility_keys) else None)
        native = native_view(parse_json(q['native' + suffix]), 'ACTIVE' if enabled_now else 'DISABLED',
                             compatibility=compatibility)
        if projection:
            from cp1_cni_delta import project, FIXED_API
            need(compatibility is not None, 'Pod delta requires whole compatibility streams')
            api = {key: parse_json(q['api/' + key + suffix]) for key in (*FIXED_API,'pods','endpointslices')}
            fixed = native['fixed']
            projected_delta = project({'native': {'nftables': fixed['nftables']},
                                      'xtables': fixed['xtables'], 'kernel_ipsets': fixed['kernel_ipsets'],
                                      'opaque_expressions': 0}, api, delta_rows)
            native['fixed'] = {'nftables': projected_delta.pop('native')['nftables'], **projected_delta,
                               'projection_schema': projection}
        need(native['entries'] == daemon_entries, 'observer native/daemon entries differ')
        current = {'inventory': plane_data, 'native': native['fixed'],
                   'host': parse_json(q['host' + suffix]), 'protection': parse_json(q['protection' + suffix]),
                   'configuration': q['configuration'], 'version': q['version']}
        host = current['host']
        need(host.get('default_zone') == host.get('management_zone') == 'public' and
             isinstance(host.get('management_connection'), str) and
             re.fullmatch(r'[a-fA-F0-9-]{36}', host['management_connection']), 'management identity differs')
        if not same(current, activation['fixed']):
            raise StateMismatch(activation['fixed'], current)
        if projected:
            need(same(projected, current) and actual_entries == daemon_entries and enabled == enabled_now,
                 'state changed during paired observation')
        projected, actual_entries, enabled = current, daemon_entries, enabled_now
    need(fingerprint(projected) == lease['fixed_state_sha256'], 'activation fixed-state binding differs')
    if withdrawal:
        need(enabled is False, 'whole-state withdrawal requires disabled policy')
    if bootstrap:
        need(enabled is True and actual_entries == [], 'bootstrap requires an empty enabled owned set')
    need(guard['run_id'] == lease['run_id'], 'guard belongs to another run')
    result = {'boot_id': record['boot_id'], 'run_id': lease['run_id'], 'owned': True,
            'fixed_state_sha256': fingerprint(projected), 'policy_enabled': enabled,
            'native_entries': actual_entries, 'daemon_entries': actual_entries,
            'guard_armed': guard['armed'], 'guard_deadline': guard['deadline']}
    if projection and not withdrawal:
        from cp1_cni_membership import observation_from_capture
        result['eligible_observation'] = observation_from_capture(membership_capture,lease,result,clock,bootstrap=bootstrap)
    return result


def compatibility_command(node, argv, timeout):
    # Reuse the tested bounded spooling/multicall implementation. node.command
    # resolves symlinks into argv[0], which breaks iptables-save multicall.
    spec = importlib.util.spec_from_file_location('cp1_dataplane_collector', ROOT / 'scripts/collect-cp1-cni-dataplane.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.command(node, argv, timeout)


def collect(node, protection_reader, *, compatibility=False, delta_api=False, deadline=None, receipt_link=False):
    """Read local state twice with a 25-second total helper command budget.

    protection_reader must use the same bounded helper, returning the full
    previously accepted protection projection (including namespace policies).
    No caller-supplied owned/fixed/guard flags are queried here.
    """
    need(type(receipt_link) is bool, 'receipt linkage mode must be explicit')
    started = time.monotonic()
    end = min(started + 25, number(deadline)) if deadline is not None else started + 25
    need(end > started, 'whole observation deadline exhausted')
    node.COMMAND_STATE.update(count=0, timeouts=0, budget_exhausted=False, deadline=end)
    backend = node.BaseBackend('k8s-control01')
    record = {'schema': 'fds.cp1-cni-state-capture.v1', 'complete': False, 'errors': [], 'queries': {},
              'wall': time.time(), 'boottime': time.clock_gettime(time.CLOCK_BOOTTIME)}
    def read(key, argv):
        value = node.command(argv, timeout=5)
        record['queries'][key] = value
        return success(value)
    def internal(key, value):
        record['queries'][key] = {'rc': 0, 'stdout': encoded(value).decode(), 'stderr': '',
                                  'stdout_truncated': False, 'stderr_truncated': False}
    try:
        backend.identity()
        record['boot_id'] = read('boot', ['cat', '/proc/sys/kernel/random/boot_id'])
        for key, argv in (('version', ['firewall-cmd', '--version']), ('state', ['firewall-cmd', '--state']),
                          ('selinux', ['getenforce']), ('configuration', ['cat', '/etc/firewalld/firewalld.conf'])):
            read(key, argv)
        for suffix in ('', '_after'):
            # host/protection are preserved independently from dynamic policy.
            internal('host' + suffix, {'default_zone': read('default' + suffix, ['firewall-cmd', '--get-default-zone']),
                     'management_zone': read('management_zone' + suffix, ['firewall-cmd', '--get-zone-of-interface=ens160']),
                     'management_connection': read('management_connection' + suffix,
                          ['nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', 'ens160'])})
            # The pinned helper already reads this full snapshot for protection.
            # Do not query it again or reuse a snapshot from the prior sample.
            backend.cp1_cni_original_firewall = None
            internal('protection' + suffix, protection_reader(backend))
            if receipt_link:
                need(isinstance(backend.cp1_cni_original_firewall, dict), 'original firewall capture absent')
                internal('original/firewall' + suffix, backend.cp1_cni_original_firewall)
            if delta_api:
                spec = importlib.util.spec_from_file_location('delta_dataplane_queries', ROOT / 'scripts/collect-cp1-cni-dataplane.py')
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                from cp1_cni_delta import FIXED_API
                for key in (*FIXED_API,'pods','endpointslices'):
                    value = backend.kubectl(module.API_READS[key] + ['-o','json'])
                    record['queries']['api/'+key+suffix]=value
                    success(value)
            for plane in ('runtime', 'permanent'):
                prefix = ['firewall-cmd'] + (['--permanent'] if plane == 'permanent' else [])
                for kind in ('zones', 'policies'):
                    read(plane + '/' + kind + suffix, prefix + ['--list-all-' + kind])
                read(plane + '/direct' + suffix, prefix + ['--direct', '--get-all-rules'])
                names = read(plane + '/ipsets' + suffix, prefix + ['--get-ipsets']).split()
                need(len(names) <= 128 and len(names) == len(set(names)) and
                     all(re.fullmatch(r'[A-Za-z0-9_-]+', n) for n in names), 'unsafe ipset inventory')
                for name in sorted(names):
                    read(plane + '/ipsets/' + name + suffix, prefix + ['--info-ipset=' + name])
                if plane == 'permanent':
                    for kind in ('policy', 'ipset'):
                        present = (NAME in names if kind == 'ipset' else
                                   NAME in objects(success(record['queries'][plane + '/policies' + suffix])))
                        if present:
                            read(plane + '/' + kind + '-description' + suffix, prefix + ['--' + kind + '=' + NAME, '--get-description'])
            read('native' + suffix, ['nft', '-j', 'list', 'ruleset'])
            if compatibility:
                for key, argv in (('iptables_save', ['iptables-save', '-c']),
                                  ('ip6tables_save', ['ip6tables-save', '-c']), ('ipsets', ['ipset', 'save'])):
                    remaining = end - time.monotonic()
                    need(remaining > 0, 'whole observation budget exhausted')
                    value = compatibility_command(node, argv, min(5, remaining))
                    record['queries'][key + suffix] = value
                    success(value)
        for key, argv in (('version', ['firewall-cmd', '--version']), ('state', ['firewall-cmd', '--state']),
                          ('selinux', ['getenforce']), ('configuration', ['cat', '/etc/firewalld/firewalld.conf'])):
            read(key + '_after', argv)
        need(read('boot_after', ['cat', '/proc/sys/kernel/random/boot_id']) == record['boot_id'], 'boot changed')
        need(time.monotonic() < end, 'whole observation budget exhausted')
        record['complete'] = True
    except (ValueError, OSError, KeyError, TypeError) as error:
        record['errors'].append(str(error))
    finally:
        record.update(ended_wall=time.time(), ended_boottime=time.clock_gettime(time.CLOCK_BOOTTIME))
        backend.cache.cleanup(); node.COMMAND_STATE['deadline'] = None
    return record


def protection(backend):
    """Concrete datastore projection, including namespace-scoped protection.

    API resourceVersion/status counters are not policy. UID, labels, policy
    specs and the existing helper's Calico image/restart/Ready facts are kept.
    This does not claim that API data proves dataplane enforcement.
    """
    old = backend.cp1_state()
    for result in list(old['queries'].values()) + list(old['snapshot_queries'].values()):
        success(result)
    backend.cp1_cni_original_firewall = copy.deepcopy(old['stable']['firewall'])
    result = copy.deepcopy(old['stable']['protection'])
    for key, resource in (('network_policies', 'networkpolicies.networking.k8s.io'),
                          ('calico_network_policies', 'networkpolicies.crd.projectcalico.org'),
                          ('namespaces', 'namespaces')):
        args = ['get', resource] + ([] if key == 'namespaces' else ['-A']) + ['-o', 'json']
        raw = parse_json(success(backend.kubectl(args)))
        need(isinstance(raw.get('items'), list), 'protection list absent')
        rows = []
        for item in raw['items']:
            meta = item['metadata']
            rows.append({'namespace': meta.get('namespace'), 'name': meta['name'], 'uid': meta['uid'],
                         'labels': meta.get('labels', {}), 'spec': item.get('spec', {})})
        result[key] = sorted(rows, key=lambda r: (r['namespace'] or '', r['name']))
    return result


class LocalObserver:
    """Concrete composition of bounded local queries and unit attestation.

    Caller authenticates immutable activation/source staging and supplies
    nofollow, root-owned unit-file/symlink readers. No arbitrary observer
    boolean is accepted as evidence of ownership or guard state.
    """
    def __init__(self, node, activation, read_unit_file, read_unit_link, clock):
        need(hashlib.sha256(Path(node.__file__).read_bytes()).hexdigest() ==
             '93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa', 'observer helper source differs')
        self.node, self.activation = node, copy.deepcopy(activation)
        self.read_unit_file, self.read_unit_link, self.clock = read_unit_file, read_unit_link, clock
        self.evidence = None
        self.last_verified = None

    def unit_command(self, argv, timeout):
        result = self.node.command(argv, timeout=timeout)
        return {'rc': result['rc'], 'stdout': result['stdout'], 'stderr': result['stderr'],
                'truncated': result.get('stdout_truncated', False) is not False or result.get('stderr_truncated', False) is not False}

    def __call__(self):
        return self._observe()

    def bootstrap(self):
        """Seed only an empty grant; never prime the active mutation cache."""
        return self._observe(bootstrap=True)

    def _observe(self, *, bootstrap=False):
        self.last_verified = None
        delta = self.activation.get('projection_schema') == 'fds.cp1-cni-pod-delta.v1'
        capture = collect(self.node, protection, compatibility=True, delta_api=delta)
        # Preserve collected diagnostics even when a subsequent unit query
        # fails. A failed observation cannot reuse an earlier successful one.
        self.evidence = {'capture': capture}
        try:
            peer_capture = None
            if delta:
                spec=importlib.util.spec_from_file_location('live_membership_collector',ROOT/'scripts/collect-cp1-cni-membership.py')
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                peer_capture=module.collect(self.node)
                self.evidence['membership_capture']=peer_capture
            units = query_units(self.activation['lease'], self.unit_command, self.read_unit_file, self.read_unit_link,
                                include_links=not bootstrap)
            self.evidence['units'] = units
            clock = self.clock()
            self.evidence['clock'] = clock
            actual = snapshot_from_records(capture, self.activation, units, clock, membership_capture=peer_capture,bootstrap=bootstrap)
            if not bootstrap:
                self.last_verified = {'actual':copy.deepcopy(actual),'wall':capture['wall'],'boottime':capture['boottime']}
            return actual
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.evidence['verification_error'] = {'message': str(error), 'type': type(error).__name__,
                'differences': getattr(error, 'differences', []), 'authorizes_apply': False}
            raise

    def membership(self):
        # A full observation has just run in the mutation pre/post boundary.
        # Refresh CRI identity without repeating every firewall/API query.
        # The resulting age includes the older full-state capture, so this
        # cannot refresh the fixed-state/guard proof or extend its deadline.
        from cp1_cni_membership import observation_from_capture, observation
        need(self.activation.get('projection_schema')=='fds.cp1-cni-pod-delta.v1' and
             self.last_verified is not None, 'fresh whole state must precede peer refresh')
        spec=importlib.util.spec_from_file_location('mutation_membership_collector',ROOT/'scripts/collect-cp1-cni-membership.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        capture=module.collect(self.node)
        self.evidence['mutation_peer_capture']=capture
        current=self.clock()
        value=observation_from_capture(capture,self.activation['lease'],self.last_verified['actual'],current)
        for key in ('wall','boottime'):value[key]=min(value[key],self.last_verified[key])
        observation(value,self.activation['lease'],current)
        return value

    def recovery(self):
        self.last_verified = None
        from cp1_cni_withdrawal import collect_owned, owned_snapshot
        capture = collect_owned(self.node)
        self.evidence = {'owned_withdrawal_capture': capture}
        return owned_snapshot(capture, self.activation, self.clock())

    def withdrawal(self):
        self.last_verified = None
        delta = self.activation.get('projection_schema') == 'fds.cp1-cni-pod-delta.v1'
        capture = collect(self.node, protection, compatibility=True, delta_api=delta)
        self.evidence = {'withdrawal_capture': capture}
        peer_capture=None
        if delta:
            spec=importlib.util.spec_from_file_location('withdrawal_membership_collector',ROOT/'scripts/collect-cp1-cni-membership.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            peer_capture=module.collect(self.node)
            self.evidence['membership_capture']=peer_capture
        return snapshot_from_records(capture, self.activation, None, self.clock(), withdrawal=True, membership_capture=peer_capture)
