#!/usr/bin/env python3
"""Prepare a pinned legacy manifest offline; never patch a running DaemonSet."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml


def prepare(docs, mtu, image_lock):
    cm = [x for x in docs if x and x.get('kind') == 'ConfigMap' and x['metadata']['name'] == 'calico-config']
    ds = [x for x in docs if x and x.get('kind') == 'DaemonSet' and x['metadata']['name'] == 'calico-node']
    if len(cm) != 1 or len(ds) != 1:
        raise ValueError('unexpected manifest structure')
    cm[0]['data']['calico_backend'] = 'vxlan'
    cm[0]['data']['veth_mtu'] = str(mtu)
    node = next(x for x in ds[0]['spec']['template']['spec']['containers'] if x['name'] == 'calico-node')
    values = {'CALICO_IPV4POOL_CIDR': '10.244.0.0/16', 'CALICO_IPV4POOL_IPIP': 'Never',
              'CALICO_IPV4POOL_VXLAN': 'CrossSubnet', 'CALICO_IPV4POOL_NAT_OUTGOING': 'true'}
    for key, value in values.items():
        node['env'] = [e for e in node['env'] if e['name'] != key] + [{'name': key, 'value': value}]
    for probe, removed in (('readinessProbe', '-bird-ready'), ('livenessProbe', '-bird-live')):
        argv = node[probe]['exec']['command']
        if removed not in argv:
            raise ValueError('unexpected probe structure: ' + probe)
        node[probe]['exec']['command'] = [x for x in argv if x != removed]
    for item in docs:
        if not item:
            continue
        spec = item.get('spec', {}).get('template', {}).get('spec', {})
        for container in spec.get('containers', []) + spec.get('initContainers', []):
            original = container['image']
            image = image_lock.get(original, '')
            if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image or ''):
                raise ValueError('approved image digest missing for ' + original)
            container['image'] = image
    return docs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--image-lock', type=Path, required=True)
    p.add_argument('--mtu', type=int, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if hashlib.sha256(a.source.read_bytes()).hexdigest() != a.sha256:
        raise SystemExit('upstream file hash mismatch')
    if not 576 <= a.mtu <= 1450 or a.out.exists():
        raise SystemExit('invalid/unreviewed MTU or existing output')
    docs = prepare(list(yaml.safe_load_all(a.source.read_text())), a.mtu,
                   json.loads(a.image_lock.read_text()))
    a.out.write_text(yaml.safe_dump_all(docs, sort_keys=False))
    print('PREPARED_FOR_INSTALL_REVIEW; existing pools require separate migration, not env edits')


if __name__ == '__main__':
    main()
