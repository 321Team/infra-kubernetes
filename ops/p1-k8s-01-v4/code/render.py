#!/usr/bin/env python3
"""Offline policy renderer. Never contacts or changes a cluster."""
import argparse
import hashlib
import ipaddress
import json
import re
from pathlib import Path

import yaml

API = 'crd.projectcalico.org/v1'
LABEL = 'fds.example/bootstrap'
BUNDLE = 'p1-k8s-01-v4'
ORDER = ['k8s-worker01', 'k8s-worker02', 'k8s-worker03',
         'k8s-control01', 'k8s-control02', 'k8s-control03']
IPS = dict(zip(ORDER, ['10.1.93.105', '10.1.93.107', '10.1.93.108',
                       '10.1.93.102', '10.1.93.103', '10.1.93.104']))
NODES = [x + '/32' for x in IPS.values()]
CP = [IPS[x] + '/32' for x in ORDER if 'control' in x]
DNS = ['10.1.93.52/32', '10.1.93.53/32']
SSH = '10.250.10.10/32'
LB = '10.1.93.109/32'


def need(ok, message):
    if not ok:
        raise ValueError(message)


def hostnet(value):
    net = ipaddress.ip_network(value, strict=True)
    need(net.version == 4 and net.prefixlen == 32, 'IPv4 /32 required: ' + value)
    need(not net.network_address.is_loopback and not net.network_address.is_multicast
         and not net.network_address.is_unspecified, 'invalid remote host: ' + value)
    return value


def evidence(value, base):
    need(isinstance(value, dict) and set(value) == {'path', 'sha256'},
         'evidence requires path and sha256')
    p = Path(value['path'])
    p = p if p.is_absolute() else base / p
    need(p.is_file(), 'missing measured/decision evidence: ' + str(p))
    need(hashlib.sha256(p.read_bytes()).hexdigest() == value['sha256'],
         'evidence hash mismatch: ' + str(p))


def validate(c, base, check_evidence=True):
    need(c['bundle'] == BUNDLE, 'wrong bundle')
    need(re.fullmatch(r'[A-Za-z0-9_-]{4,64}', c.get('attempt_id') or '') is not None,
         'unique attempt_id required')
    need(re.fullmatch(r'[0-9a-f-]{36}', c['cluster_uid'] or '') is not None,
         'measure kube-system namespace UID first')
    need(set(c['nodes']) == set(ORDER), 'exact six nodes required')
    need(c['dataplane'] == 'iptables', 'this code targets verified iptables dataplane only')
    need(c['calico_version'] == 'v3.32.1', 'review changed Calico version first')
    need(c['system_images']['mode'] in ('preloaded', 'approved_registry'), 'select image supply mode')
    need(re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', c['probe_image'] or '') is not None,
         'probe image must be an approved immutable digest with Python 3')
    need(len(c['probe_nodes']) == 3 and set(c['probe_nodes']) == set(ORDER[:3]),
         'one probe on each of the three Workers is required')
    need(isinstance(c['mtu'], int) and 576 <= c['mtu'] <= 1450,
         'record measured MTU; 1450 is a candidate, not a measurement')
    if check_evidence:
        for key in ('transition', 'system_images', 'forward_boundary', 'policy_overlap'):
            evidence(c['evidence'][key], base)
    for name in ORDER:
        n = c['nodes'][name]
        need(n['ip'] == IPS[name], 'inventory mismatch: ' + name)
        need(re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', n['interface'] or '') is not None
             and n['interface'] != 'lo', 'measure named physical interface: ' + name)
        need(n['ntp_nets'], 'approved measured NTP destinations required: ' + name)
        for net in n['ntp_nets'] + n['registry_nets']:
            hostnet(net)
        if c['system_images']['mode'] == 'approved_registry':
            need(n['registry_nets'], 'registry endpoints missing: ' + name)
        else:
            need(not n['registry_nets'], 'preloaded mode must not add registry egress')
        for field in ('api_ingress_node_sources', 'api_egress_nets'):
            for net in n[field]:
                need(hostnet(net) in NODES, 'extra API host must be a known node')
        need(not n['api_ingress_node_sources'] or 'control' in name, 'Worker API ingress forbidden')
        need(all(x in CP for x in n['api_egress_nets']), 'API destination must be a CP')
        # Workload-to-API source must be a reviewed kube-system endpoint selector.
        for selector in n['api_ingress_workload_selectors']:
            need('control' in name and isinstance(selector, str) and selector.strip()
                 and selector.strip() != 'all()', 'unrestricted workload API selector forbidden')
        if check_evidence:
            for key in ('felix_effective', 'source_route', 'firewall_checkpoint',
                        'recovery', 'api_paths', 'ntp', 'dataplane'):
                evidence(n['evidence'][key], base)
    # Host API flows must be allowed in both directions, not only on the client.
    for source in ORDER:
        for dest in c['nodes'][source]['api_egress_nets']:
            target = next(k for k, v in IPS.items() if v + '/32' == dest)
            need(IPS[source] + '/32' in c['nodes'][target]['api_ingress_node_sources'],
                 'missing reciprocal CP ingress for ' + source + ' -> ' + target)


def rule(proto, nets, ports, direction):
    peer = 'source' if direction == 'ingress' else 'destination'
    r = {'action': 'Allow', 'protocol': proto, peer: {'nets': nets},
         'destination': {'ports': ports}}
    if peer == 'destination':
        r['destination']['nets'] = nets
    return r


def obj(kind, name, spec, labels=None):
    m = {'name': name, 'labels': {'fds.example/package': BUNDLE}}
    if labels:
        m['labels'].update(labels)
    return {'apiVersion': API, 'kind': kind, 'metadata': m, 'spec': spec}


