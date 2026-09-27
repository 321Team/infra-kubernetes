"""Offline firewalld 2.4.3 parser/config/formatter contract probe.

Full upstream XML readers, writers and check_config_dict run unmodified.
Only Settings data classes and CLI formatter methods are AST-selected; the
DBus exception decorator is an identity function. No daemon/kernel claim.
Download the exact listed upstream sources separately; this script pins bytes.
"""
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

UPSTREAM = {'config/services/dhcpv6-client.xml': '34d4b9a6c6345b4d55a3c9665170eacb8ca861c93100c4702bd37ab1fc29255f',
 'config/services/ssh.xml': '89ead377d6d529a217c7c31a623232d257d2f2763be3b8c35c31601068213ac8',
 'src/firewall/__init__.py': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
 'src/firewall/client.py': '5fde6a95d034a185f9dc0227b33ee1da14ae65fe4fb9f327334b646d9a31a4ca',
 'src/firewall/command.py': 'd38d88eb5b5bd5a434637f409e44d76a849c65884dd6b7f17ea183245a7371fa',
 'src/firewall/config/__init__.py.in': '97d68269ed0448b1d270ca51c07fcd608f847c3a981f66bf92ae168f760d7cf1',
 'src/firewall/config/dbus.py': '6ed2dcb2c0ae5d6d3829efda41a423b887238b406d2b54b65458e2d28e418686',
 'src/firewall/core/base.py': 'a3de6f03effff506fd9715da65225d94a4bb361a59ef1e7d4fa7e795fa914982',
 'src/firewall/core/icmp.py': 'e9119ec0cc18ced1f3c3de813fd18e969dbdf6d8ea7865def40467d716367f7f',
 'src/firewall/core/io/io_object.py': 'b2f8ae8c96198c785df7a71e6072f1f4610110b742db8ddf3e6a0520470719ae',
 'src/firewall/core/io/ipset.py': '6e7b399ad49a35c0e214d87fcfeb670ddc1d7fd493352482b36d7705a360c053',
 'src/firewall/core/io/policy.py': 'd279a93208cffbcdbc5e8405d0910a3dfa12eb7d711484fd97a899038cfd4916',
 'src/firewall/core/io/service.py': '90f2ab5a49c6d3f4f7f5fe9b2154d2a093cbde2f7bf54068b9039cfcd973a939',
 'src/firewall/core/io/zone.py': '9c6b165d452b9b929b10b26f5cf594a2de63acc075a2e777d88af693a90b5fc5',
 'src/firewall/core/ipset.py': 'a0e75786031135f72f74b1e54cd47ec83a2220bcb940b60fe5ceed3e01b4dcb3',
 'src/firewall/core/logger.py': '024c37fcc06eddc21b1661214fe3918795fcbba58640dff4a8c4bd2fb3a27d7d',
 'src/firewall/core/prog.py': 'c21199fe69444e0ab6844bc63dde500bdc57844c5374c1f429267bc97a8159e9',
 'src/firewall/core/rich.py': 'a7c6f10632122fbb3b10eab8219b93452229d98a173f1eacfca01182953bc4f6',
 'src/firewall/errors.py': 'ed8ad97121b70abf09a7197f7460d2081c2b30eb5cfc484c1faa9e879d42c856',
 'src/firewall/functions.py': 'effb5c89fede743e7350e2c32e61f5f23b7f1a907d1d061462880869d9d993e0'}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def selected_class(path, name, context, methods=None):
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    if methods is not None:
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
        need({n.name for n in cls.body} == set(methods), 'upstream formatter methods differ')
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), context)
    return context[name]


