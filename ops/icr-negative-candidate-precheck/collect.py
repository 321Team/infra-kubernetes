#!/usr/bin/env python3
"""Bounded worker03 metadata observation, never approval or an active probe."""
import datetime as dt
import hashlib
import json
import os
import pwd
import socket
import stat
import subprocess
import sys

NODE = 'k8s-worker03'
NODE_IP = '10.1.93.108'
API = 'https://10.1.93.109:6443'
POD_PATH = '/api/v1/pods?fieldSelector=spec.nodeName%3Dk8s-worker03&limit=100'
MAX_ITEMS = 100
KST = dt.timezone(dt.timedelta(hours=9))


def now():
    return dt.datetime.now(KST).isoformat(timespec='seconds')


def metadata(obj):
    m = obj['metadata']
    return {k: m[k] for k in ('name', 'namespace', 'uid', 'resourceVersion',
                              'creationTimestamp', 'deletionTimestamp') if k in m}


def project_node(obj):
    if obj.get('kind') != 'Node' or obj.get('metadata', {}).get('name') != NODE:
        raise ValueError('NODE_IDENTITY_MISMATCH')
    addresses = obj.get('status', {}).get('addresses', [])
    ips = [x.get('address') for x in addresses if x.get('type') == 'InternalIP']
    ready = [x.get('status') for x in obj.get('status', {}).get('conditions', [])
             if x.get('type') == 'Ready']
    if ips != [NODE_IP] or ready != ['True'] or not obj['metadata'].get('uid'):
        raise ValueError('NODE_IDENTITY_OR_READY_MISMATCH')
    return {'metadata': metadata(obj), 'internalIPs': ips, 'ready': ready}


def project_pods(obj):
    if obj.get('kind') != 'PodList' or not isinstance(obj.get('items'), list):
        raise ValueError('INVALID_POD_LIST')
    if obj.get('metadata', {}).get('continue') or len(obj['items']) > MAX_ITEMS:
        raise ValueError('INCOMPLETE_OR_OVERSIZED_POD_LIST')
    rows, seen = [], set()
    for p in obj['items']:
        m, s, status = p['metadata'], p['spec'], p.get('status', {})
        if s.get('nodeName') != NODE or not all(m.get(k) for k in ('name', 'namespace', 'uid')):
            raise ValueError('POD_IDENTITY_MISMATCH')
        if m['uid'] in seen:
            raise ValueError('DUPLICATE_POD_UID')
        seen.add(m['uid'])
        statuses = {x['name']: x for x in status.get('containerStatuses', [])}
        containers = []
        for x in s.get('containers', []):
            st = statuses.get(x['name'], {})
            containers.append({'name': x['name'], 'image': x.get('image'),
                               'imageID': st.get('imageID'), 'ready': st.get('ready'),
                               'privileged': x.get('securityContext', {}).get('privileged', False)})
        ready = [x.get('status') for x in status.get('conditions', []) if x.get('type') == 'Ready']
        reasons = []
        if m['namespace'] in ('kube-system', 'kube-public', 'kube-node-lease'):
            reasons.append('SYSTEM_NAMESPACE')
        if s.get('serviceAccountName') == 'prometheus' or m.get('labels', {}).get('app') == 'prometheus':
            reasons.append('PROMETHEUS_IDENTITY')
        if any(s.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC')):
            reasons.append('HOST_NAMESPACE')
        if any(x['privileged'] for x in containers):
            reasons.append('PRIVILEGED_CONTAINER')
        if 'kubernetes.io/config.mirror' in m.get('annotations', {}):
            reasons.append('STATIC_MIRROR_POD')
        if m.get('deletionTimestamp'):
            reasons.append('TERMINATING')
        if status.get('phase') != 'Running' or ready != ['True']:
            reasons.append('NOT_RUNNING_READY')
        if status.get('hostIP') != NODE_IP or not status.get('podIP') or not s.get('serviceAccountName'):
            reasons.append('NETWORK_OR_SA_INCOMPLETE')
        if not containers or any(not x['imageID'] or x['ready'] is not True for x in containers):
            reasons.append('CONTAINER_IDENTITY_OR_READY_INCOMPLETE')
        # Only common role selectors; arbitrary labels/annotations/env may carry data.
        label_keys = ('app', 'role', 'app.kubernetes.io/name', 'app.kubernetes.io/component',
                      'app.kubernetes.io/part-of')
        labels = {k: m.get('labels', {})[k] for k in label_keys if k in m.get('labels', {})}
        owners = [{k: o[k] for k in ('kind', 'name', 'uid', 'controller') if k in o}
                  for o in m.get('ownerReferences', [])]
        rows.append({'metadata': metadata(p), 'role_labels': labels, 'owners': owners,
                     'nodeName': s['nodeName'], 'serviceAccountName': s.get('serviceAccountName'),
                     'hostNetwork': s.get('hostNetwork', False),
                     'hostPID': s.get('hostPID', False), 'hostIPC': s.get('hostIPC', False),
                     'podIP': status.get('podIP'), 'hostIP': status.get('hostIP'),
                     'phase': status.get('phase'), 'ready': ready, 'containers': containers,
                     'screening': 'EXCLUDED_FROM_GENERAL_POD_SHORTLIST' if reasons else 'OWNER_REVIEW_REQUIRED',
                     'reasons': reasons, 'approval': 'NOT_ESTABLISHED',
                     'probe_capability': 'NOT_CHECKED', 'egress_policy': 'NOT_ASSESSED'})
    return {'list_resourceVersion': obj.get('metadata', {}).get('resourceVersion'),
            'items': rows, 'pod_count': len(rows),
            'owner_review_count': sum(not r['reasons'] for r in rows)}


def query(report, kubectl, key, args, projector=None):
    cmd = ['/usr/bin/sudo', '-n', kubectl, '--kubeconfig=/etc/kubernetes/admin.conf',
           '--request-timeout=10s'] + args
    row = {'id': key, 'command': cmd, 'started_kst': now()}
    report['queries'].append(row)
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=15, check=False)
        row.update(rc=p.returncode,
                   stderr_sha256=hashlib.sha256(p.stderr.encode()).hexdigest(),
                   stderr_bytes=len(p.stderr.encode()))
        if p.returncode:
            row['status'] = 'FORBIDDEN' if 'forbidden' in p.stderr.lower() else 'QUERY_ERROR'
            return None
        if len(p.stdout.encode()) > 4_000_000:
            row['status'] = 'OUTPUT_LIMIT'
            return None
        row['stdout_sha256'] = hashlib.sha256(p.stdout.encode()).hexdigest()
        value = projector(json.loads(p.stdout)) if projector else p.stdout.strip()
        row.update(status='COLLECTED', data=value)
        return value
    except subprocess.TimeoutExpired:
        row.update(rc=124, status='TIMEOUT')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Do not copy exception strings/raw Pod data into evidence.
        row.update(rc=125, status='INVALID_OR_INCOMPLETE_RESPONSE')
    finally:
        row['finished_kst'] = now()
    return None