def policies(c, name):
    n, cp = c['nodes'][name], 'control' in name
    ing = [rule('TCP', [SSH], [22], 'ingress'),
           rule('TCP', CP, [10250], 'ingress'),
           rule('UDP', NODES, [4789], 'ingress')]
    out = [rule('TCP', [LB], [6443], 'egress'),
           rule('UDP', NODES, [4789], 'egress'),
           rule('UDP', DNS, [53], 'egress'),
           rule('TCP', DNS, [53], 'egress'),
           rule('UDP', n['ntp_nets'], [123], 'egress')]
    if cp:
        ing += [rule('TCP', [LB] + n['api_ingress_node_sources'], [6443], 'ingress'),
                rule('TCP', CP, [2379, 2380], 'ingress')]
        out += [rule('TCP', CP, [2379, 2380], 'egress'),
                rule('TCP', NODES, [10250], 'egress')]
        for selector in n['api_ingress_workload_selectors']:
            ing.append({'action': 'Allow', 'protocol': 'TCP', 'source': {
                'namespaceSelector': 'projectcalico.org/name == "kube-system"',
                'selector': selector}, 'destination': {'ports': [6443]}})
    else:
        out.append(rule('TCP', ['10.1.93.54/32'], [443], 'egress'))
    if n['api_egress_nets']:
        out.append(rule('TCP', n['api_egress_nets'], [6443], 'egress'))
    if n['registry_nets']:
        out.append(rule('TCP', n['registry_nets'], [443], 'egress'))
    # Explicit termination; no dependence on fall-through to another GNP.
    ing.append({'action': 'Deny'})
    out.append({'action': 'Deny'})
    gnp = obj('GlobalNetworkPolicy', 'fds-k8s-host-' + name, {
        'order': 100, 'selector': f'{LABEL} == "{BUNDLE}" && fds.example/node == "{name}"',
        'applyOnForward': False, 'types': ['Ingress', 'Egress'], 'ingress': ing, 'egress': out})
    hep = obj('HostEndpoint', 'fds-k8s-' + name, {
        'node': name, 'interfaceName': n['interface'], 'expectedIPs': [n['ip']]},
        {LABEL: BUNDLE, 'fds.example/node': name,
         'fds.example/role': 'control-plane' if cp else 'worker'})
    inbound = [{'protocol': 'tcp', 'port': 22, 'net': SSH}]
    outbound = [{'protocol': 'tcp', 'port': 6443, 'net': LB}]
    for net in n['api_egress_nets']:
        outbound.append({'protocol': 'tcp', 'port': 6443, 'net': net})
    if cp:
        inbound += [{'protocol': 'tcp', 'port': 6443, 'net': x}
                    for x in [LB] + n['api_ingress_node_sources']]
        for net in CP:
            for port in (2379, 2380):
                inbound.append({'protocol': 'tcp', 'port': port, 'net': net})
                outbound.append({'protocol': 'tcp', 'port': port, 'net': net})
    felix = {'spec': {'failsafeInboundHostPorts': inbound, 'failsafeOutboundHostPorts': outbound}}
    return gnp, hep, felix


def probes(c):
    ns = 'fds-gate2-probe'
    docs = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns,
             'labels': {'pod-security.kubernetes.io/enforce': 'restricted'}}}]
    # The selected, scanned digest must include Python 3; no installation in pods.
    for index, node in enumerate(c['probe_nodes']):
        name = 'probe-' + 'abc'[index]
        docs.append({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
            'name': name, 'namespace': ns, 'labels': {'app': name}}, 'spec': {
            'nodeSelector': {'kubernetes.io/hostname': node},
            'automountServiceAccountToken': False,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000,
                                'runAsGroup': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'http', 'image': c['probe_image'], 'imagePullPolicy': 'Never',
                            'command': ['python3', '-m', 'http.server', '8080', '--bind', '0.0.0.0'],
                            'workingDir': '/tmp', 'ports': [{'containerPort': 8080}],
                            'securityContext': {'allowPrivilegeEscalation': False,
                             'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                            'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                                          'limits': {'cpu': '100m', 'memory': '128Mi'}}}]}})
    docs.append({'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'probe-b', 'namespace': ns},
                 'spec': {'type': 'ClusterIP', 'selector': {'app': 'probe-b'},
                          'ports': [{'port': 8080, 'targetPort': 8080}]}})
    return docs


def write_yaml(path, docs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    c = json.loads(a.config.read_text())
    validate(c, a.config.resolve().parent)
    need(not a.out.exists(), 'output directory exists; use a new version directory')
    a.out.mkdir(parents=True)
    for name in ORDER:
        g, h, f = policies(c, name)
        write_yaml(a.out / 'gnp' / (name + '.yaml'), [g])
        write_yaml(a.out / 'hep' / (name + '.yaml'), [h])
        d = a.out / 'felix'
        d.mkdir(exist_ok=True)
        (d / (name + '.json')).write_text(json.dumps(f, indent=2) + '\n')
    write_yaml(a.out / 'probes.yaml', probes(c))
    (a.out / 'config.lock.json').write_text(json.dumps(c, indent=2) + '\n')
    rows = [hashlib.sha256(f.read_bytes()).hexdigest() + '  ' + str(f.relative_to(a.out))
            for f in sorted(a.out.rglob('*')) if f.is_file()]
    (a.out / 'SHA256SUMS').write_text('\n'.join(rows) + '\n')
    print('GENERATED_FOR_SERVER_VALIDATION; no live changes made')


if __name__ == '__main__':
    main()
