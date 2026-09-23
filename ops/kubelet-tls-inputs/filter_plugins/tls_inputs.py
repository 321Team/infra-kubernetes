"""Controller-only parser. Never return full configuration, argv, token or keys."""
import base64
import hashlib
import json
import re
import ssl
import subprocess
import yaml

HOSTS = ('k8s-control01', 'k8s-control02', 'k8s-control03', 'k8s-worker03')
CA = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
TOKEN = '/var/run/secrets/kubernetes.io/serviceaccount/token'
CONFIG = '/var/lib/kubelet/config.yaml'
JOBS = {'kubelet': '/metrics', 'kubelet-cadvisor': '/metrics/cadvisor'}


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key, value in node.value:
            k = self.construct_object(key, deep=deep)
            if k in result:
                raise ValueError('DUPLICATE_KEY')
            result[k] = self.construct_object(value, deep=deep)
        return result


def document(text):
    if len(text) > 2 * 1024 * 1024:
        raise ValueError('TOO_LARGE')
    # Do not silently accept YAML aliases, merge keys, multiple docs or duplicates.
    if any(isinstance(x, yaml.events.AliasEvent) for x in yaml.parse(text)):
        raise ValueError('YAML_ALIAS')
    obj = yaml.load(text, Loader=UniqueLoader)
    if not isinstance(obj, dict):
        raise ValueError('EXPECTED_MAPPING')
    return obj


