#!/usr/bin/env python3
"""Read one pinned Grafana identity and bounded policy inputs; no probe/verdict."""
import datetime as dt
import hashlib
import json
import os
import pwd
import socket
import stat
import subprocess
import sys

API = 'https://10.1.93.109:6443'
NS = 'monitoring-api'
POD = 'grafana-7dc57885d4-sk8h2'
UID = '00396c87-640c-4704-ac6c-dcbacf6fbc90'
IMAGE = '10.1.93.54/fds/grafana@sha256:7a1ff54408c90357ff1c76189e5bbc24f898531cb771fbbfe6a477f00de37d5b'
KST = dt.timezone(dt.timedelta(hours=9))
MAX_ITEMS = 100
POD_PATH = '/api/v1/namespaces/' + NS + '/pods/' + POD


def now():
    return dt.datetime.now(KST).isoformat(timespec='seconds')


def metadata(obj):
    m = obj['metadata']
    if not all(isinstance(m.get(k), str) and m[k] for k in ('name', 'uid', 'resourceVersion')):
        raise ValueError('INCOMPLETE_IDENTITY')
    labels = m.get('labels', {})
    if not isinstance(labels, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
        raise ValueError('INVALID_LABELS')
    # Labels are needed to evaluate selectors. No annotations/managedFields.
    return {**{k: m[k] for k in ('name', 'namespace', 'uid', 'resourceVersion') if k in m},
            'labels': labels}


def project_pod(obj):
    m = metadata(obj)
    s, st = obj['spec'], obj['status']
    if (obj.get('kind') != 'Pod' or obj.get('apiVersion') != 'v1'
            or (m['name'], m.get('namespace'), m['uid']) != (POD, NS, UID)
            or obj['metadata'].get('deletionTimestamp')
            or 'kubernetes.io/config.mirror' in obj['metadata'].get('annotations', {})
            or s.get('nodeName') != 'k8s-worker03' or s.get('serviceAccountName') != 'default'
            or st.get('hostIP') != '10.1.93.108' or st.get('podIP') != '10.244.39.217'
            or m['labels'].get('app') != 'grafana' or st.get('phase') != 'Running'
            or any(s.get(k, False) for k in ('hostNetwork', 'hostPID', 'hostIPC'))):
        raise ValueError('PINNED_GRAFANA_IDENTITY_MISMATCH')
    cs = s.get('containers', [])
    statuses = st.get('containerStatuses', [])
    ready = [x.get('status') for x in st.get('conditions', []) if x.get('type') == 'Ready']
    if (len(cs) != 1 or cs[0].get('name') != 'grafana' or len(statuses) != 1
            or statuses[0].get('name') != 'grafana' or statuses[0].get('imageID') != IMAGE
            or statuses[0].get('ready') is not True or ready != ['True']
            or 'running' not in statuses[0].get('state', {})
            or s.get('ephemeralContainers')
            or any(c.get('securityContext', {}).get('privileged', False)
                   for c in cs + s.get('initContainers', []))):
        raise ValueError('CONTAINER_OR_READY_MISMATCH')
    return {'metadata': m, 'nodeName': s['nodeName'], 'serviceAccountName': s['serviceAccountName'],
            'podIP': st['podIP'], 'hostIP': st['hostIP'], 'container': 'grafana',
            'imageID': statuses[0]['imageID'], 'running_ready': True,
            'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
            'privileged_containers': False}


def project_identity(obj, kind, name, namespace=None):
    m = metadata(obj)
    if (obj.get('apiVersion') != 'v1' or obj.get('kind') != kind
            or m['name'] != name or m.get('namespace') != namespace
            or obj['metadata'].get('deletionTimestamp')):
        raise ValueError('IDENTITY_MISMATCH')
    return {'apiVersion': 'v1', 'kind': kind, 'metadata': m}


def project_list(obj, api, kind, namespace=None):
    if (obj.get('apiVersion') != api or obj.get('kind') != kind + 'List'
            or not isinstance(obj.get('items'), list)
            or obj.get('metadata', {}).get('continue')
            or obj.get('metadata', {}).get('remainingItemCount', 0)
            or len(obj['items']) > MAX_ITEMS
            or not obj.get('metadata', {}).get('resourceVersion')):
        raise ValueError('WRONG_OR_INCOMPLETE_LIST')
    rows, names, uids = [], set(), set()
    for item in obj['items']:
        m = metadata(item)
        # Typed lists may omit per-item TypeMeta. Reject an explicit mismatch.
        if (item.get('apiVersion', api) != api or item.get('kind', kind) != kind
                or m.get('namespace') != namespace or m['name'] in names or m['uid'] in uids
                or item['metadata'].get('deletionTimestamp') or not isinstance(item.get('spec'), dict)):
            raise ValueError('WRONG_OR_DUPLICATE_RESOURCE')
        names.add(m['name']); uids.add(m['uid'])
        rows.append({'apiVersion': api, 'kind': kind, 'metadata': m, 'spec': item['spec']})
    return {'apiVersion': api, 'kind': kind + 'List',
            'resourceVersion': obj['metadata']['resourceVersion'], 'items': rows}


POLICY_READS = (
    ('kubernetes-networkpolicies', '/apis/networking.k8s.io/v1/namespaces/' + NS + '/networkpolicies?limit=100', 'networking.k8s.io/v1', 'NetworkPolicy', NS),
    ('calico-networkpolicies', '/apis/crd.projectcalico.org/v1/namespaces/' + NS + '/networkpolicies?limit=100', 'crd.projectcalico.org/v1', 'NetworkPolicy', NS),
    ('globalnetworkpolicies', '/apis/crd.projectcalico.org/v1/globalnetworkpolicies?limit=100', 'crd.projectcalico.org/v1', 'GlobalNetworkPolicy', None),
    ('tiers', '/apis/crd.projectcalico.org/v1/tiers?limit=100', 'crd.projectcalico.org/v1', 'Tier', None),
    ('networksets', '/apis/crd.projectcalico.org/v1/namespaces/' + NS + '/networksets?limit=100', 'crd.projectcalico.org/v1', 'NetworkSet', NS),
    ('globalnetworksets', '/apis/crd.projectcalico.org/v1/globalnetworksets?limit=100', 'crd.projectcalico.org/v1', 'GlobalNetworkSet', None),
)


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
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        row.update(rc=125, status='INVALID_OR_INCOMPLETE_RESPONSE')
    finally:
        row['finished_kst'] = now()
    return None


def collect():
    report = {'schema': 'icr-grafana-policy-precheck/v1', 'started_kst': now(),
              'host': socket.gethostname(), 'account': pwd.getpwuid(os.geteuid()).pw_name,
              'collector_sha256': globals().get('SOURCE_SHA256', 'UNRECORDED'),
              'result': 'STOP', 'runtime_ac': 'NOT_ASSESSED', 'probe': 'NOT_RUN',
              'tool_capability': 'NOT_CHECKED', 'policy_verdict': 'NOT_ASSESSED',
              'operational_approval': 'NOT_ESTABLISHED', 'queries': []}
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
    first = query(report, kubectl, 'grafana-before', ['get', '--raw=' + POD_PATH], project_pod)
    if first is None:
        report['reason'] = 'PINNED_GRAFANA_MISMATCH_OR_QUERY_FAILED'
        return report, 2
    identities = (
        ('namespace', '/api/v1/namespaces/' + NS, 'Namespace', NS, None),
        ('serviceaccount', '/api/v1/namespaces/' + NS + '/serviceaccounts/default', 'ServiceAccount', 'default', NS),
    )
    for key, path, kind, name, namespace in identities:
        value = query(report, kubectl, key, ['get', '--raw=' + path],
                      lambda obj: project_identity(obj, kind, name, namespace))
        if value is None:
            report['reason'] = 'IDENTITY_QUERY_FAILED:' + key
            return report, 2
    for key, path, api, kind, namespace in POLICY_READS:
        value = query(report, kubectl, key, ['get', '--raw=' + path],
                      lambda obj: project_list(obj, api, kind, namespace))
        if value is None:
            report['reason'] = 'POLICY_QUERY_FAILED_OR_INCOMPLETE:' + key
            return report, 2
    last = query(report, kubectl, 'grafana-after', ['get', '--raw=' + POD_PATH], project_pod)
    if last is None or first != last:
        report['reason'] = 'GRAFANA_CHANGED_DURING_COLLECTION'
        return report, 2
    report.update(result='COLLECTED_PENDING_POLICY_REVIEW',
                  reason='Sequential policy inputs only; no atomic snapshot, policy decision or probe')
    return report, 0


if __name__ == '__main__':
    result, code = collect()
    result['finished_kst'] = now()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)
