#!/usr/bin/env python3
"""Check generated objects against selected CRD schema keywords; not full JSONSchema/CEL."""
import argparse
import copy
import importlib.util
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
sys.path.insert(0, str(ROOT / 'tests'))
import render
from validate import fixture, calico


def check(v, s, path='$'):
    if v is None and s.get('nullable'):
        return
    t = s.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'number': (int, float), 'boolean': bool}
    if t in types:
        assert isinstance(v, types[t]), path + ' type'
        if t in ('integer', 'number'):
            assert not isinstance(v, bool), path + ' numeric boolean'
    if 'enum' in s:
        assert v in s['enum'], path + ' enum'
    if 'pattern' in s and isinstance(v, str):
        assert re.search(s['pattern'], v) is not None, path + ' pattern'
    for key, compare in [('minimum', lambda x, y: x >= y), ('maximum', lambda x, y: x <= y)]:
        if key in s and isinstance(v, (int, float)) and not isinstance(v, bool):
            assert compare(v, s[key]), path + ' ' + key
    if isinstance(v, dict):
        for key in s.get('required', []):
            assert key in v, path + ' missing ' + key
        props = s.get('properties', {})
        for key, val in v.items():
            if key in props:
                check(val, props[key], path + '.' + key)
            elif isinstance(s.get('additionalProperties'), dict):
                check(val, s['additionalProperties'], path + '.' + key)
            elif props:
                assert key in props, path + ' unknown field ' + key
    elif isinstance(v, list):
        for i, val in enumerate(v):
            check(val, s.get('items', {}), path + '[' + str(i) + ']')
    for part in s.get('allOf', []):
        check(v, part, path)
    if 'anyOf' in s:
        passed = False
        for part in s['anyOf']:
            try:
                check(v, part, path)
                passed = True
            except AssertionError:
                pass
        assert passed, path + ' anyOf'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    a = p.parse_args()
    docs = list(yaml.safe_load_all(a.source.read_text()))
    schemas = {}
    for o in docs:
        if o and o.get('kind') == 'CustomResourceDefinition':
            for version in o['spec']['versions']:
                if version['served']:
                    schemas[(o['spec']['group'] + '/' + version['name'], o['spec']['names']['kind'])] = version['schema']['openAPIV3Schema']
    c = fixture()
    # Exercise the optional kube-system selector as well as the fixed host rules.
    c['nodes']['k8s-control01']['api_ingress_workload_selectors'] = ['k8s-app == "kube-dns"']
    count = 0
    for name in render.ORDER:
        g, h, f = render.policies(c, name)
        f = {'apiVersion': render.API, 'kind': 'FelixConfiguration',
             'metadata': {'name': 'node.' + name}, 'spec': f['spec']}
        for o in (g, h, f):
            check(o, schemas[(o['apiVersion'], o['kind'])])
            count += 1
    images = {}
    for o in docs:
        spec = (o or {}).get('spec', {}).get('template', {}).get('spec', {})
        for x in spec.get('containers', []) + spec.get('initContainers', []):
            images[x['image']] = 'test.invalid/image@sha256:' + 'b' * 64
    prepared = calico.prepare(copy.deepcopy(docs), 1450, images)
    cm = next(o for o in prepared if o and o.get('kind') == 'ConfigMap' and o['metadata']['name'] == 'calico-config')
    ds = next(o for o in prepared if o and o.get('kind') == 'DaemonSet' and o['metadata']['name'] == 'calico-node')
    node = next(c for c in ds['spec']['template']['spec']['containers'] if c['name'] == 'calico-node')
    assert cm['data']['calico_backend'] == 'vxlan' and cm['data']['veth_mtu'] == '1450'
    env = {e['name']: e.get('value') for e in node['env']}
    assert env['CALICO_IPV4POOL_CIDR'] == '10.244.0.0/16'
    assert env['CALICO_IPV4POOL_IPIP'] == 'Never' and env['CALICO_IPV4POOL_VXLAN'] == 'CrossSubnet'
    assert env['CALICO_IPV4POOL_NAT_OUTGOING'] == 'true'
    assert node['readinessProbe']['exec']['command'] == ['/bin/calico-node', '-felix-ready']
    assert node['livenessProbe']['exec']['command'] == ['/bin/calico-node', '-felix-live']
    assert '@sha256:' in node['image']
    print('PASS:', count, 'GNP/HEP/Felix objects: served API, recursive fields/types/required/enum/pattern/bounds/allOf/anyOf subset')
    print('PASS: Calico offline preparation: VXLAN backend, IPPool env/CIDR/NAT/MTU, both BIRD probes, image digests')
    print('NOT RUN: full JSONSchema, CEL, server admission, Calico semantic validation, runtime enforcement')


if __name__ == '__main__':
    main()
