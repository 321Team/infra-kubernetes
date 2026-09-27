#!/usr/bin/env python3
"""Candidate CP1-only Join support. Secrets are returned only over private SSH.

No firewall edits, etcd removal, reset, or snapshot restore are implemented here.
The controller must never persist the credentials response to shared evidence.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile

HOST = 'k8s-control01'
BASE = Path('/var/lib/fds/cp-join-credentials')
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s']
ETCD = ['-n', 'kube-system', 'exec', 'etcd-k8s-control01', '-c', 'etcd', '--',
        'etcdctl', '--endpoints=https://127.0.0.1:2379',
        '--cacert=/etc/kubernetes/pki/etcd/ca.crt',
        '--cert=/etc/kubernetes/pki/etcd/healthcheck-client.crt',
        '--key=/etc/kubernetes/pki/etcd/healthcheck-client.key', '-w', 'json']
TARGETS = {'k8s-control02': '10.1.93.103', 'k8s-control03': '10.1.93.104'}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def command(argv, data=None, timeout=45):
    result = subprocess.run(argv, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    # Never propagate subprocess stdout/stderr: they can include credentials.
    need(result.returncode == 0, 'command failed: ' + argv[0])
    need(len(result.stdout) <= 8 * 1024 * 1024, 'output limit')
    return result.stdout


def kubectl(args):
    return json.loads(command(KUBE + args))


def atomic(path, value):
    raw = json.dumps(value, sort_keys=True).encode() + b'\n'
    fd = os.open(str(path) + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(str(path) + '.tmp', path)
        parent = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(parent)
        finally: os.close(parent)
    finally:
        Path(str(path) + '.tmp').unlink(missing_ok=True)


def private(path):
    for part in reversed([path] + list(path.parents)):
        if not part.exists():
            part.mkdir(mode=0o700)
        st = part.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022,
             'untrusted credential directory')
    return path


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o077,
             'untrusted credential journal')
        return json.load(stream)


def configuration():
    # kubeadm consumes the actual cluster configuration. Safe YAML parsing is a
    # capability check before any mutation, never a regex substitute for YAML.
    try:
        import yaml
    except ImportError:
        raise ValueError('CP1 system Python PyYAML unavailable; no mutation performed')
    raw = kubectl(['-n', 'kube-system', 'get', 'configmap', 'kubeadm-config', '-o', 'json'])['data']['ClusterConfiguration']
    cfg = yaml.safe_load(raw)
    need(cfg.get('kind') == 'ClusterConfiguration', 'unexpected cluster configuration')
    need(cfg.get('apiVersion') == 'kubeadm.k8s.io/v1beta4', 'configuration API differs')
    need(cfg.get('controlPlaneEndpoint') == '10.1.93.109:6443', 'controlPlaneEndpoint differs')
    need(cfg.get('certificatesDir', '/etc/kubernetes/pki') == '/etc/kubernetes/pki', 'custom certificate directory')
    etcd = cfg.get('etcd', {})
    need('external' not in etcd and 'local' in etcd, 'stacked etcd required')
    need(etcd['local'].get('dataDir', '/var/lib/etcd') == '/var/lib/etcd', 'custom etcd directory')
    version = command(['kubeadm', 'version', '-o', 'short']).decode().strip()
    need(version == 'v1.36.4', 'CP1 kubeadm version differs')
    pub = command(['openssl', 'x509', '-in', '/etc/kubernetes/pki/ca.crt', '-pubkey', '-noout'])
    der = command(['openssl', 'pkey', '-pubin', '-outform', 'DER'], pub)
    return raw, {'endpoint': cfg['controlPlaneEndpoint'], 'ca_spki_sha256': hashlib.sha256(der).hexdigest(),
                 'kubeadm_version': version, 'configuration_sha256': hashlib.sha256(raw.encode()).hexdigest()}


def topology():
    readyz = command(KUBE + ['get', '--raw=/readyz']).decode().strip()
    need(readyz == 'ok', 'API not ready')
    nodes = kubectl(['get', 'nodes', '-o', 'json'])['items']
    member_data = kubectl(ETCD + ['member', 'list'])
    members = member_data['members']
    health = kubectl(ETCD + ['endpoint', 'health', '--cluster'])
    status = kubectl(ETCD + ['endpoint', 'status', '--cluster'])
    need(members and len(health) == len(members), 'incomplete etcd endpoint health')
    need(all(row.get('health') is True for row in health), 'etcd endpoint unhealthy')
    need(len(status) == len(members) and all(not row.get('Status', {}).get('errors') for row in status),
         'etcd endpoint status incomplete/errors')
    return {'nodes': [{'name': n['metadata']['name'], 'uid': n['metadata']['uid'],
                       'control_plane': 'node-role.kubernetes.io/control-plane' in n['metadata'].get('labels', {}),
                       'ready': any(c['type'] == 'Ready' and c['status'] == 'True'
                                    for c in n.get('status', {}).get('conditions', []))} for n in nodes],
            'members': [{'id': str(m['ID']), 'name': m.get('name', ''), 'peer_urls': m['peerURLs'],
                         'client_urls': m.get('clientURLs', []), 'learner': m.get('isLearner', False)} for m in members],
            'endpoint_health': health, 'endpoint_status': status, 'api_ready': True,
            'observed_at': dt.datetime.now(dt.timezone.utc).isoformat()}


def certificate_secret(name='kubeadm-certs'):
    # get --ignore-not-found produces empty output only on actual NotFound.
    need(name == 'kubeadm-certs' or re.fullmatch('bootstrap-token-[a-z0-9]{6}', name), 'unexpected Secret name')
    raw = command(KUBE + ['-n', 'kube-system', 'get', 'secret', name, '--ignore-not-found', '-o', 'json'])
    return json.loads(raw) if raw.strip() else None


def delete_owned_secret(metadata):
    existing = certificate_secret(metadata['name'])
    if existing is None:
        return
    need(all(existing['metadata'][key] == metadata[key] for key in ('uid', 'resourceVersion')),
         'owned Secret identity changed; preserve for review')
    options = {'apiVersion': 'v1', 'kind': 'DeleteOptions',
               'preconditions': {key: metadata[key] for key in ('uid', 'resourceVersion')}}
    command(KUBE + ['delete', '--raw=/api/v1/namespaces/kube-system/secrets/' + metadata['name'], '-f', '-'],
            json.dumps(options).encode())


def mint(job):
    raw_config, public = configuration()
    directory = private(BASE / job)
    path = directory / 'CREDENTIALS.json'
    need(not path.exists(), 'credential attempt already exists; use revoke before new job')
    need(certificate_secret() is None, 'existing kubeadm-certs belongs to another attempt')
    record = {'job_id': job, 'state': 'MINTING', 'token_id': None, 'secret_uid': None, 'secret_version': None,
              'certificate_owner': None, 'join_token_secret': None}
    atomic(path, record)
    try:
        token = command(['kubeadm', 'token', 'create', '--ttl=2h', '--description=ICR_CP_JOIN_' + job]).decode().strip()
        need(re.fullmatch(r'[a-z0-9]{6}\.[a-z0-9]{16}', token), 'invalid token response')
        record['token_id'] = token.split('.')[0]; atomic(path, record)
        token_secret = certificate_secret('bootstrap-token-' + record['token_id'])
        need(token_secret is not None, 'new token Secret absent')
        record['join_token_secret'] = {k: token_secret['metadata'][k] for k in ('name', 'uid', 'resourceVersion')}
        atomic(path, record)
        key = secrets.token_hex(32)
        init = {'apiVersion': 'kubeadm.k8s.io/v1beta4', 'kind': 'InitConfiguration', 'certificateKey': key}
        with tempfile.TemporaryDirectory(prefix='fds-join-certs-', dir='/run') as tmp:
            cfg = Path(tmp) / 'upload.yaml'
            fd = os.open(cfg, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(json.dumps(init) + '\n---\n' + raw_config)
            command(['kubeadm', 'init', 'phase', 'upload-certs', '--config', str(cfg),
                     '--upload-certs', '--skip-certificate-key-print'], timeout=120)
        secret = certificate_secret()
        need(secret is not None, 'uploaded certificate Secret absent')
        record.update(state='ISSUED', secret_uid=secret['metadata']['uid'],
                      secret_version=secret['metadata']['resourceVersion'])
        owners = secret['metadata'].get('ownerReferences', [])
        need(len(owners) == 1 and owners[0]['kind'] == 'Secret' and
             re.fullmatch('bootstrap-token-[a-z0-9]{6}', owners[0]['name']), 'unexpected certificate TTL owner')
        owner = certificate_secret(owners[0]['name'])
        need(owner is not None and owner['metadata']['uid'] == owners[0]['uid'], 'certificate TTL owner differs')
        record['certificate_owner'] = {k: owner['metadata'][k] for k in ('name', 'uid', 'resourceVersion')}
        atomic(path, record)
        return dict(public, token=token, certificate_key=key, sensitive=True)
    except Exception:
        record['state'] = 'MINT_FAILED_CLEANUP_REQUIRED'; atomic(path, record)
        # Do not print or recover by guessing an unrecorded Secret UID.
        if record['join_token_secret']:
            try:
                delete_owned_secret(record['join_token_secret'])
                record['token_id'] = None; atomic(path, record)
            except Exception: pass
        raise


def revoke(job):
    path = BASE / job / 'CREDENTIALS.json'
    record = read_private(path)
    if record['state'] == 'REVOKED':
        return {'status': 'REVOKED'}
    if record['token_id']:
        need(record.get('join_token_secret'), 'token identity was not recorded; cleanup review required')
        delete_owned_secret(record['join_token_secret'])
        record['token_id'] = None; atomic(path, record)
    existing = certificate_secret()
    if existing is not None:
        need(record['secret_uid'] and existing['metadata']['uid'] == record['secret_uid'] and
             existing['metadata']['resourceVersion'] == record['secret_version'],
             'certificate Secret ownership changed; retain for manual review')
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                   'preconditions': {'uid': record['secret_uid'], 'resourceVersion': record['secret_version']}}
        command(KUBE + ['delete', '--raw=/api/v1/namespaces/kube-system/secrets/kubeadm-certs', '-f', '-'],
                json.dumps(options).encode())
    owner = record.get('certificate_owner')
    if owner:
        existing_owner = certificate_secret(owner['name'])
        if existing_owner is not None:
            need(all(existing_owner['metadata'][k] == owner[k] for k in ('uid', 'resourceVersion')),
                 'certificate TTL owner changed; retain for manual review')
            options = {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                       'preconditions': {k: owner[k] for k in ('uid', 'resourceVersion')}}
            command(KUBE + ['delete', '--raw=/api/v1/namespaces/kube-system/secrets/' + owner['name'], '-f', '-'],
                    json.dumps(options).encode())
    record['state'] = 'REVOKED'; atomic(path, record)
    return {'status': 'REVOKED'}


def main(request):
    need(os.geteuid() == 0 and socket.gethostname().split('.')[0] == HOST, 'CP1 root identity required')
    os.environ['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    os.environ['LC_ALL'] = 'C'
    mode = request['mode']
    if mode == 'inspect':
        need(certificate_secret() is None, 'existing kubeadm-certs belongs to another attempt')
        return {'configuration': configuration()[1], 'topology': topology()}
    if mode == 'topology':
        return topology()
    job = request.get('job_id', '')
    need(re.fullmatch('[a-f0-9]{16,32}', job), 'invalid job')
    if mode == 'credentials':
        return mint(job)
    if mode == 'revoke':
        return revoke(job)
    raise ValueError('unsupported mode')


if __name__ == '__main__':
    try:
        print(json.dumps(main(json.load(sys.stdin)), sort_keys=True))
    except Exception as error:
        # Exception type only: subprocess/YAML failures can contain secret data.
        print(json.dumps({'status': 'STOP_REVIEW_REQUIRED', 'error_type': type(error).__name__}))
        sys.exit(2)