def decoded(obj, key):
    return base64.b64decode(obj[key], validate=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def path(value):
    if value in (None, ''):
        return ''
    if not isinstance(value, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value):
        return 'UNSUPPORTED_PATH'
    return value


def certificates(data):
    text = data.decode('ascii')
    if 'PRIVATE KEY' in text:
        raise ValueError('NOT_PUBLIC_CERT')
    blocks = re.findall(r'-----BEGIN CERTIFICATE-----\s+.*?-----END CERTIFICATE-----', text, re.S)
    if not 1 <= len(blocks) <= 16:
        raise ValueError('CERT_BUNDLE_SIZE')
    fingerprints = set()
    for b in blocks:
        der = ssl.PEM_cert_to_DER_cert(b)
        p = subprocess.run(['openssl', 'x509', '-inform', 'DER', '-noout'],
                           input=der, capture_output=True, timeout=5)
        if p.returncode:
            raise ValueError('INVALID_CERTIFICATE')
        fingerprints.add(digest(der))
    return sorted(fingerprints)


def worker(obj):
    raw = decoded(obj, 'config_b64')
    text = raw.decode('utf-8')
    cfg = document(text)
    if cfg.get('kind') != 'KubeletConfiguration' or cfg.get('apiVersion') != 'kubelet.config.k8s.io/v1beta1':
        raise ValueError('CONFIG_SCHEMA')
    args = decoded(obj, 'argv_b64').decode('utf-8').split('\0')
    if not args or args[0].rsplit('/', 1)[-1] != 'kubelet':
        raise ValueError('PROCESS_IDENTITY')
    allowed = {'--config', '--config-dir', '--rotate-server-certificates',
               '--tls-cert-file', '--tls-private-key-file', '--feature-gates',
               '--cert-dir', '--hostname-override', '--node-ip'}
    flags = {}
    for i, arg in enumerate(args[1:], 1):
        key, sep, value = arg.partition('=')
        if key not in allowed:
            continue
        if not sep:
            value = args[i+1] if i+1 < len(args) and args[i+1] and not args[i+1].startswith('--') else 'true'
        if key == '--feature-gates':
            selected = re.findall(r'(?:^|,)RotateKubeletServerCertificate=(true|false)(?:,|$)', value)
            value = selected if selected else 'NOT_EXPLICIT'
        elif key in {'--rotate-server-certificates'}:
            value = value if value in {'true', 'false'} else 'UNSUPPORTED_VALUE'
        elif key in {'--node-ip', '--hostname-override'}:
            value = value if value in {'10.1.93.108', 'k8s-worker03'} else 'UNEXPECTED_IDENTITY'
        else:
            value = path(value)
        flags.setdefault(key, []).append(value)
    bootstrap = cfg.get('serverTLSBootstrap', 'NOT_EXPLICIT')
    if bootstrap != 'NOT_EXPLICIT' and type(bootstrap) is not bool:
        raise ValueError('BOOTSTRAP_TYPE')
    a = cfg.get('authentication', {})
    gates = cfg.get('featureGates', {})
    out = {'pid': obj['pid'], 'config_sha256': digest(raw), 'config_metadata': obj['metadata'],
           'yaml_unique_single_mapping': True, 'document_end_marker': bool(re.search(r'^\.\.\.(?:\s|$)', text, re.M)),
           'serverTLSBootstrap': bootstrap, 'rotateCertificates': cfg.get('rotateCertificates', 'NOT_EXPLICIT'),
           'tlsCertFile': path(cfg.get('tlsCertFile')), 'tlsPrivateKeyFile': path(cfg.get('tlsPrivateKeyFile')),
           'RotateKubeletServerCertificate': gates.get('RotateKubeletServerCertificate', 'NOT_EXPLICIT'),
           'authentication': {'anonymous_enabled': a.get('anonymous', {}).get('enabled', 'NOT_EXPLICIT'),
                              'webhook_enabled': a.get('webhook', {}).get('enabled', 'NOT_EXPLICIT'),
                              'client_ca_file': path(a.get('x509', {}).get('clientCAFile'))},
           'authorization_mode': cfg.get('authorization', {}).get('mode', 'NOT_EXPLICIT'),
           'selected_process_flags': flags}
    pending = []
    if flags.get('--config') != [CONFIG]:
        pending.append('KUBELET_CONFIG_PATH_NOT_EXACT')
    if any(k in flags for k in ('--config-dir', '--tls-cert-file', '--tls-private-key-file', '--rotate-server-certificates')):
        pending.append('EXPLICIT_CLI_OVERRIDE_REQUIRES_REVIEW')
    if flags.get('--feature-gates') not in (None, ['NOT_EXPLICIT'], [['true']]):
        pending.append('CLI_SERVING_GATE_REQUIRES_REVIEW')
    if any(len(v) != 1 for v in flags.values()):
        pending.append('DUPLICATE_CLI_FLAG')
    if out['tlsCertFile'] or out['tlsPrivateKeyFile']:
        pending.append('EXPLICIT_LOCAL_TLS_FILES_REQUIRES_REVIEW')
    if out['document_end_marker'] or bootstrap is True:
        pending.append('SINGLE_LINE_CHANGE_PRECONDITION_CHANGED')
    if text.lstrip().startswith('{') or (bootstrap != 'NOT_EXPLICIT' and not re.search(
            r'^serverTLSBootstrap:[ \t]*(?:true|false)[ \t]*(?:#.*)?$', text, re.M)):
        pending.append('LINE_EDIT_FORMAT_REQUIRES_REVIEW')
    if out['RotateKubeletServerCertificate'] not in ('NOT_EXPLICIT', True):
        pending.append('LOCAL_SERVING_GATE_REQUIRES_REVIEW')
    if out['authentication']['anonymous_enabled'] is not False or out['authentication']['webhook_enabled'] is not True or out['authorization_mode'] != 'Webhook':
        pending.append('LOCAL_AUTH_SETTINGS_REQUIRE_REVIEW')
    out['pending'] = pending
    return out


def api(obj, key):
    x = json.loads(decoded(obj, key))
    if x.get('status') != 'success':
        raise ValueError('PROM_API_FAILURE')
    return x['data']


def prometheus(obj):
    loaded = api(obj, 'loaded_config_b64')['yaml']
    cfg = document(loaded)
    selected = [j for j in cfg['scrape_configs'] if j.get('job_name') in JOBS]
    if len(selected) != 2 or {j['job_name'] for j in selected} != set(JOBS):
        raise ValueError('EXPECTED_TWO_JOBS')
    jobs, pending = [], []
    for j in selected:
        name = j['job_name']
        t = j.get('tls_config', {})
        a = j.get('authorization', {})
        server_name = t.get('server_name', '')
        server_name = server_name if isinstance(server_name, str) and re.fullmatch(r'[A-Za-z0-9.-]*', server_name) else 'UNSUPPORTED_VALUE'
        ref = {'name': name, 'scheme': j.get('scheme'), 'metrics_path': j.get('metrics_path', '/metrics'),
               'ca_file': path(t.get('ca_file')), 'server_name': server_name,
               'insecure_skip_verify': t.get('insecure_skip_verify', False),
               'bearer_token_file': path(j.get('bearer_token_file')),
               'credentials_file': path(a.get('credentials_file')),
               'authorization_type': a.get('type', 'Bearer'),
               'inline_auth_present': bool(j.get('bearer_token') or a.get('credentials')),
               'other_auth_present': any(k in j for k in ('basic_auth', 'oauth2')),
               'inline_ca_present': bool(t.get('ca')),
               'client_cert_or_key_present': any(t.get(k) for k in ('cert', 'key', 'cert_file', 'key_file')),
               'proxy_config_present': any(j.get(k) for k in ('proxy_url', 'proxy_from_environment', 'proxy_connect_header'))}
        if not (ref['scheme'] == 'https' and ref['metrics_path'] == JOBS[name]
                and ref['ca_file'] == CA and not ref['server_name']
                and ref['insecure_skip_verify'] is False
                and ((ref['bearer_token_file'] == TOKEN and not ref['credentials_file'])
                     or (ref['credentials_file'] == TOKEN and not ref['bearer_token_file'] and ref['authorization_type'] == 'Bearer'))
                and not any(ref[k] for k in ('inline_auth_present', 'other_auth_present', 'inline_ca_present', 'client_cert_or_key_present', 'proxy_config_present'))):
            pending.append(name + ':TRUST_OR_AUTH_REFERENCE_REQUIRES_REVIEW')
        jobs.append(ref)
    active = api(obj, 'targets_b64')['activeTargets']
    counts = {k: sum(t.get('health') == k for t in active) for k in ('up', 'down', 'unknown')}
    targets = []
    for name, metrics_path in JOBS.items():
        url = 'https://10.1.93.108:10250' + metrics_path
        found = [t for t in active if t.get('scrapePool') == name and t.get('scrapeUrl') == url]
        if len(found) != 1:
            pending.append(name + ':WORKER03_TARGET_NOT_UNIQUE')
            continue
        t = found[0]
        error = t.get('lastError', '')
        # No raw error/URL headers or credentials are retained.
        kind = 'NONE' if not error else next((k for needle, k in (
            ('IP SAN', 'IP_SAN'), ('unknown authority', 'UNKNOWN_CA'), ('deadline', 'TIMEOUT'),
            ('401', 'HTTP_401'), ('403', 'HTTP_403')) if needle in error), 'OTHER')
        targets.append({'job': name, 'url': url, 'health': t['health'],
                        'last_scrape': t['lastScrape'], 'error_class': kind})
    return {'pod': obj['pod'], 'loaded_config_sha256': digest(loaded.encode()), 'jobs': jobs,
            'prom_ca_der_sha256': certificates(decoded(obj, 'prom_ca_b64')),
            'target_counts': {'total': len(active), **counts}, 'worker03_targets': targets, 'pending': pending}


def report(result, expected):
    out = {'host': expected, 'collection_ok': False}
    try:
        if result.get('rc') != 0 or result.get('unreachable'):
            out['error'] = 'REMOTE_READ_FAILED'
            try:
                error = json.loads(result.get('stdout', '{}'))
                stages = {'identity', 'worker_identity', 'worker_process', 'worker_config',
                          'signer_public_ca', 'prom_identity', 'prom_loaded_config', 'prom_targets',
                          'prom_public_ca', 'prom_identity_recheck'}
                if error.get('stage') in stages:
                    out['stage'] = error['stage']
                codes = {'READ_FAILED', 'HOST_IDENTITY_MISMATCH', 'WORKER_IP_CHANGED',
                         'KUBELET_NOT_RUNNING', 'KUBELET_RESTARTED_DURING_READ',
                         'CONFIG_CHANGED_DURING_READ', 'PROM_IDENTITY_CHANGED',
                         'PROM_NOT_READY', 'PROM_CHANGED_DURING_READ', 'TimeoutExpired',
                         'FileNotFoundError', 'KeyError', 'JSONDecodeError', 'StopIteration'}
                if error.get('collection_error') in codes:
                    out['detail'] = error['collection_error']
            except Exception:
                pass
            return out
        obj = json.loads(result['stdout'])
        if expected not in HOSTS or obj['host'] != expected or obj.get('collection_error'):
            raise ValueError('HOST_OR_READ_MISMATCH')
        out.update({'time': obj['time'], 'account': obj['account']})
        if expected == 'k8s-worker03':
            out.update(worker(obj))
        else:
            out['signer_ca_der_sha256'] = certificates(decoded(obj, 'signer_ca_b64'))
            if expected == 'k8s-control01':
                out.update(prometheus(obj))
        out['collection_ok'] = True
    except Exception:
        # Exception text may contain the offending YAML/config value; never echo it.
        out['error'] = 'PARSE_OR_SCHEMA_FAILED'
    return out


def summary(hostvars):
    reports = {h: hostvars[h].get('tls_report', {}) if h in hostvars else {} for h in HOSTS}
    ok = all(r.get('collection_ok') is True for r in reports.values())
    pending = ['INDEPENDENT_PR31_REVIEW', 'CHANGE_WINDOW_AND_RECOVERY_BACKUP',
               'SSH_RECOVERY_WRITE_PRIVILEGE_AND_CSR_REVIEWER', 'SIGNER_ISSUANCE_NOT_TESTED',
               'NEW_CERTIFICATE_TLS_AND_SCRAPE_ACCEPTANCE_NOT_TESTED']
    pending += [h + ':' + p for h, r in reports.items() for p in r.get('pending', [])]
    match = False
    if ok:
        trust = set(reports['k8s-control01']['prom_ca_der_sha256'])
        cas = [reports[h]['signer_ca_der_sha256'] for h in HOSTS[:3]]
        match = all(len(c) == 1 and c == cas[0] and c[0] in trust for c in cas)
        if not match:
            pending.append('SIGNER_CA_SET_OR_PROM_TRUST_MISMATCH')
    else:
        pending.append('INCOMPLETE_COLLECTION')
    return {'collection_ok': ok, 'all_three_signer_ca_in_prom_bundle': match,
            'pending': pending, 'runtime': 'NOT_RUN', 'change_authorized_by_this_result': False,
            'limits': ['Fingerprint membership is not chain verification or signer key availability.',
                       'Only the current mounted public CA file and loaded config references were compared.',
                       'No service-account token was read; no CSR was created or approved.']}


class FilterModule:
    def filters(self):
        return {'tls_input_report': report, 'tls_input_summary': summary}