def collect():
    report = {'schema': 'icr-negative-candidate-precheck/v1', 'started_kst': now(),
              'host': socket.gethostname(), 'account': pwd.getpwuid(os.geteuid()).pw_name,
              'collector_sha256': globals().get('SOURCE_SHA256', 'UNRECORDED'),
              'result': 'STOP', 'runtime_ac': 'NOT_ASSESSED',
              'probe': 'NOT_RUN', 'approval': 'NOT_ESTABLISHED', 'queries': []}
    if report['host'].split('.')[0] != 'k8s-control01' or report['account'] != 'svc_ansible':
        report['reason'] = 'HOST_OR_ACCOUNT_MISMATCH'
        return report, 2
    kubectl = next((p for p in ('/usr/bin/kubectl', '/usr/local/bin/kubectl')
                    if os.path.isfile(p) and os.access(p, os.X_OK)), None)
    if not kubectl or not os.access('/usr/bin/sudo', os.X_OK):
        report['reason'] = 'MISSING_EXISTING_TOOL'
        return report, 2
    st = os.stat(kubectl)
    if st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        report['reason'] = 'UNTRUSTED_KUBECTL_FILE'
        return report, 2
    api = query(report, kubectl, 'api-server',
                ['config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}'])
    if api != API:
        report['reason'] = 'EXPECTED_LB_API_OR_ACCESS_FAILED'
        return report, 2
    node = query(report, kubectl, 'worker03', ['get', '--raw=/api/v1/nodes/' + NODE], project_node)
    if node is None:
        report['reason'] = 'WORKER03_IDENTITY_OR_QUERY_FAILED'
        return report, 2
    pods = query(report, kubectl, 'worker03-pods', ['get', '--raw=' + POD_PATH], project_pods)
    if pods is None:
        report['reason'] = 'POD_LIST_FAILED_OR_INCOMPLETE'
        return report, 2
    report.update(result='COLLECTED_PENDING_OWNER_SELECTION',
                  reason='Inventory only; no approved probe or policy verdict',
                  pod_count=pods['pod_count'], owner_review_count=pods['owner_review_count'])
    return report, 0


if __name__ == '__main__':
    result, code = collect()
    result['finished_kst'] = now()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)
