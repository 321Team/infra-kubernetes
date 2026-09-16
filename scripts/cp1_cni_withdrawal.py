"""Narrow owned-policy withdrawal proof; no grant or full-recovery authority.

No Kubernetes/CRI/guard/lease-liveness dependency: losing these inputs is a
reason to withdraw. The caller still verifies immutable activation provenance.
Query only inet/firewalld: nft references are table-local, and unrelated
iptables compatibility warnings must not stop withdrawal of this owned grant.
"""
import time

from cp1_cni_membership import need, entries, boot, number, same, validate_lease
from cp1_cni_observer import NAME, objects, parse_json, success, native_view, policy_definition


def owned_snapshot(record, activation, clock):
    lease = validate_lease(activation['lease'])
    need(record.get('schema') == 'fds.cp1-cni-owned-withdrawal.v1' and
         record.get('complete') is True and record.get('errors') == [], 'owned capture incomplete')
    boot(clock['boot_id'])
    for key in ('wall', 'boottime'):
        start, end, now = (number(record[key]), number(record['ended_' + key]), number(clock[key]))
        need(start <= end <= now and 0 <= now - start < 30, 'owned capture stale/future')
    q = {k: success(v) for k, v in record['queries'].items()}
    need(q['boot'] == q['boot_after'] == clock['boot_id'], 'owned capture boot changed')
    # Check definitions and Permanent descriptions together, twice. A run ID
    # in untrusted JSON alone is never ownership proof.
    samples = []
    for suffix in ('', '_after'):
        need(q['version' + suffix] == '2.4.3' and q['state' + suffix] == 'running',
             'owned daemon environment differs')
        for kind in ('policy', 'ipset'):
            need(q[kind + '-description' + suffix] == lease['run_id'], 'owned description differs')
        found, enabled = None, None
        for plane in ('runtime', 'permanent'):
            policies = objects(q[plane + '/policy' + suffix])
            need(set(policies) == {NAME}, 'owned policy inventory differs')
            disabled = policies[NAME].get('disable') == 'yes'
            policy_definition(policies[NAME], disabled)
            info = objects(q[plane + '/ipset' + suffix])
            need(set(info) == {NAME}, 'owned set inventory differs')
            value = info[NAME]
            need(set(value) == {'type', 'options', 'entries', 'rich rules'} and
                 value['type'] == 'hash:net,iface' and value['options'] == 'family=inet' and
                 value['rich rules'] == [], 'owned set definition differs')
            elements = entries(value['entries'].split())
            if plane == 'permanent':
                need(disabled and not elements, 'permanent owned grant must be disabled/empty')
            else:
                enabled, found = not disabled, elements
        raw = parse_json(q['native' + suffix])
        need(isinstance(raw.get('nftables'), list), 'owned native objects absent')
        # A table-scoped query must not smuggle in another table/family.
        for item in raw.get('nftables', []):
            need(isinstance(item, dict) and len(item) == 1, 'owned native object invalid')
            kind, value = next(iter(item.items()))
            need(isinstance(value, dict), 'owned native body invalid')
            if kind == 'metainfo':
                continue
            need(value.get('family') == 'inet' and
                 value.get('name' if kind == 'table' else 'table') == 'firewalld',
                 'owned query contains a foreign table')
        native = native_view(raw, 'ACTIVE' if enabled else 'DISABLED', withdrawal_only=True)
        need(native['entries'] == found, 'owned native/daemon elements differ')
        samples.append({'policy_enabled': enabled, 'entries': found, 'owned': native['owned']})
    need(same(samples[0], samples[1]), 'owned state changed during capture')
    return {'verification_scope': 'OWNED_WITHDRAWAL_ONLY', 'run_id': lease['run_id'],
            'boot_id': clock['boot_id'], 'owned': True, 'policy_enabled': samples[0]['policy_enabled'],
            'native_entries': samples[0]['entries'], 'daemon_entries': samples[0]['entries'],
            'fixed_state_sha256': None, 'guard_armed': False, 'guard_deadline': None,
            'full_recovery_complete': False, 'authorizes_apply': False}


def collect_owned(node):
    """Bounded read-only paired sample with no cluster API or unit dependency."""
    record = {'schema': 'fds.cp1-cni-owned-withdrawal.v1', 'complete': False, 'errors': [],
              'queries': {}, 'wall': time.time(), 'boottime': time.clock_gettime(time.CLOCK_BOOTTIME)}
    previous = dict(node.COMMAND_STATE)
    node.COMMAND_STATE.update(count=0, timeouts=0, budget_exhausted=False, deadline=time.monotonic() + 20)
    def read(key, argv):
        record['queries'][key] = node.command(argv, timeout=3)
        success(record['queries'][key])
    try:
        for suffix in ('', '_after'):
            read('boot' + suffix, ['cat', '/proc/sys/kernel/random/boot_id'])
            read('version' + suffix, ['firewall-cmd', '--version'])
            read('state' + suffix, ['firewall-cmd', '--state'])
            for plane in ('runtime', 'permanent'):
                prefix = ['firewall-cmd'] + (['--permanent'] if plane == 'permanent' else [])
                for kind in ('policy', 'ipset'):
                    read(plane + '/' + kind + suffix, prefix + ['--info-' + kind + '=' + NAME])
                    if plane == 'permanent':
                        read(kind + '-description' + suffix, prefix + ['--' + kind + '=' + NAME, '--get-description'])
            read('native' + suffix, ['nft', '-j', 'list', 'table', 'inet', 'firewalld'])
        record['complete'] = True
    except (ValueError, OSError, KeyError, TypeError) as error:
        record['errors'].append(str(error))
    finally:
        record.update(ended_wall=time.time(), ended_boottime=time.clock_gettime(time.CLOCK_BOOTTIME))
        node.COMMAND_STATE.clear(); node.COMMAND_STATE.update(previous)
    return record
