#!/usr/bin/env bash
# Read-only. Output contains configuration in transit: use ONLY the no_log play.
python3 - <<'PY'
import base64, datetime, json, socket, subprocess, sys

HOSTS = {'k8s-control01', 'k8s-control02', 'k8s-control03', 'k8s-worker03'}
POD = 'prometheus-58c848c688-kzvhp'
UID = '46c00f91-2073-440a-800c-6cedba89d291'
CA = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
host = socket.gethostname().split('.')[0]
stage = 'identity'

def run(args):
    p = subprocess.run(args, capture_output=True, timeout=25)
    if p.returncode or len(p.stdout) > 2 * 1024 * 1024:
        raise RuntimeError('READ_FAILED')
    return p.stdout

def enc(data):
    return base64.b64encode(data).decode('ascii')

def kube(*args):
    return run(['sudo', '-n', 'kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--cache-dir=/dev/null',
                '--request-timeout=15s', *args])

def prom(*args):
    return kube('exec', '-n', 'monitoring-api', POD, '-c', 'prometheus', '--', *args)

def pod_state():
    p = json.loads(kube('get', '--raw', '/api/v1/namespaces/monitoring-api/pods/' + POD))
    s = p['spec']
    if (p['metadata']['uid'] != UID or s['nodeName'] != 'k8s-worker03'
            or s.get('serviceAccountName') != 'prometheus' or s.get('hostNetwork', False)
            or p['status'].get('podIP') != '10.244.39.219'):
        raise RuntimeError('PROM_IDENTITY_CHANGED')
    c = next(c for c in p['status']['containerStatuses'] if c['name'] == 'prometheus')
    if not c.get('ready') or not c.get('state', {}).get('running'):
        raise RuntimeError('PROM_NOT_READY')
    return {'name': POD, 'uid': UID, 'node': s['nodeName'], 'sa': s['serviceAccountName'],
            'pod_ip': p['status']['podIP'], 'container_id': c['containerID'],
            'image_id': c['imageID'], 'restart_count': c['restartCount']}

try:
    if host not in HOSTS:
        raise RuntimeError('HOST_IDENTITY_MISMATCH')
    out = {'host': host, 'time': datetime.datetime.now().astimezone().isoformat(),
           'account': run(['id', '-un']).decode().strip()}
    if host == 'k8s-worker03':
        stage = 'worker_identity'
        ips = json.loads(run(['ip', '-j', '-4', 'address', 'show', 'dev', 'ens160']))
        if not any(a.get('local') == '10.1.93.108' for i in ips for a in i['addr_info']):
            raise RuntimeError('WORKER_IP_CHANGED')
        pid = run(['systemctl', 'show', 'kubelet', '--property=MainPID', '--value']).decode().strip()
        if not pid.isdigit() or int(pid) <= 1:
            raise RuntimeError('KUBELET_NOT_RUNNING')
        out['pid'] = int(pid)
        stage = 'worker_process'
        out['argv_b64'] = enc(run(['sudo', '-n', 'cat', '/proc/' + pid + '/cmdline']))
        path = '/var/lib/kubelet/config.yaml'
        stage = 'worker_config'
        metadata = ['sudo', '-n', 'stat', '-Lc', '%u|%g|%a|%C|%s|%i|%y|%z', path]
        before_metadata = run(metadata).decode().strip()
        # These are configuration/public metadata, never kubelet.key or kubelet.conf.
        out['config_b64'] = enc(run(['sudo', '-n', 'cat', path]))
        out['metadata'] = run(metadata).decode().strip()
        if out['metadata'] != before_metadata:
            raise RuntimeError('CONFIG_CHANGED_DURING_READ')
        if pid != run(['systemctl', 'show', 'kubelet', '--property=MainPID', '--value']).decode().strip():
            raise RuntimeError('KUBELET_RESTARTED_DURING_READ')
    else:
        stage = 'signer_public_ca'
        out['signer_ca_b64'] = enc(run(['sudo', '-n', 'cat', '/etc/kubernetes/pki/ca.crt']))
        if host == 'k8s-control01':
            stage = 'prom_identity'
            before = pod_state()
            stage = 'prom_loaded_config'
            out['loaded_config_b64'] = enc(prom('wget', '-q', '-T', '10', '-O', '-',
                'http://127.0.0.1:9090/api/v1/status/config'))
            stage = 'prom_targets'
            out['targets_b64'] = enc(prom('wget', '-q', '-T', '10', '-O', '-',
                'http://127.0.0.1:9090/api/v1/targets?state=active'))
            stage = 'prom_public_ca'
            out['prom_ca_b64'] = enc(prom('cat', CA))
            stage = 'prom_identity_recheck'
            out['pod'] = pod_state()
            if out['pod'] != before:
                raise RuntimeError('PROM_CHANGED_DURING_READ')
    print(json.dumps(out))
except Exception as e:
    # Never dump command stdout/stderr, argv, configuration or tracebacks on error.
    known = str(e) if isinstance(e, RuntimeError) else type(e).__name__
    print(json.dumps({'host': host, 'stage': stage, 'collection_error': known}))
    sys.exit(1)
PY
