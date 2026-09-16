#!/usr/bin/env python3
"""Replay an operator-supplied actual capture offline; no host commands.

Raw target data is not committed. Supply the exact verified CAPTURE.json and
its SHA256. Counterexamples are in-memory mutations, never runtime tests.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cp1_cni_dataplane as d


def probe(record):
    base = d.assess_capture(record)
    checks = []
    def check(name, edit, change_expected=True):
        changed = copy.deepcopy(record); edit(changed)
        try:
            result = d.assess_capture(changed)
            differs = result['fixed_dataplane_sha256'] != base['fixed_dataplane_sha256']
            outcome = 'DIFFERENT_FIXED_STATE' if differs else 'SAME_FIXED_STATE'
        except (ValueError, KeyError, TypeError) as error:
            differs = True; outcome = 'REJECTED: ' + str(error)
        d.need(differs is change_expected, 'capture regression failed: ' + name)
        checks.append({'name': name, 'result': 'PASS', 'outcome': outcome})

    def replace(r, key, old, new, paired=True):
        for suffix in ('', '_after') if paired else ('_after',):
            value = r['queries'][key + suffix]
            d.need(old in value['stdout'], 'counterexample anchor absent: ' + key)
            value['stdout'] = value['stdout'].replace(old, new, 1)

    def native_edit(r, edit):
        for suffix in ('', '_after'):
            value = r['queries']['native_json' + suffix]
            raw = d.parse_json(value['stdout']); edit(raw); value['stdout'] = json.dumps(raw)

    check('hidden_set_broadened_both_samples', lambda r: replace(r, 'ipsets',
          'add cali40network-ip-pools 10.244.0.0/16', 'add cali40network-ip-pools 0.0.0.0/0'))
    check('hidden_set_changed_between_samples', lambda r: replace(r, 'ipsets',
          'add cali40network-ip-pools 10.244.0.0/16', 'add cali40network-ip-pools 0.0.0.0/0', False))
    check('hidden_set_direction_changed', lambda r: replace(r, 'iptables_save',
          '--match-set cali40masq-ipam-pools src', '--match-set cali40masq-ipam-pools dst'))
    check('hidden_conntrack_scope_changed', lambda r: replace(r, 'iptables_save',
          '--ctstate RELATED,ESTABLISHED', '--ctstate NEW,RELATED,ESTABLISHED'))
    check('hidden_dnat_destination_changed', lambda r: replace(r, 'iptables_save',
          '--to-destination 10.244.246.77:53', '--to-destination 10.244.246.80:53'))
    check('missing_kernel_sets', lambda r: r['queries'].pop('ipsets_after'))
    check('warning_in_authoritative_save', lambda r: r['queries']['iptables_save'].update(stderr='incomplete dump'))
    check('truncated_native', lambda r: r['queries']['native_json'].update(stdout_truncated=True))
    check('boolean_exit_status', lambda r: r['queries']['ip6tables_save'].update(rc=True))
    def arp(raw):
        rule = next(x['rule'] for x in raw['nftables'] if x.get('rule', {}).get('chain') == 'filter-cali-arp-calic2af0f77fd7')
        rule['expr'][2]['match']['right'] = '10.244.246.80'
    check('arp_guard_address_changed', lambda r: native_edit(r, arp))
    def verdict(raw):
        rule = next(x['rule'] for x in raw['nftables'] if x.get('rule', {}).get('chain') == 'cali-from-wl-dispatch' and
                    {'drop': None} in x['rule']['expr'])
        rule['expr'][-1] = {'accept': None}
    check('native_verdict_disagrees_with_save', lambda r: native_edit(r, verdict))
    def api(r):
        result = r['queries']['endpointslices_after']; raw = d.parse_json(result['stdout'])
        raw['items'][0]['endpoints'][0]['addresses'] = ['10.1.93.103']; result['stdout'] = json.dumps(raw)
    check('api_context_raced', api)
    def counters(raw):
        for item in raw['nftables']:
            for expr in item.get('rule', {}).get('expr', []):
                if isinstance(expr.get('counter'), dict):
                    expr['counter']['packets'] += 50; expr['counter']['bytes'] += 500
    check('native_counters_only', lambda r: native_edit(r, counters), False)
    return {'assessment': base, 'counterexamples': checks,
            'test_kind': 'OFFLINE_REPLAY_AND_IN_MEMORY_MUTATIONS', 'actual_mutations': False,
            'runtime_pod_replacement_tested': False, 'authorizes_apply': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', required=True); p.add_argument('--sha256', required=True)
    args = p.parse_args()
    with Path(args.capture).open('rb') as stream: raw = stream.read(16 * 1024 * 1024 + 1)
    d.need(len(raw) <= 16 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == args.sha256,
           'capture size/hash differs')
    result = probe(d.parse_json(raw))
    result['capture_sha256'] = args.sha256
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__': main()
