#!/usr/bin/env python3
"""One-node Calico changes from an authenticated CP; never disables firewalld."""
import argparse
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import yaml
from render import ORDER, BUNDLE, LABEL, need

FIELDS = ('failsafeInboundHostPorts', 'failsafeOutboundHostPorts')
GROUP = 'crd.projectcalico.org'


def run(argv, data=None):
    p = subprocess.run(argv, input=data, text=True, capture_output=True, timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or str(argv))
    return p.stdout


class Cluster:
    def __init__(self, prefix):
        self.prefix = prefix

    def call(self, *args, data=None):
        return run(self.prefix + list(args), data)

    def get(self, kind, name=None, optional=False):
        args = ['get', kind]
        if name:
            args.append(name)
        if optional:
            args.append('--ignore-not-found=true')
        out = self.call(*args, '-o', 'json')
        return json.loads(out) if out.strip() else None

    def remove_exact(self, plural, current):
        m = current['metadata']
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {
            'uid': m['uid'], 'resourceVersion': m['resourceVersion']}}
        url = f'/apis/{GROUP}/v1/{plural}/{m["name"]}'
        self.call('delete', '--raw=' + url, '-f', '-', data=json.dumps(options))


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')
    os.chmod(path, 0o600)


def verify_bundle(root):
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        rel = Path(name)
        need(not rel.is_absolute() and '..' not in rel.parts, 'invalid manifest path')
        p = root / rel
        need(p.is_file() and not p.is_symlink(), 'missing or symbolic bundle file')
        need(hashlib.sha256(p.read_bytes()).hexdigest() == digest, 'bundle hash mismatch: ' + name)


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['preflight', 'stage-gnp', 'set-failsafe', 'create-hep',
                                     'rollback-node', 'cleanup-gnp'])
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--kubeconfig', required=True)
    p.add_argument('--context', required=True)
    p.add_argument('--node', choices=ORDER)
    p.add_argument('--server')
    p.add_argument('--tls-server-name')
    p.add_argument('--protection-receipt', type=Path)
    a = p.parse_args()
    verify_bundle(a.bundle)
    config = json.loads((a.bundle / 'config.lock.json').read_text())
    prefix = ['kubectl', '--kubeconfig=' + a.kubeconfig, '--context=' + a.context,
              '--request-timeout=30s']
    if a.server:
        need(a.server == 'https://127.0.0.1:6443' and a.tls_server_name,
             'recovery server requires loopback and a verified certificate SAN')
        prefix += ['--server=' + a.server, '--tls-server-name=' + a.tls_server_name]
    else:
        need(not a.tls_server_name, 'tls-server-name requires explicit recovery server')
    k = Cluster(prefix)
    need(k.get('namespace', 'kube-system')['metadata']['uid'] == config['cluster_uid'],
         'wrong cluster UID')
    a.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    binding = {'cluster_uid': config['cluster_uid'], 'attempt_id': config['attempt_id'],
               'config_sha256': hashlib.sha256((a.bundle / 'config.lock.json').read_bytes()).hexdigest()}
    binding_file = a.state / 'state-binding.json'
    if binding_file.exists():
        need(json.loads(binding_file.read_text()) == binding, 'state directory belongs to a different attempt/config')
    else:
        need(a.action in ('preflight', 'stage-gnp'), 'initialize state with preflight first')
        dump(binding_file, binding)
    heps = k.get('hostendpoints.' + GROUP)['items']
    if a.action in ('preflight', 'stage-gnp'):
        need(not any(h['spec']['node'] in ORDER for h in heps),
             'existing HEP: stop for migration review; never delete it to satisfy preflight')
        nodes = k.get('nodes')['items']
        need(set(n['metadata']['name'] for n in nodes) == set(ORDER), 'node inventory mismatch')
        need(all(any(x['type'] == 'Ready' and x['status'] == 'True'
                     for x in n['status']['conditions']) for n in nodes), 'all six nodes must be Ready')
        # WorkloadEndpoint is served through the calico-apiserver aggregation
        # layer in this Calico release, not as a plain crd.projectcalico.org/v1
        # CRD, and this cluster runs without calico-apiserver. An absent
        # resource type must not turn the selector-collision check into an
        # automatic pass, so check pod labels instead (a WEP carries its pod's
        # labels) and record that the fallback source was used.
        try:
            weps = json.loads(k.call('get', 'workloadendpoints.' + GROUP, '-A', '-o', 'json'))['items']
            wep_source = 'workloadendpoints'
        except RuntimeError as e:
            if "doesn't have a resource type" not in str(e):
                raise
            weps = json.loads(k.call('get', 'pods', '-A', '-o', 'json'))['items']
            wep_source = 'pods-as-fallback'
            dump(a.state / 'wep-fallback.json', {'source': wep_source, 'count': len(weps),
                                                 'reason': str(e).strip()})
        need(not any(x['metadata'].get('labels', {}).get(LABEL) == BUNDLE for x in weps),
             'GNP selector already selects a workload')
        controllers = k.get('kubecontrollersconfigurations.' + GROUP)['items']
        need(not any(x.get('spec', {}).get('controllers', {}).get('node', {})
                     .get('hostEndpoint', {}).get('autoCreate') == 'Enabled' for x in controllers),
             'automatic HEP creation must be reviewed and disabled for this procedure')
        for kind in ('globalnetworkpolicies', 'hostendpoints', 'felixconfigurations',
                     'ippools', 'kubecontrollersconfigurations'):
            q = a.state / ('initial-' + kind + '.json')
            if not q.exists():
                dump(q, k.get(kind + '.' + GROUP))
        if a.action == 'preflight':
            print('PREFLIGHT_STRUCTURAL_OK; effective Felix, policy overlap and recovery review still required')
            return
        for name in ORDER:
            f = a.bundle / 'gnp' / (name + '.yaml')
            obj = yaml.safe_load(f.read_text())
            need(k.get('globalnetworkpolicies.' + GROUP, obj['metadata']['name'], True) is None,
                 'GNP already exists; inspect partial work, do not overwrite')
            k.call('create', '--dry-run=server', '-f', str(f))
        for name in ORDER:
            f = a.bundle / 'gnp' / (name + '.yaml')
            created = json.loads(k.call('create', '-f', str(f), '-o', 'json'))
            dump(a.state / (name + '-gnp-created.json'), created)
        print('GNP_STAGED; HEP has not been created')
        return
    need(a.node is not None, 'one --node is required')
    node = a.node
    hepname, felixname = 'fds-k8s-' + node, 'node.' + node
    fstate = a.state / (node + '-felix-before.json')
    intended = json.loads((a.bundle / 'felix' / (node + '.json')).read_text())['spec']
    hep = k.get('hostendpoints.' + GROUP, hepname, True)
    if a.action == 'set-failsafe':
        need(not any(h['spec']['node'] == node for h in heps), 'HEP already exists on target')
        need(not fstate.exists(), 'backup exists: inspect prior attempt before retry')
        staged = json.loads((a.state / (node + '-gnp-created.json')).read_text())
        live_gnp = k.get('globalnetworkpolicies.' + GROUP, staged['metadata']['name'])
        need(live_gnp['metadata']['uid'] == staged['metadata']['uid'] and
             live_gnp['spec'] == staged['spec'], 'staged GNP drift')
        old = k.get('felixconfigurations.' + GROUP, felixname, True)
        dump(fstate, old)  # Backup precedes mutation, including original absence.
        if old is None:
            value = {'apiVersion': GROUP + '/v1', 'kind': 'FelixConfiguration',
                     'metadata': {'name': felixname}, 'spec': intended}
            k.call('create', '--dry-run=server', '-f', '-', data=json.dumps(value))
            k.call('create', '-f', '-', data=json.dumps(value))
        else:
            patch = [{'op': 'test', 'path': '/metadata/resourceVersion',
                      'value': old['metadata']['resourceVersion']}]
            if 'spec' not in old:
                patch.append({'op': 'add', 'path': '/spec', 'value': {}})
            patch += [{'op': 'add', 'path': '/spec/' + key, 'value': intended[key]} for key in FIELDS]
            k.call('patch', 'felixconfigurations.' + GROUP, felixname,
                   '--type=json', '--patch', json.dumps(patch), '--dry-run=server')
            k.call('patch', 'felixconfigurations.' + GROUP, felixname,
                   '--type=json', '--patch', json.dumps(patch))
        dump(a.state / (node + '-felix-after.json'), k.get('felixconfigurations.' + GROUP, felixname))
        print('NODE_FAILSAFE_WRITTEN; verify effective settings before create-hep')
    elif a.action == 'create-hep':
        need(not any(h['spec']['node'] == node for h in heps), 'existing HEP: do not replace')
        current = k.get('felixconfigurations.' + GROUP, felixname)
        need(all(current.get('spec', {}).get(x) == intended[x] for x in FIELDS), 'failsafe storage drift')
        need((a.state / (node + '-felix-after.json')).exists(), 'missing failsafe checkpoint')
        file = a.bundle / 'hep' / (node + '.yaml')
        k.call('create', '--dry-run=server', '-f', str(file))
        created = json.loads(k.call('create', '-f', str(file), '-o', 'json'))
        dump(a.state / (node + '-hep-created.json'), created)
        print('HEP_CREATED_AND_ENFORCING; verify before any firewalld change')
    elif a.action == 'rollback-node':
        need(a.protection_receipt is not None, 'first restore and test protective firewall on target')
        r = json.loads(a.protection_receipt.read_text())
        need(r['node'] == node and r['status'] == 'PROTECTION_RESTORED_REVIEWED'
             and r['cluster_uid'] == config['cluster_uid'] and r['attempt_id'] == config['attempt_id']
             and r['config_sha256'] == binding['config_sha256'] and r['evidence'], 'invalid protection receipt')
        # Receipt binds real target tests; it is an operator/reviewer assertion, not remote sensing.
        from render import evidence
        for item in r['evidence']:
            evidence(item, a.protection_receipt.resolve().parent)
        # Validate everything read-only first. A refusal after the HEP was already
        # deleted would leave a half-restored node, so no mutation happens before
        # every check below has passed.
        if hep:
            recorded = json.loads((a.state / (node + '-hep-created.json')).read_text())
            need(hep['metadata']['uid'] == recorded['metadata']['uid'] and
                 hep['spec'] == recorded['spec'] and hep['metadata']['labels'] == recorded['metadata']['labels'],
                 'HEP drift: refuse deletion')
        old = cur = None
        if fstate.exists():
            old = json.loads(fstate.read_text())
            cur = k.get('felixconfigurations.' + GROUP, felixname, True)
            need(cur is not None, 'failsafe resource disappeared: inspect before restoration')
            after = json.loads((a.state / (node + '-felix-after.json')).read_text())
            need(cur['metadata']['uid'] == after['metadata']['uid'], 'Felix UID changed: refuse restoration')
            need(all(cur.get('spec', {}).get(x) == intended[x] for x in FIELDS),
                 'failsafe drift or already restored; inspect before retry')
            if old is None:
                # Compare with the spec recorded right after creation, not with FIELDS alone:
                # the API server can default extra fields (e.g. nftablesMode) when it creates the
                # resource. Anything added or changed since creation is still refused.
                need(cur.get('spec', {}) == after.get('spec', {}),
                     'Felix spec changed since creation (unrelated fields): refuse deletion')
        if hep:
            k.remove_exact('hostendpoints', hep)
            need(k.get('hostendpoints.' + GROUP, hepname, True) is None, 'HEP still exists')
        if fstate.exists():
            if old is None:
                k.remove_exact('felixconfigurations', cur)
            else:
                replacement = copy.deepcopy(cur)
                for key in FIELDS:
                    if key in old.get('spec', {}):
                        replacement['spec'][key] = old['spec'][key]
                    else:
                        replacement['spec'].pop(key, None)
                k.call('replace', '-f', '-', data=json.dumps(replacement))
        dump(a.state / (node + '-rollback-record.json'), {'node': node, 'status': 'CALICO_RESTORED_TEST_REQUIRED'})
        print('CALICO_RESTORED_TEST_REQUIRED; recheck effective inheritance and traffic')
    elif a.action == 'cleanup-gnp':
        need(not any(h['spec']['node'] == node for h in heps), 'HEP still present on target')
        recorded = json.loads((a.state / (node + '-gnp-created.json')).read_text())
        cur = k.get('globalnetworkpolicies.' + GROUP, recorded['metadata']['name'], True)
        if cur:
            need(cur['metadata']['uid'] == recorded['metadata']['uid'] and cur['spec'] == recorded['spec'],
                 'GNP drift: refuse deletion')
            k.remove_exact('globalnetworkpolicies', cur)
        print('UNSELECTED_GNP_REMOVED')


if __name__ == '__main__':
    main()