def probe(upstream):
    for name, sha in UPSTREAM.items():
        need(hashlib.sha256((upstream / name).read_bytes()).hexdigest() == sha, 'upstream differs: ' + name)
    # Equivalent to the single configure substitution needed by this offline
    # import. Other @...@ strings are unused; no executable is invoked.
    config = upstream / 'src/firewall/config/__init__.py.in'
    with tempfile.TemporaryDirectory(prefix='cp1-xml-probe-') as tmp:
        root = Path(tmp)
        for name in UPSTREAM:
            if name.startswith('src/'):
                out = root / name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes((upstream / name).read_bytes())
        (root / 'src/firewall/config/__init__.py').write_text(config.read_text().replace('@PACKAGE_VERSION@', '2.4.3'))
        sys.path.insert(0, str(root / 'src'))
        from firewall.core.io.zone import zone_reader, zone_writer
        from firewall.core.io.policy import policy_reader, policy_writer
        from firewall.core.io.ipset import ipset_reader, ipset_writer
        from firewall.core.io.service import service_reader
        from firewall.core import base as constants
        from firewall.errors import FirewallError
        from test_cp1_minimal_change import baseline, m, BUNDLE

        context = {k: getattr(constants, k) for k in ('DEFAULT_ZONE_TARGET', 'DEFAULT_POLICY_TARGET',
                   'DEFAULT_POLICY_PRIORITY', 'DEFAULT_ZONE_PRIORITY')}
        context['handle_exceptions'] = lambda fn: fn
        settings_classes = {kind: selected_class(upstream / 'src/firewall/client.py', name, context)
                            for kind, name in (('zones', 'FirewallClientZoneSettings'),
                                               ('policies', 'FirewallClientPolicySettings'),
                                               ('ipsets', 'FirewallClientIPSetSettings'))}
        formatter = selected_class(upstream / 'src/firewall/command.py', 'FirewallCommand', {},
                                   ('print_zone_policy_info', 'print_ipset_info'))()
        formatter.verbose = False
        checks, outputs = [], {}
        readers = dict(zones=zone_reader, policies=policy_reader, ipsets=ipset_reader)
        writers = dict(zones=zone_writer, policies=policy_writer, ipsets=ipset_writer)
        base, _ = baseline()
        model = m.expected_objects(base)
        all_objects = dict(zones={}, policies={}, ipsets={}, services={}, icmptypes={}, conf={'FirewallBackend': 'nftables'})
        for name in ('ssh', 'dhcpv6-client'):
            all_objects['services'][name] = service_reader(name + '.xml', str(upstream / 'config/services'))

        def validate(objects):
            for kind in readers:
                for obj in objects[kind].values():
                    obj.check_config_dict(obj.export_config_dict(), objects)
                    if kind == 'ipsets':
                        for entry in obj.entries:
                            obj.check_entry(entry, obj.options, obj.type)

        def format_object(kind, name, obj):
            data = list(obj.export_config()) if kind == 'ipsets' else obj.export_config_dict()
            settings = settings_classes[kind](data)
            lines = []
            formatter.print_msg = lines.append
            if kind == 'ipsets':
                formatter.print_ipset_info(name, settings)
            else:
                formatter.print_zone_policy_info(name, settings, isPolicy=kind == 'policies')
            return '\n'.join(lines)

        with patch.object(subprocess, 'Popen', side_effect=AssertionError('host process forbidden')):
            for name, xml in m.documents(base).items():
                p = root / 'generated' / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(xml)
                kind = p.parent.name
                all_objects[kind][p.stem] = readers[kind](p.name, str(p.parent))
            validate(all_objects)
            for kind in readers:
                for name, obj in all_objects[kind].items():
                    target = root / 'roundtrip' / kind
                    target.mkdir(parents=True, exist_ok=True)
                    writers[kind](obj, str(target))
                    again = readers[kind](name + '.xml', str(target))
                    need(again.export_config_dict() == obj.export_config_dict(), 'roundtrip differs: ' + name)
                    checks.append('parser/config/roundtrip: ' + name)
                    raw = format_object(kind, name, obj)
                    outputs[name] = raw
                    need(m.parse_info(raw, name) == model[kind][name], 'actual formatted state differs: ' + name)
                    checks.append('CLI formatter/state comparison: ' + name)

            zone = all_objects['zones'][m.ZONE]
            need(zone.forward is False and zone.interfaces == [] and zone.sources == [], 'staging is bound or forwards')
            checks.append('staged zone unbound, no sources, forward=false')
            bound = copy.deepcopy(all_objects)
            bound['zones'][m.ZONE].interfaces = ['cali+']
            validate(bound)
            expected = copy.deepcopy(model['zones'][m.ZONE])
            expected['fields']['interfaces'] = 'cali+'
            need(m.parse_info(format_object('zones', m.ZONE, bound['zones'][m.ZONE]), m.ZONE) == expected,
                 'bound zone state differs')
            checks.append('bound cali+ accepted by config validator and state comparison')

            negatives = []
            for kind, name in (('services', 'ssh'), ('ipsets', m.SETS[0]), ('ipsets', m.SETS[1]), ('zones', m.ZONE)):
                changed = copy.deepcopy(all_objects)
                del changed[kind][name]
                negatives.append(('missing dependency ' + kind + '/' + name, changed))
            changed = copy.deepcopy(all_objects)
            changed['ipsets'][m.SETS[0]].entries = ['10.1.1.1/99,ens160']
            negatives.append(('invalid ipset network', changed))
            changed = copy.deepcopy(bound)
            other = copy.deepcopy(changed['zones'][m.ZONE]); other.name = 'foreign'
            changed['zones']['foreign'] = other
            negatives.append(('same interface in two zones', changed))
            for name, changed in negatives:
                try:
                    validate(changed)
                except FirewallError as error:
                    checks.append('rejected: ' + name)
                else:
                    raise ValueError('invalid config accepted: ' + name)

        return {'schema': 'fds.cp1-minimal-xml-validation.v1', 'result': 'PASS', 'checks_passed': len(checks),
                'checks': checks, 'runtime_bundle_sha256': BUNDLE, 'upstream_tag': 'v2.4.3',
                'upstream_sha256': UPSTREAM, 'actual_formatted_objects': outputs,
                'limitations': ['XML parsers and config checks executed locally; CP1 host was not accessed',
                                'Settings/formatter AST selection bypasses DBus exception decorator only',
                                'No DBus daemon, nft kernel, systemd timer, packet flow or acceptance execution']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('upstream', type=Path, help='firewalld v2.4.3 source root')
    print(json.dumps(probe(parser.parse_args().upstream.resolve()), ensure_ascii=False, sort_keys=True, indent=2))
