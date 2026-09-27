#!/usr/bin/env python3
"""One-shot, read-only ICR identity/policy collection. No Runtime AC verdict."""
import datetime as dt
import getpass
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys

EXPECTED = dict(zip(
    ['k8s-control01', 'k8s-control02', 'k8s-control03',
     'k8s-worker01', 'k8s-worker02', 'k8s-worker03'],
    ['10.1.93.102', '10.1.93.103', '10.1.93.104',
     '10.1.93.105', '10.1.93.107', '10.1.93.108']))
ALLOWED_API = {'https://' + ip + ':6443' for ip in
               ['10.1.93.109', '10.1.93.102', '10.1.93.103', '10.1.93.104']}
KST = dt.timezone(dt.timedelta(hours=9))


def now():
    return dt.datetime.now(KST).isoformat(timespec='seconds')


def metadata(obj):
    m = obj.get('metadata', {})
    return {k: m[k] for k in ['name', 'namespace', 'uid', 'resourceVersion',
                              'labels', 'creationTimestamp'] if k in m}


def project(kind, obj):
    """Keep only diagnostic fields; never emit full Pod/SA/Node objects."""
    items = obj['items'] if 'items' in obj else [obj]
    result = []
    for x in items:
        s, st = x.get('spec', {}), x.get('status', {})
        y = {'metadata': metadata(x)}
        if kind == 'nodes':
            y['addresses'] = st.get('addresses', [])
            y['ready'] = [c.get('status') for c in st.get('conditions', [])
                          if c.get('type') == 'Ready']
            y['nodeInfo'] = {k: st.get('nodeInfo', {}).get(k) for k in
                             ['kubeletVersion', 'containerRuntimeVersion', 'osImage']}
        elif kind == 'pods':
            y['identity'] = {k: s.get(k) for k in
                             ['nodeName', 'serviceAccountName', 'hostNetwork']}
            y['network'] = {k: st.get(k) for k in ['podIP', 'podIPs', 'hostIP', 'phase']}
            y['ready'] = [c.get('status') for c in st.get('conditions', [])
                          if c.get('type') == 'Ready']
            y['images'] = [{'name': c.get('name'), 'image': c.get('image')}
                           for c in s.get('containers', [])]
            y['containers'] = [{k: c.get(k) for k in
                                ['name', 'imageID', 'ready', 'restartCount']}
                               for c in st.get('containerStatuses', [])]
        elif kind == 'sa':
            y['automountServiceAccountToken'] = x.get('automountServiceAccountToken')
        elif kind == 'namespaces':
            pass
        else:
            # Network policy/HEP/IPPool/Tier specs contain routing/selection rules.
            # Annotations and managedFields (including last-applied blobs) are omitted.
            y['spec'] = s
        result.append(y)
    return result


def query(report, kubectl, key, args, kind=None):
    cmd = ([] if os.geteuid() == 0 else ['sudo', '-n']) + [
        kubectl, '--kubeconfig=/etc/kubernetes/admin.conf',
        '--request-timeout=10s'] + args
    row = {'id': key, 'started_kst': now(), 'command': cmd}
    report['queries'].append(row)
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=15, check=False)
        row.update(rc=p.returncode, stderr=p.stderr[:2000])
        if p.returncode:
            row['status'] = 'QUERY_ERROR'
            return None
        if len(p.stdout) > 4_000_000:
            row['status'] = 'OUTPUT_LIMIT'
            return None
        row['stdout_sha256'] = hashlib.sha256(p.stdout.encode()).hexdigest()
        data = project(kind, json.loads(p.stdout)) if kind else p.stdout.strip()
        row.update(status='COLLECTED', data=data)
        return data
    except subprocess.TimeoutExpired:
        row.update(rc=124, status='TIMEOUT')
    except (OSError, ValueError, TypeError, KeyError):
        row.update(rc=125, status='COLLECT_ERROR')
    finally:
        row['finished_kst'] = now()
    return None


