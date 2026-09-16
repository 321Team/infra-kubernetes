#!/usr/bin/env python3
"""One read-only CP1 dataplane capture for offline compatibility analysis.

Collect native JSON, xtables extension parameters, kernel ipsets and Calico /
kube-proxy API configuration in one artifact. No firewall apply, workload
creation, reload, install or systemd changes. Raw warnings/failures are retained;
a complete capture is NOT an accepted configuration or activation receipt.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
NODE_SHA = '93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa'
MAX_BYTES = 2 * 1024 * 1024

HOST_READS = {
    'boot': ['cat', '/proc/sys/kernel/random/boot_id'],
    'kernel': ['uname', '-r'],
    'packages': ['rpm', '-q', 'firewalld', 'nftables', 'iptables-nft', 'ipset'],
    'containerd_version': ['containerd', '--version'],
    'native_json': ['nft', '-j', '-a', 'list', 'ruleset'],
    'native_text': ['nft', '-a', 'list', 'ruleset'],
    'iptables_version': ['iptables', '--version'],
    'iptables_save': ['iptables-save', '-c'],
    'ip6tables_save': ['ip6tables-save', '-c'],
    'ipsets': ['ipset', 'save'],
    'links': ['ip', '-j', '-d', 'link', 'show'],
    'routes': ['ip', '-j', '-4', 'route', 'show', 'table', 'all'],
    'runtime_zones': ['firewall-cmd', '--list-all-zones'],
    'runtime_policies': ['firewall-cmd', '--list-all-policies'],
    'permanent_zones': ['firewall-cmd', '--permanent', '--list-all-zones'],
    'permanent_policies': ['firewall-cmd', '--permanent', '--list-all-policies'],
}
API_READS = {
    'pods': ['get', 'pods', '-A'],
    'namespaces': ['get', 'namespaces'],
    'serviceaccounts': ['get', 'serviceaccounts', '-A'],
    'services': ['get', 'services', '-A'],
    'endpointslices': ['get', 'endpointslices.discovery.k8s.io', '-A'],
    'networkpolicies': ['get', 'networkpolicies.networking.k8s.io', '-A'],
    'calico_networkpolicies': ['get', 'networkpolicies.crd.projectcalico.org', '-A'],
    'calico_globalnetworkpolicies': ['get', 'globalnetworkpolicies.crd.projectcalico.org'],
    'calico_hostendpoints': ['get', 'hostendpoints.crd.projectcalico.org'],
    'calico_networksets': ['get', 'networksets.crd.projectcalico.org', '-A'],
    'calico_globalnetworksets': ['get', 'globalnetworksets.crd.projectcalico.org'],
    'calico_felix': ['get', 'felixconfigurations.crd.projectcalico.org'],
    'calico_ippools': ['get', 'ippools.crd.projectcalico.org'],
    'calico_blocks': ['get', 'ipamblocks.crd.projectcalico.org'],
    'calico_affinities': ['get', 'blockaffinities.crd.projectcalico.org'],
    'calico_daemonset': ['-n', 'kube-system', 'get', 'daemonset', 'calico-node'],
    'calico_config': ['-n', 'kube-system', 'get', 'configmap', 'calico-config'],
    'kube_proxy_config': ['-n', 'kube-system', 'get', 'configmap', 'kube-proxy'],
    'kube_proxy_daemonset': ['-n', 'kube-system', 'get', 'daemonset', 'kube-proxy'],
}


def command(node, argv, timeout):
    """Preserve argv[0] for xtables multicall after resolving trusted executable.

    Spool to private anonymous files to bound resident output memory. Each
    stream is truncated in the artifact with an explicit flag, never accepted.
    """
    result = {'argv': argv, 'started_at': node.now(), 'stdout_truncated': False, 'stderr_truncated': False}
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            resolved = node.binary(argv[0])
            proc = subprocess.run(argv, executable=resolved, stdin=subprocess.DEVNULL,
                                  stdout=stdout, stderr=stderr, timeout=timeout,
                                  env={'PATH': '/usr/sbin:/usr/bin:/usr/local/bin', 'LC_ALL': 'C',
                                       'LANG': 'C', 'HOME': '/root', 'KUBECTL_KUBERC': 'false'})
            result['rc'] = proc.returncode
        except subprocess.TimeoutExpired:
            result.update(rc=124, error='COMMAND_TIMEOUT')
        except (OSError, ValueError) as error:
            result.update(rc=127, error=str(error))
        for key, stream in (('stdout', stdout), ('stderr', stderr)):
            stream.seek(0)
            raw = stream.read(MAX_BYTES + 1)
            result[key + '_truncated'] = len(raw) > MAX_BYTES
            result[key] = node.redact(raw[:MAX_BYTES].decode('utf-8', 'replace'))
    result['ended_at'] = node.now()
    return result


def collect(node, reader=None, budget=240):
    node.need(0 < budget <= 240, 'capture budget invalid')
    reader = reader or (lambda argv, timeout: command(node, argv, timeout))
    started = time.monotonic()
    deadline = started + budget
    backend = node.BaseBackend('k8s-control01')
    prior = dict(node.COMMAND_STATE)
    node.COMMAND_STATE.update(count=0, timeouts=0, budget_exhausted=False, deadline=deadline)
    report = {'schema': 'fds.cp1-cni-dataplane-capture.v1', 'mode': 'READ_ONLY', 'host': 'k8s-control01',
              'queries': {}, 'errors': [], 'warnings': [], 'complete': False, 'paired_observation': True,
              'atomic_snapshot': False, 'authorizes_apply': False, 'authorizes_cp2_change': False,
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'started_at': node.now(), 'budget_seconds': budget}
    try:
        backend.identity()
        def read(key, argv):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report['errors'].append(key + ': CAPTURE_BUDGET_EXHAUSTED')
                return
            result = reader(argv, min(10, remaining))
            report['queries'][key] = result
            if (type(result.get('rc')) is not int or result['rc'] != 0 or
                    result.get('stdout_truncated', False) is not False or
                    result.get('stderr_truncated', False) is not False):
                report['errors'].append(key + ': INCOMPLETE_QUERY')
            if result.get('stderr', '').strip():
                report['warnings'].append(key)
        for suffix in ('', '_after'):
            for key, argv in HOST_READS.items(): read(key + suffix, argv)
            for key, argv in API_READS.items():
                read(key + suffix, ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
                     '--cache-dir=' + backend.cache.name, '--request-timeout=8s', *argv, '-o', 'json'])
        read('boot_final', HOST_READS['boot'])
        first = report['queries'].get('boot', {}).get('stdout', '').strip()
        last = report['queries'].get('boot_after', {}).get('stdout', '').strip()
        final = report['queries'].get('boot_final', {}).get('stdout', '').strip()
        if not first or first != last or first != final: report['errors'].append('BOOT_CHANGED_OR_MISSING')
        report['complete'] = not report['errors']
    except (OSError, ValueError, KeyError, TypeError) as error:
        report['errors'].append(str(error))
    finally:
        report['ended_at'] = node.now()
        report['elapsed_seconds'] = time.monotonic() - started
        backend.cache.cleanup()
        node.COMMAND_STATE.clear(); node.COMMAND_STATE.update(prior)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    source = ROOT / 'scripts/node-firewall.py'
    if hashlib.sha256(source.read_bytes()).hexdigest() != NODE_SHA:
        raise SystemExit('STOP: pinned helper differs')
    spec = importlib.util.spec_from_file_location('cp1_dataplane_pinned_helper', source)
    node = importlib.util.module_from_spec(spec); spec.loader.exec_module(node)
    # Reserve the output before touching the host: a typo/collision must not
    # force the operator to repeat an expensive read-only collection.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        result = collect(node)
        raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({'status': 'READ_ONLY_CAPTURE_COMPLETE' if result['complete'] else 'READ_ONLY_CAPTURE_INCOMPLETE',
                      'output': args.output, 'sha256': hashlib.sha256(raw).hexdigest(),
                      'warnings': result['warnings'], 'errors': result['errors'], 'authorizes_apply': False}))
    return 0 if result['complete'] else 1


if __name__ == '__main__': raise SystemExit(main())
