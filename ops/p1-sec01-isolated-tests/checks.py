#!/usr/bin/env python3
"""Strict, secret-safe parsers and evidence sealing; Python standard library only."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tarfile


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def count(text):
    # etcdctl v3.6.0 --count-only requires --write-out=fields.
    rows = re.findall(r'^"Count"\s*:\s*(\d+)\s*$', text, re.M)
    if len(rows) != 1:
        raise ValueError('missing or ambiguous Count')
    return int(rows[0])


def secret_counts(data):
    if not isinstance(data, dict) or 'header' not in data:
        raise ValueError('invalid etcd range response')
    kvs = data.get('kvs', [])
    if not isinstance(kvs, list) or int(data.get('count', len(kvs))) != len(kvs):
        raise ValueError('partial or malformed range response')
    sb = 0
    for kv in kvs:
        # Never include data or decoder exception text in a diagnostic.
        value = base64.b64decode(kv['value'], validate=True)
        sb += value.startswith(b'k8s:enc:secretbox:v1:')
    return len(kvs), sb, len(kvs) - sb


def token_absent(pod):
    spec = pod['spec']
    if spec.get('automountServiceAccountToken') is not False:
        raise ValueError('automount is not false')
    for vol in spec.get('volumes', []):
        for source in vol.get('projected', {}).get('sources', []):
            if 'serviceAccountToken' in source:
                raise ValueError('projected serviceAccountToken present')


def nodes_ready(data):
    expected = {f'k8s-{role}{i:02}' for role in ('control', 'worker') for i in (1, 2, 3)}
    nodes = data['items']
    if len(nodes) != 6 or {n['metadata']['name'] for n in nodes} != expected:
        raise ValueError('unexpected node inventory')
    for node in nodes:
        if [c['status'] for c in node.get('status', {}).get('conditions', [])
                if c['type'] == 'Ready'] != ['True']:
            raise ValueError('node not Ready')


def process_ids(data_dir, proc=Path('/proc')):
    result = []
    for child in proc.iterdir():
        if not child.name.isdigit():
            continue
        try:
            args = (child / 'cmdline').read_bytes().split(b'\0')
            if not args or Path(os.fsdecode(args[0])).name != 'etcd':
                continue
            exact = os.fsencode('--data-dir=' + data_dir)
            separate = any(args[i:i+2] == [b'--data-dir', os.fsencode(data_dir)]
                           for i in range(len(args) - 1))
            if exact in args or separate:
                result.append(int(child.name))
        except FileNotFoundError:
            continue
    return sorted(result)


def baseline(kind, data, own_name):
    result = []
    for obj in data['items']:
        meta = obj['metadata']
        if kind == 'pods':
            if meta['name'] == own_name:
                continue
            status = obj.get('status', {})
            ready = [x['status'] for x in status.get('conditions', []) if x['type'] == 'Ready']
            containers = sorted((x['name'], x.get('restartCount'), x.get('ready'))
                                for x in status.get('containerStatuses', []))
            row = [meta['name'], meta['uid'], status.get('phase'), ready, containers]
        else:
            if meta.get('labels', {}).get('kubernetes.io/service-name') == own_name:
                continue
            # Endpoint order is immaterial, membership/address/UID/conditions/ports are not.
            endpoints = sorted(json.dumps(e, sort_keys=True) for e in obj.get('endpoints', []))
            row = [meta['name'], meta['uid'], meta.get('labels', {}), obj.get('addressType'),
                   sorted(obj.get('ports', []), key=lambda p: json.dumps(p, sort_keys=True)), endpoints]
        result.append(row)
    return sorted(result)


def manifest_files(root):
    files = []
    for p in root.rglob('*'):
        if p.is_symlink():
            raise ValueError('symlink in evidence')
        if p.is_file() and p != root / 'SHA256SUMS':
            files.append(p)
    return sorted(files)


def verify(root):
    expected = {}
    for line in (root / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        sha, name = line.split('  ', 1)
        rel = Path(name)
        if (not re.fullmatch(r'[0-9a-f]{64}', sha) or rel.is_absolute()
                or '..' in rel.parts or name in expected or '\\' in name):
            raise ValueError('invalid manifest')
        expected[name] = sha
    actual = {p.relative_to(root).as_posix(): digest(p) for p in manifest_files(root)}
    if not expected or actual != expected:
        raise ValueError('evidence manifest mismatch')


def seal(root):
    root = root.resolve(strict=True)
    files = manifest_files(root)
    (root / 'SHA256SUMS').write_text(''.join(
        f'{digest(p)}  {p.relative_to(root).as_posix()}\n' for p in files), encoding='utf-8')
    archive = root.with_name(root.name + '.tar.gz')
    with tarfile.open(str(archive) + '.tmp', 'w:gz') as tar:
        tar.add(root, arcname=root.name)
    os.replace(str(archive) + '.tmp', archive)
    Path(str(archive) + '.sha256').write_text(f'{digest(archive)}  {archive.name}\n', encoding='utf-8')
    verify(root)
    # Verify archived bytes against the same manifest, not just the input folder.
    with tarfile.open(archive, 'r:gz') as tar:
        for p in files:
            with tar.extractfile(root.name + '/' + p.relative_to(root).as_posix()) as f:
                if hashlib.sha256(f.read()).hexdigest() != digest(p):
                    raise ValueError('archive contents mismatch')


def main(args):
    mode = args[0]
    if mode == 'count':
        print(count(sys.stdin.read()))
    elif mode == 'secrets':
        a, b, c = secret_counts(json.load(sys.stdin))
        print(f'secret_prefix_count total={a} secretbox={b} not_secretbox={c}')
    elif mode == 'token':
        token_absent(json.load(sys.stdin)); print('automount=false projected_tokens=0')
    elif mode == 'nodes':
        nodes_ready(json.load(sys.stdin)); print('nodes=6/6_Ready')
    elif mode == 'pids':
        print('\n'.join(map(str, process_ids(args[1]))))
    elif mode == 'baseline':
        print(json.dumps(baseline(args[1], json.load(sys.stdin), args[2]), sort_keys=True))
    elif mode == 'revision':
        data = json.load(sys.stdin)
        if len(data) != 1 or not isinstance(data[0]['Status']['header']['revision'], int):
            raise ValueError('invalid endpoint status')
        print(data[0]['Status']['header']['revision'])
    elif mode in ('seal', 'verify'):
        {'seal': seal, 'verify': verify}[mode](Path(args[1])); print(mode + '=OK')
    else:
        raise ValueError('unknown mode')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        # Inputs may contain Secret values. Do not print exceptions/tracebacks.
        print('validation failed (input omitted)', file=sys.stderr)
        sys.exit(2)