def collect():
    report = {'schema': 'icr-monitoring-precheck/v1', 'started_kst': now(),
              'host': socket.gethostname(), 'account': getpass.getuser(),
              'collector_sha256': globals().get('SOURCE_SHA256', 'UNRECORDED'),
              'result': 'STOP', 'runtime_ac': 'NOT_ASSESSED', 'queries': []}
    if report['host'].split('.')[0] != 'k8s-control01':
        report['reason'] = 'HOST_MISMATCH'
        return report, 2
    kubectl = next((p for p in ['/usr/bin/kubectl', '/usr/local/bin/kubectl']
                    if os.path.isfile(p) and os.access(p, os.X_OK)), None)
    if not kubectl or (os.geteuid() != 0 and not shutil.which('sudo')):
        report['reason'] = 'MISSING_EXISTING_TOOL'
        return report, 2
    executable = os.stat(kubectl)
    if executable.st_uid != 0 or executable.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        report['reason'] = 'UNTRUSTED_KUBECTL_FILE'
        return report, 2
    server = query(report, kubectl, 'api-server',
                   ['config', 'view', '--minify', '-o',
                    'jsonpath={.clusters[0].cluster.server}'])
    if server not in ALLOWED_API:
        report['reason'] = 'API_IDENTITY_OR_ACCESS_FAILED'
        return report, 2
    nodes = query(report, kubectl, 'nodes', ['get', '--raw=/api/v1/nodes'], 'nodes')
    actual = {x['metadata'].get('name'): [a['address'] for a in x['addresses']
               if a.get('type') == 'InternalIP'] for x in (nodes or [])}
    if set(actual) != set(EXPECTED) or any(actual[n] != [ip] for n, ip in EXPECTED.items()):
        report['reason'] = 'CLUSTER_NODE_IDENTITY_MISMATCH'
        return report, 2
    # Explicit GET paths avoid discovery and kubectl disk-cache writes on control01.
    calico_api = '/apis/crd.projectcalico.org/v1/'
    core = '/api/v1/namespaces/'
    specs = [
        ('prometheus', 'pods', core + 'monitoring-api/pods?labelSelector=app%3Dprometheus'),
        ('prometheus-sa', 'sa', core + 'monitoring-api/serviceaccounts/prometheus'),
        ('monitoring-api-ns', 'namespaces', core + 'monitoring-api'),
        ('monitoring-node-ns', 'namespaces', core + 'monitoring-node'),
        ('node-exporter', 'pods', core + 'monitoring-node/pods'),
        ('calico', 'pods', core + 'kube-system/pods?labelSelector=k8s-app%3Dcalico-node'),
        ('hep', 'policy', calico_api + 'hostendpoints'),
        ('gnp', 'policy', calico_api + 'globalnetworkpolicies'),
        ('tiers', 'policy', calico_api + 'tiers'),
        ('ippools', 'policy', calico_api + 'ippools'),
        ('networksets', 'policy', calico_api + 'globalnetworksets'),
    ]
    for ns in ['monitoring-api', 'monitoring-node']:
        specs += [(ns + '-netpol', 'policy', '/apis/networking.k8s.io/v1/namespaces/'
                   + ns + '/networkpolicies'),
                  (ns + '-calico-netpol', 'policy', calico_api + 'namespaces/'
                   + ns + '/networkpolicies')]
    for key, kind, path in specs:
        value = query(report, kubectl, key, ['get', '--raw=' + path], kind)
        if value is None:
            report['reason'] = 'QUERY_FAILED:' + key
            return report, 2
        if key == 'prometheus':
            valid = [p for p in value if p['network']['phase'] == 'Running'
                     and p['ready'] == ['True'] and p['network']['podIP']
                     and p['identity']['nodeName'] in EXPECTED
                     and p['identity']['serviceAccountName'] == 'prometheus']
            if len(value) != 1 or len(valid) != 1:
                report['reason'] = 'SENDER_NOT_UNIQUELY_READY'
                return report, 2
    report['result'] = 'COLLECTED_PENDING_ANALYSIS'
    report['reason'] = 'Sender identity and policy snapshot only; packet source unobserved'
    return report, 0


if __name__ == '__main__':
    result, code = collect()
    result['finished_kst'] = now()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)
