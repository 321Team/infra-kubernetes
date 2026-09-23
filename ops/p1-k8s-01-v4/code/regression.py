#!/usr/bin/env python3
"""API, etcd and all-Worker Pod/Service/DNS checks from an authenticated CP."""
import argparse
import json
import os
import subprocess
from pathlib import Path

from render import ORDER, need


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kubeconfig', required=True)
    p.add_argument('--context', required=True)
    p.add_argument('--cluster-uid', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit('use a new output file for each phase')
    os.umask(0o077)
    log = []
    base = ['kubectl', '--kubeconfig=' + a.kubeconfig, '--context=' + a.context, '--request-timeout=30s']
    def k(*args):
        r = subprocess.run(base + list(args), text=True, capture_output=True, timeout=120)
        log.append({'argv': base + list(args), 'rc': r.returncode, 'stdout': r.stdout, 'stderr': r.stderr})
        a.out.write_text(json.dumps(log, indent=2))
        if r.returncode:
            raise RuntimeError('command failed; see ' + str(a.out))
        return r.stdout
    uid = json.loads(k('get', 'ns', 'kube-system', '-o', 'json'))['metadata']['uid']
    if uid != a.cluster_uid:
        raise SystemExit('wrong cluster')
    nodes = json.loads(k('get', 'nodes', '-o', 'json'))['items']
    need({n['metadata']['name'] for n in nodes} == set(ORDER), 'six-node inventory mismatch')
    need(all(any(x['type'] == 'Ready' and x['status'] == 'True' for x in n['status']['conditions'])
               for n in nodes), 'a node is not Ready')
    k('get', '--raw=/readyz?verbose')
    syspods = json.loads(k('get', 'pods', '-n', 'kube-system', '-o', 'json'))['items']
    for pod in syspods:
        if pod['status'].get('phase') == 'Succeeded':
            continue
        need(any(x['type'] == 'Ready' and x['status'] == 'True'
                   for x in pod['status'].get('conditions', [])), pod['metadata']['name'] + ' is not Ready')
    # kubeadm stacked-etcd standard paths; preflight must verify them against static Pod manifests.
    for node in ORDER[3:]:
        for command in ('health', 'status'):
            output = k('-n', 'kube-system', 'exec', 'etcd-' + node, '-c', 'etcd', '--', 'etcdctl',
              '--endpoints=https://127.0.0.1:2379', '--cacert=/etc/kubernetes/pki/etcd/ca.crt',
              '--cert=/etc/kubernetes/pki/etcd/healthcheck-client.crt',
              '--key=/etc/kubernetes/pki/etcd/healthcheck-client.key',
              'endpoint', command, '--cluster', '--write-out=json')
            endpoints = json.loads(output)
            need(len(endpoints) == 3, 'expected three etcd endpoints')
            if command == 'health':
                need(all(x.get('health') is True for x in endpoints), 'unhealthy etcd endpoint')
            else:
                need(len({x['Status']['header']['cluster_id'] for x in endpoints}) == 1,
                     'etcd cluster ID mismatch')
                need(len({x['Status']['header']['member_id'] for x in endpoints}) == 3,
                     'etcd member IDs not unique')
                need(all(not x['Status'].get('errors') and x['Status'].get('leader')
                         for x in endpoints), 'etcd errors or missing leader')
    pods = {}
    for name in ('probe-a', 'probe-b', 'probe-c'):
        k('-n', 'fds-gate2-probe', 'wait', '--for=condition=Ready', 'pod/' + name, '--timeout=60s')
        pods[name] = json.loads(k('-n', 'fds-gate2-probe', 'get', 'pod', name, '-o', 'json'))
    need({x['spec']['nodeName'] for x in pods.values()} == set(ORDER[:3]), 'one Pod on each Worker required')
    service = json.loads(k('-n', 'fds-gate2-probe', 'get', 'svc', 'probe-b', '-o', 'json'))
    def http(source, target):
        code = 'import urllib.request; r=urllib.request.urlopen(' + repr('http://' + target + ':8080/') + ',timeout=5); print(r.status); assert r.status==200'
        k('-n', 'fds-gate2-probe', 'exec', source, '--', 'python3', '-c', code)
    for source in pods:
        for target in pods:
            if source != target:
                http(source, pods[target]['status']['podIP'])
        http(source, service['spec']['clusterIP'])
        http(source, 'probe-b.fds-gate2-probe.svc.cluster.local')
    for name in pods:
        k('-n', 'fds-gate2-probe', 'exec', name, '--', 'python3', '-c',
          'import socket; print(socket.getaddrinfo("kubernetes.default.svc.cluster.local",443))')
    print('REGRESSION_COMMANDS_OK; inspect etcd fields, MTU, Host DNS/NTP/image and negative tests separately')


if __name__ == '__main__':
    main()
