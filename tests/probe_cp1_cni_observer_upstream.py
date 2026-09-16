#!/usr/bin/env python3
"""Check observer parsing against verbatim, hash-pinned CLI formatters.

Only selected upstream formatter functions are executed, with model settings.
No firewalld daemon, D-Bus client, systemd or kernel command is run.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cp1_cni_observer as observer

SHA256 = '62f74246de2419030e2ca1fab43f1caf46814cad8abff6895a94173528edd8ec'
BLOB = '8d84f4222fc2e27cb2184fe56c0452fd450a9e2a'
METHODS = {'print_zone_policy_info', 'print_zone_info', 'print_policy_info', 'print_ipset_info'}


def probe(path):
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA256, 'upstream source bytes differ'
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == BLOB
    source = ast.parse(raw)
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'FirewallCommand')
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    assert {n.name for n in cls.body} == METHODS
    context = {}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), context)
    output = []
    formatter = context['FirewallCommand']()
    formatter.verbose = False
    formatter.print_msg = output.append
    results = []

    def settings(**values):
        return SimpleNamespace(**{'get' + key: (lambda value=value: value) for key, value in values.items()})
    common = dict(Target='CONTINUE', Services=[], Ports=[], Protocols=[], Masquerade=False,
                  ForwardPorts=[], SourcePorts=[], IcmpBlocks=[], Description='owned-run', Short='',
                  RichRules=['rule family="ipv4" source ipset="' + observer.NAME +
                             '" destination ipset="' + observer.NAME + '" accept'])
    for disabled in (False, True):
        output.clear()
        formatter.print_policy_info(observer.NAME, settings(**common, IngressZones=['public'],
                                    EgressZones=['public'], Priority=-1, Disable=disabled),
                                    active_policies=[] if disabled else [observer.NAME])
        parsed = observer.objects('\n'.join(output))[observer.NAME]
        observer.policy_definition(parsed, disabled)
        results.append({'case': 'policy-disabled' if disabled else 'policy-active', 'pass': True,
                        'upstream_output': '\n'.join(output)})
    for entries in ([], ['10.244.246.80/32,cali0123456789a']):
        output.clear()
        formatter.print_ipset_info(observer.NAME, settings(Type='hash:net,iface', Options={'family': 'inet'},
                                                          Entries=entries, Description='owned-run', Short=''))
        parsed = observer.objects('\n'.join(output))[observer.NAME]
        assert parsed == {'type': 'hash:net,iface', 'options': 'family=inet',
                          'entries': ' '.join(entries), 'rich rules': []}
        results.append({'case': 'set-populated' if entries else 'set-empty', 'pass': True,
                        'upstream_output': '\n'.join(output)})
    output.clear()
    formatter.print_zone_info('public', settings(**dict(common, Target='default', RichRules=[]),
                              IcmpBlockInversion=False, Interfaces=['ens160'], Sources=[], Forward=True,
                              IngressPriority=0, EgressPriority=0), default_zone='public', active_zones=['public'])
    parsed = observer.objects('\n'.join(output))['public']
    assert parsed['interfaces'] == 'ens160' and parsed['sources'] == '' and parsed['forward'] == 'yes'
    results.append({'case': 'public-active-default', 'pass': True, 'upstream_output': '\n'.join(output)})
    return {'status': 'PINNED_UPSTREAM_FORMATTER_PROBE_PASS', 'cases': results,
            'source': 'https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/command.py',
            'upstream_git_blob': BLOB, 'upstream_sha256': SHA256,
            'functions_executed': sorted(METHODS), 'settings_are_test_doubles': True,
            'daemon_tested': False, 'kernel_tested': False, 'systemd_tested': False,
            'actual_cp1_tested': False, 'authorizes_deployment': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command-source', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.command_source), sort_keys=True, indent=2))
