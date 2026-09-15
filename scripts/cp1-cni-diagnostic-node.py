#!/usr/bin/env python3
"""CP1 local job plus independent, boot-persistent recovery service.

Invoked by the pinned controller. No firewall operation occurs on import.
"""
import contextlib
import base64
import copy
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path('/var/lib/fds/icr-d-net-01-01/cni-diagnostic')
UNITS = Path('/etc/systemd/system')
RECEIPT = Path('/var/lib/fds/icr-d-net-01-01/cp1-canary/20260914T095827Z-7938c6e73fbd.json')
RECEIPT_SHA = 'a1380a68cb8f39f5562b938f47caa315fb3074d6f50e6d21cf657d2d159ee0c1'
IMAGE = 'docker.io/library/python@sha256:46ee549c88617e9bc8acb843a326f1a5c0fa5608d7f9703509efe6d53b55f318'
FILES = ('cni_diag_core.py', 'cp1-cni-diagnostic-node.py', 'node-firewall.py')


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_dir(path, create=False):
    for p in reversed([path] + list(path.parents)):
        if create and not p.exists():
            p.mkdir(mode=0o700)
        st = p.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022,
             'Unsafe root directory: ' + str(p))


def safe_read(path):
    safe_dir(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022,
             'Unsafe root file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(8_000_001)
    finally:
        os.close(fd)


def atomic(path, content, exclusive=False):
    safe_dir(path.parent)
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        fd, tmp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_json(path, data, exclusive=False):
    atomic(path, (json.dumps(data, indent=2, sort_keys=True) + '\n').encode(), exclusive)


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unit_names(job):
    return 'fds-cni-job-' + job.name, 'fds-cni-recover-' + job.name


def unit_documents(job, seconds):
    work, recovery = unit_names(job)
    program = str(job / 'cp1-cni-diagnostic-node.py')
    service = ('[Unit]\nDescription=FDS CP1 diagnostic recovery\n'
               'After=firewalld.service NetworkManager.service\n'
               '[Service]\nType=oneshot\nTimeoutStartSec=150\nUMask=0077\n'
               'ExecStart=/usr/bin/python3 -I -B ' + program + ' recover ' + str(job) + '\n'
               '[Install]\nWantedBy=multi-user.target\n')
    timer = ('[Unit]\nDescription=FDS CP1 diagnostic deadline and recovery retry\n'
             '[Timer]\nOnActiveSec=' + str(max(1, int(seconds))) + 's\n'
             'OnUnitInactiveSec=30s\nAccuracySec=1s\nUnit=' + recovery + '.service\n'
             '[Install]\nWantedBy=timers.target\n')
    return {recovery + '.service': service, recovery + '.timer': timer}


def system(argv, timeout=15):
    result = subprocess.run(['/usr/bin/systemctl'] + argv, text=True, capture_output=True,
                            timeout=timeout, env={'PATH': '/usr/sbin:/usr/bin', 'LC_ALL': 'C.UTF-8'})
    need(result.returncode == 0, 'systemctl failed: ' + ' '.join(argv[:2]))
    return result.stdout.strip()


@contextlib.contextmanager
def lock(path, nonblock=False):
    safe_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        need(st.st_uid == 0 and stat.S_ISREG(st.st_mode) and not st.st_mode & 0o077, 'Unsafe lock')
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblock else 0))
        yield
    finally:
        os.close(fd)


def install(data):
    need(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'k8s-control01', 'CP1 root required')
    request, sources = data['request'], data['sources']
    need(set(sources) == set(FILES), 'Unexpected source bundle')
    need(re.fullmatch(r'[0-9]{14}-[a-f0-9]{12}', request['run_id']), 'Invalid run ID')
    need(time.time() <= request['issued_at'] + 60 and time.time() >= request['issued_at'] - 5,
         'Remote launch expired or clock offset exceeds 5 seconds')
    safe_dir(ROOT, create=True)
    job = ROOT / request['run_id']
    with lock(ROOT / 'launch.lock', nonblock=True):
        active = ROOT / 'active.json'
        if active.exists():
            old = json.loads(safe_read(active))
            need(re.fullmatch(r'[0-9]{14}-[a-f0-9]{12}', old.get('run_id', '')), 'Invalid active job record')
            result_path = ROOT / old['run_id'] / 'STATE.json'
            state = json.loads(safe_read(result_path)) if result_path.exists() else {}
            need(state.get('cleanup_complete') is True, 'Prior job needs recovery; do not repeat')
        job.mkdir(mode=0o700)
        for name, content in sources.items():
            need(sha(content.encode()) == request['files_sha256'][name], 'Source digest mismatch')
            atomic(job / name, content.encode(), exclusive=True)
        # Total time starts at the controller after its local preflight, not at activation.
        remaining = min(1200, request['issued_at'] + 1200 - time.time())
        request['budget'] = {'boot_id': boot_id(), 'boot_start': time.clock_gettime(time.CLOCK_BOOTTIME)
                             - (1200 - remaining), 'wall_end': request['issued_at'] + 1200}
        save_json(job / 'REQUEST.json', request, exclusive=True)
        save_json(job / 'STATE.json', {'run_id': request['run_id'], 'phase': 'STAGED',
                                      'cleanup_complete': False}, exclusive=True)
        save_json(active, {'run_id': request['run_id']})
        units = unit_documents(job, remaining - 180)
        save_json(job / 'UNITS.json', units, exclusive=True)
        for name, content in units.items():
            atomic(UNITS / name, content.encode(), exclusive=True)
        system(['daemon-reload'])
        work, recovery = unit_names(job)
        system(['enable', recovery + '.service'])  # Immediate recovery on subsequent boot.
        system(['enable', '--now', recovery + '.timer'])
        need(system(['is-active', recovery + '.timer']) == 'active', 'Recovery timer inactive')
        need(system(['is-enabled', recovery + '.service']) == 'enabled', 'Boot recovery not enabled')
        deadline = system(['show', recovery + '.timer', '-p', 'NextElapseUSecMonotonic', '--value'])
        need(deadline and deadline != '0', 'Recovery deadline not scheduled')
        save_json(job / 'ARMED.json', {'timer': recovery + '.timer', 'next_elapse': deadline,
                                     'boot_service': recovery + '.service', 'armed_at': time.time()})
        command = ['/usr/bin/systemd-run', '--unit=' + work, '--property=Type=exec',
                   '--property=KillMode=control-group', '--property=TimeoutStopSec=5', '--property=UMask=0077',
                   '--property=RuntimeMaxSec=' + str(max(1, int(remaining - 180))),
                   '/usr/bin/python3', '-I', '-B', str(job / FILES[1]), 'run', str(job)]
        p = subprocess.run(command, capture_output=True, text=True, timeout=20)
        need(p.returncode == 0, 'Job launch failed; recovery remains armed')
    print(json.dumps({'run_id': request['run_id'], 'job': str(job), 'status': 'STARTED',
                      'recovery': recovery + '.timer'}))


class Linux:
    def __init__(self, job, recovery=False):
        self.job = job
        self.request = json.loads(safe_read(job / 'REQUEST.json'))
        for name in FILES:
            need(sha(safe_read(job / name)) == self.request['files_sha256'][name], 'Staged source changed')
        self.core = load_module('cni_core', job / FILES[0])
        self.helper = load_module('cni_helper', job / FILES[2])
        self.budget = self.core.Budget(self.request['budget'], boot_id())
        self.recovery = recovery
        self.boot_changed = self.request['budget']['boot_id'] != boot_id()
        self.cleanup_mode = recovery
        # Recovery attempts are bounded and retried. They never restart diagnosis.
        self.recovery_deadline = time.monotonic() + 110 if recovery else None
        self.result = json.loads(safe_read(job / 'STATE.json'))
        self.result.update(self.core.FLAGS)

    def save(self, data):
        self.result = data
        save_json(self.job / 'STATE.json', data)

    def guard(self, cleanup=False):
        cleanup = cleanup or self.cleanup_mode
        if not cleanup:
            need(not (self.job / 'STOP').exists(), 'Recovery requested')
            self.budget.remaining()
        elif self.recovery:
            need(time.monotonic() < self.recovery_deadline - 1, 'Recovery attempt budget exhausted')
        else:
            self.budget.remaining(cleanup=True)

    def timeout(self, limit, cleanup=False):
        self.guard(cleanup)
        if self.recovery:
            return min(limit, self.recovery_deadline - time.monotonic() - 1)
        return min(limit, self.budget.remaining(cleanup=cleanup or self.cleanup_mode) - 0.5)

    def cmd(self, argv, payload=None, limit=15, cleanup=False, accepted=(0,)):
        timeout = self.timeout(limit, cleanup)
        binary = self.helper.binary(argv[0])
        rec = {'argv': argv, 'started_at': time.time()}
        output = None
        try:
            # Temporary files prevent unbounded pipe buffering; never publish raw Secrets.
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                p = subprocess.Popen(argv, executable=binary, stdin=subprocess.PIPE if payload is not None
                                     else subprocess.DEVNULL, stdout=out, stderr=err,
                                     env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
                                     start_new_session=True)
                try:
                    p.communicate(None if payload is None else payload.encode(), timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
                    rec.update(rc=None, error='timeout; outcome may be unknown')
                    raise
                out.seek(0); err.seek(0)
                stdout, stderr = out.read(4_000_001), err.read(16001)
                need(len(stdout) <= 4_000_000 and len(stderr) <= 16000, 'Command output too large')
                output = stdout.decode('utf-8')
                rec.update(rc=p.returncode, stdout=self.helper.redact(output),
                           stderr=self.helper.redact(stderr.decode('utf-8')))
                need(p.returncode in accepted, 'Command failed: ' + ' '.join(argv[:3]))
                return output.strip()
        finally:
            rec['ended_at'] = time.time()
            log = (json.dumps(rec, sort_keys=True) + '\n').encode()
            fd = os.open(self.job / 'COMMANDS.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'ab') as stream:
                stream.write(log); stream.flush(); os.fsync(stream.fileno())

    def command(self, args, cleanup=False):
        return self.cmd(['firewall-cmd'] + args, cleanup=cleanup)

    def kubectl(self, args, obj=None):
        return self.cmd(['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
                         '--cache-dir=' + str(self.job / 'cache'), '--request-timeout=8s'] + args,
                        payload=None if obj is None else json.dumps(obj), limit=15)

    def get(self, resource, namespace=None, name=None):
        args = ['get', resource]
        if name:
            args += [name]
        if namespace:
            args += ['-n', namespace]
        return json.loads(self.kubectl(args + ['-o', 'json']))

    def snapshot(self):
        snap = {'default': self.command(['--get-default-zone'])}
        for plane in ('runtime', 'permanent'):
            prefix = ['--permanent'] if plane == 'permanent' else []
            snap[plane] = {
                'zones': self.core.parse_objects(self.command(prefix + ['--list-all-zones'])),
                'policies': self.core.parse_objects(self.command(prefix + ['--list-all-policies'])),
                'direct': self.command(prefix + ['--direct', '--get-all-rules'])}
            if plane == 'permanent':
                for kind, name in (('zone', self.core.ZONE), ('policy', self.core.POLICY)):
                    collection = 'zones' if kind == 'zone' else 'policies'
                    if name in snap[plane][collection]:
                        marker = self.command(['--permanent', '--' + kind + '=' + name, '--get-description'])
                        need(marker == self.request['run_id'], 'Permanent object belongs to another run')
        return snap

    def legacy_snapshot(self):
        def bounded(argv, timeout=15):
            return {'rc': 0, 'stdout': self.cmd(argv, limit=timeout), 'stderr': '',
                    'stdout_truncated': False, 'stderr_truncated': False}
        self.helper.command = bounded
        backend = self.helper.BaseBackend(self.core.NODE)
        try:
            return backend.snapshot()
        finally:
            backend.cache.cleanup()

    def configuration(self):
        files = {}
        for directory in ('/etc/NetworkManager', '/run/NetworkManager/system-connections',
                          '/etc/sysconfig/network-scripts'):
            root = Path(directory)
            if not root.exists():
                continue
            for path in sorted(root.rglob('*')):
                st = path.lstat()
                if stat.S_ISREG(st.st_mode):
                    files[str(path)] = [sha(path.read_bytes()), st.st_uid, stat.S_IMODE(st.st_mode)]
                elif stat.S_ISLNK(st.st_mode):
                    files[str(path)] = ['symlink', os.readlink(path)]
        return files

    def health(self):
        nodes = self.get('nodes')['items']
        need(len(nodes) == 1 and nodes[0]['metadata']['name'] == self.core.NODE, 'Single CP1 required')
        need(any(c['type'] == 'Ready' and c['status'] == 'True'
                 for c in nodes[0]['status'].get('conditions', [])), 'CP1 not Ready')
        need(self.kubectl(['get', '--raw=/readyz']) == 'ok', 'API not ready')
        stable = {}
        pods = self.get('pods', 'kube-system')['items']
        need(pods, 'No system Pods')
        for p in pods:
            statuses = p['status'].get('containerStatuses', [])
            need(p['status']['phase'] == 'Running' and statuses and all(c['ready'] for c in statuses),
                 'Unhealthy system Pod')
            stable[p['metadata']['uid']] = [[c['name'], c['restartCount']] for c in statuses]
        return {'node_uid': nodes[0]['metadata']['uid'], 'system_pods': stable,
                'nm_files': self.configuration()}

    def management(self):
        need(self.health() == self.result['health_before'], 'Management/API/system Pod/NM drift')
        if not self.cleanup_mode and not self.result.get('management_ack'):
            nonce = secrets.token_hex(16)
            self.result.update(phase='WAIT_CONTROLLER_SSH', management_nonce=nonce)
            self.save(self.result)
            until = time.monotonic() + self.timeout(45)
            while time.monotonic() < until:
                self.guard()
                path = self.job / 'ACK.json'
                if path.exists():
                    ack = json.loads(safe_read(path))
                    need(ack['nonce'] == nonce and 0 <= time.time() - ack['observed_at'] <= 30,
                         'Management SSH acknowledgement mismatch/expired')
                    self.result.update(management_ack=ack, phase='MANAGEMENT_CONFIRMED')
                    self.save(self.result)
                    return
                time.sleep(0.2)
            raise ValueError('No new controller SSH acknowledgement after reload; withdraw')

    def links_routes(self):
        return (json.loads(self.cmd(['ip', '-j', '-d', 'link', 'show'])),
                json.loads(self.cmd(['ip', '-j', '-4', 'route', 'show', 'table', 'main'])))

    def peer_identity(self):
        ns = self.request['namespace']
        meta = self.get('namespace', name=ns)['metadata']
        need(meta['uid'] == self.result['namespace_uid'], 'Namespace UID changed')
        pods = self.get('pods', ns)['items']
        need({p['metadata']['name'] for p in pods} == {'a', 'b'} and len(pods) == 2, 'Trial Pod set differs')
        links, routes = self.links_routes()
        peers = []
        program = ('import json,pathlib;print(json.dumps({k:int(pathlib.Path("/sys/class/net/eth0/"+k)'
                   '.read_text()) for k in ("ifindex","iflink")}))')
        for p in sorted(pods, key=lambda p: p['metadata']['name']):
            need(p['metadata']['labels'].get('fds-cni-trial') == ns and
                 p['spec']['containers'][0]['image'] == IMAGE and not p['spec'].get('hostPID') and
                 not p['spec'].get('hostIPC'), 'Trial Pod spec/owner differs')
            inside = json.loads(self.kubectl(['exec', '-n', ns, p['metadata']['name'], '-c', 'probe',
                                              '--', 'python3', '-I', '-B', '-c', program]))
            link = next((v for v in links if v['ifindex'] == inside['iflink']), None)
            need(link, 'Pod veth peer missing')
            peers.append({'name': p['metadata']['name'], 'uid': p['metadata']['uid'],
                          'namespace_uid': meta['uid'], 'node': p['spec'].get('nodeName'),
                          'ip': p['status']['podIP'], 'host_network': p['spec'].get('hostNetwork', False),
                          'pod_ifindex': inside['ifindex'], 'pod_iflink': inside['iflink'],
                          'host_ifindex': link['ifindex'], 'interface': link['ifname']})
        self.core.validate_peers(peers, meta['uid'], links, routes, self.result['system_interfaces'])
        return peers

    def verify_peers(self, peers, bound):
        need(self.peer_identity() == peers, 'Trial Pod/interface identity changed')
        snap = self.snapshot()
        for p in peers:
            interface = p['interface']
            need(self.cmd(['nmcli', '-g', 'GENERAL.MANAGED', 'device', 'show', interface]) == 'no',
                 'Trial veth is NetworkManager managed')
            # Existing ifcfg files can redirect firewall-cmd into persistent updates.
            for path in Path('/etc/sysconfig/network-scripts').glob('ifcfg-*'):
                need(path.name != 'ifcfg-' + interface and interface not in path.read_text(errors='strict'),
                     'Trial veth has an ifcfg association')
            for plane in ('runtime', 'permanent'):
                owners = [name for name, obj in snap[plane]['zones'].items()
                          if interface in obj['interfaces'].split()]
                need(owners == ([self.core.ZONE] if plane == 'runtime' and bound else []),
                     'Trial veth already classified or unexpected binding')
        need(self.configuration() == self.result['health_before']['nm_files'], 'NM/ifcfg configuration changed')

    def verify_cleanup_interface(self, interface, peers):
        peer = next((p for p in peers if p['interface'] == interface), None)
        need(peer, 'Refuse unowned interface cleanup')
        links, routes = self.links_routes()
        link = next((p for p in links if p['ifname'] == interface), None)
        if link:
            need(link['ifindex'] == peer['host_ifindex'] and
                 link.get('link_index') == peer['pod_ifindex'] and
                 link.get('linkinfo', {}).get('info_kind') == 'veth', 'Interface name reused; do not unbind')
        # Missing owned veth is not resurrected. Its stale binding may be removed.

    def interface_gone(self, interface, peers):
        need(interface in {p['interface'] for p in peers}, 'Unexpected interface in cleanup snapshot')
        links, _ = self.links_routes()
        return not any(link['ifname'] == interface for link in links)

    def emergency_disable(self):
        """Revoke a journal-matched Runtime policy even if API/NM/full snapshot fails."""
        candidates = [self.result.get('expected', {})]
        intent = self.result.get('intent')
        if intent:
            candidates += [intent['before'], intent['after']]
        known = [v.get('runtime', {}).get('policies', {}).get(self.core.POLICY) for v in candidates]
        known = [v for v in known if v]
        if not known or self.core.POLICY not in self.command(['--get-policies'], cleanup=True).split():
            return
        current = self.core.parse_objects(self.command(['--info-policy=' + self.core.POLICY], cleanup=True))[self.core.POLICY]
        if current['disable'] == 'yes':
            return
        need(current in known, 'Emergency revocation refused: runtime policy differs from owned journal')
        self.command(['--policy=' + self.core.POLICY, '--add-disable'], cleanup=True)
        need(self.command(['--policy=' + self.core.POLICY, '--query-disable'], cleanup=True) == 'yes',
             'Emergency revocation not confirmed')
        self.result['emergency_runtime_disabled'] = True
        self.save(self.result)

    def preflight(self):
        need(socket.gethostname().split('.')[0] == self.core.NODE and os.geteuid() == 0, 'CP1 root required')
        self.guard()
        armed = json.loads(safe_read(self.job / 'ARMED.json'))
        need(system(['is-active', armed['timer']]) == 'active', 'Recovery not armed')
        receipt = safe_read(RECEIPT)
        need(sha(receipt) == RECEIPT_SHA, 'Original CP1 receipt hash mismatch')
        original = json.loads(receipt)
        need(original['status'] == 'RUNTIME_APPLIED_REVIEW_REQUIRED' and
             original['hostname'] == self.core.NODE, 'Original receipt invalid')
        self.result['original_receipt_sha256'] = sha(receipt)
        self.result['original_after'] = original['after']
        need(self.legacy_snapshot() == original['after'], 'CP1 differs from final receipt')
        state = self.snapshot()
        self.core.validate_reload_baseline(state)
        versions = self.cmd(['rpm', '-q', 'firewalld', 'nftables', 'NetworkManager', 'iptables-nft']).splitlines()
        need(versions == self.request['qualification']['packages'], 'Qualified package versions differ')
        self.helper.binary('tcpdump')
        self.helper.binary('conntrack')
        self.result['packages'] = versions
        need(not self.get('hostendpoints.crd.projectcalico.org')['items'], 'HEP transition already started')
        need(not self.get('globalnetworkpolicies.crd.projectcalico.org')['items'], 'GNP transition already started')
        self.result['health_before'] = self.health()
        links, routes = self.links_routes()
        self.result['system_interfaces'] = [x['ifname'] for x in links if x['ifname'].startswith('cali')]
        blocks = self.get('ipamblocks.crd.projectcalico.org')['items']
        need(any(b['spec']['cidr'] == str(self.core.BLOCK) and
                 b['spec'].get('affinity') == 'host:' + self.core.NODE for b in blocks), 'CP1 IPAM block not owned')
        self.result.update(baseline=state, expected=state, phase='PREFLIGHT_OK')
        self.save(self.result)

    def create_workload(self):
        ns = self.request['namespace']
        need(not self.kubectl(['get', 'namespace', ns, '--ignore-not-found', '-o', 'json']), 'Namespace collision')
        objects = manifests(ns)
        self.result['namespace_create_intent'] = True
        self.save(self.result)
        raw = self.kubectl(['create', '-f', '-', '-o', 'json'], objects[0])
        self.result['namespace_uid'] = json.loads(raw)['metadata']['uid']
        self.save(self.result)
        for obj in objects[1:]:
            self.kubectl(['create', '-f', '-', '-o', 'json'], obj)
        wait_until = min(time.monotonic() + 240, time.monotonic() + self.budget.remaining() - 120)
        while time.monotonic() < wait_until:
            pods = self.get('pods', ns)['items']
            if len(pods) == 2 and all(any(c['type'] == 'Ready' and c['status'] == 'True'
                                      for c in p['status'].get('conditions', [])) for p in pods):
                break
            time.sleep(2)
        else:
            raise ValueError('Trial Pods not Ready')
        peers = self.peer_identity()
        self.result['peers'] = peers
        self.save(self.result)
        self.verify_peers(peers, bound=False)
        for name, value in self.core.xml_documents(self.request['run_id'], peers).items():
            atomic(self.job / name, value.encode(), exclusive=True)
        return peers

    def probes(self, peers, bound=True, include_negative=True):
        ns = self.request['namespace']
        service = self.get('service', ns, 'peer-b')
        svc_ip = str(ipaddress.IPv4Address(service['spec']['clusterIP']))
        results = {}
        for a, b in ((0, 1), (1, 0)):
            self.verify_peers(peers, bound=bound)
            request = {'peer': peers[b]['ip'], 'expected': ns + '/' + peers[b]['name'],
                       'dns': 'peer-b.' + ns + '.svc', 'service': svc_ip,
                       'service_http': a == 0,
                       'source_port': self.fresh_port(peers[a]['ip'], peers[b]['ip'], 8080)}
            raw = self.kubectl(['exec', '-n', ns, peers[a]['name'], '-c', 'probe', '--',
                                'python3', '-I', '-B', '-c', PROBE, json.dumps(request)])
            results[peers[a]['name']] = json.loads(raw)
            if include_negative:
                results[peers[a]['name']]['negative_8081'] = self.negative_probe(peers[a], peers[b])
        return results

    def fresh_port(self, source, destination, port):
        for _ in range(5):
            value = 40000 + secrets.randbelow(20000)
            existing = self.cmd(['conntrack', '-L', '-f', 'ipv4', '-p', 'tcp',
                                 '--orig-src', source, '--orig-dst', destination,
                                 '--sport', str(value), '--dport', str(port)])
            if not existing:
                return value
        raise ValueError('Could not allocate a conntrack-free direct flow')

    def negative_probe(self, source, destination):
        """Require listener confirmation and ingress SYN capture for negative flow verdict.

        This observes end-to-end denial, not the identity of a denying component.
        """
        ns = self.request['namespace']
        selftest = ('import http.client;c=http.client.HTTPConnection("127.0.0.1",8081,timeout=2);'
                    'c.request("GET","/");r=c.getresponse();print(r.status);c.close()')
        need(self.kubectl(['exec', '-n', ns, destination['name'], '-c', 'probe', '--',
                          'python3', '-I', '-B', '-c', selftest]) == '200', 'Negative port listener not confirmed')
        capture = self.job / ('negative-' + source['name'] + '.pcap')
        need(not capture.exists(), 'Capture file collision')
        source_port = self.fresh_port(source['ip'], destination['ip'], 8081)
        expression = ('src host ' + source['ip'] + ' and dst host ' + destination['ip'] +
                      ' and tcp src port ' + str(source_port) +
                      ' and tcp dst port 8081 and tcp[tcpflags] & tcp-syn != 0')
        binary = self.helper.binary('tcpdump')
        with tempfile.TemporaryFile() as err:
            proc = subprocess.Popen([binary, '-Z', 'root', '-nn', '-i', source['interface'], '-U', '-c', '1',
                                     '-s', '128', '-w', str(capture), expression],
                                    stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
            try:
                ready_until = time.monotonic() + self.timeout(3)
                while time.monotonic() < ready_until:
                    err.seek(0)
                    if 'listening on' in err.read(4000).decode(errors='replace'):
                        break
                    need(proc.poll() is None, 'Capture process exited before probe')
                    time.sleep(0.05)
                else:
                    raise ValueError('Capture did not become ready')
                probe = ('import socket,json; s=socket.socket();s.settimeout(3);s.bind(("",' + str(source_port) + '));'
                         'print(json.dumps({"connect_errno":s.connect_ex((' + repr(destination['ip']) +
                         ',8081))}));s.close()')
                observed = json.loads(self.kubectl(['exec', '-n', ns, source['name'], '-c', 'probe', '--',
                                                   'python3', '-I', '-B', '-c', probe]))
                try:
                    proc.wait(timeout=self.timeout(2))
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGINT)
                    proc.wait(timeout=2)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL); proc.wait()
            err.seek(0)
            report = err.read(8000).decode(errors='replace')
        arrived = proc.returncode == 0 and bool(re.search(r'\b1 packet captured\b', report))
        observed.update(ingress_arrival_observed=arrived, listener_confirmed=True, source_port=source_port,
                        fresh_direct_flow=True, packet_capture_sha256=sha(safe_read(capture)) if capture.exists() else None,
                        capture_report=report, component_attribution='NOT_PROVEN',
                        verdict='PASS_NEGATIVE_FLOW' if arrived and observed['connect_errno'] != 0
                        else ('FAIL' if observed['connect_errno'] == 0 else 'NOT_VERIFIED'))
        return observed

    def cleanup_namespace(self):
        ns = self.request['namespace']
        if not self.result.get('namespace_create_intent'):
            return
        raw = self.kubectl(['get', 'namespace', ns, '--ignore-not-found', '-o', 'json'])
        if raw:
            meta = json.loads(raw)['metadata']
            # Unknown create outcome is a STOP, never ownership inferred only from a label.
            need(self.result.get('namespace_uid') and meta['uid'] == self.result['namespace_uid'] and
                 meta.get('labels', {}).get('fds-cni-trial') == ns, 'Namespace ownership not proven')
            options = {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                       'preconditions': {'uid': meta['uid']}, 'propagationPolicy': 'Foreground'}
            self.kubectl(['delete', '--raw=/api/v1/namespaces/' + ns, '-f', '-'], options)
            for _ in range(25):
                if not self.kubectl(['get', 'namespace', ns, '--ignore-not-found', '-o', 'json']):
                    break
                time.sleep(1)
            else:
                raise ValueError('Namespace deletion pending')
        self.result['namespace_absent'] = True
        self.save(self.result)


SERVER = '''import http.server,os,threading
class H(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(os.environ['FDS_RESPONSE'].encode())
 def log_message(self,*args): pass
threading.Thread(target=http.server.HTTPServer(('0.0.0.0',8081),H).serve_forever,daemon=True).start()
http.server.HTTPServer(('0.0.0.0',8080),H).serve_forever()
'''
PROBE = '''import json,sys,socket,http.client
r=json.loads(sys.argv[1]);out={}
def http(ip,port,expected,source_port=0):
 c=http.client.HTTPConnection(ip,port,timeout=4,source_address=('',source_port))
 try:
  c.request('GET','/');v=c.getresponse();b=v.read(4096).decode();return {'passed':v.status==200 and b==expected,'status':v.status,'body':b}
 except Exception as e: return {'passed':False,'error':str(e)}
 finally: c.close()
out['direct']=http(r['peer'],8080,r['expected'],r['source_port'])
out['direct']['source_port']=r['source_port']
if r['service_http']: out['service']=http(r['service'],8080,r['expected'])
try:
 ips=sorted({x[4][0] for x in socket.getaddrinfo(r['dns'],8080,socket.AF_INET,socket.SOCK_STREAM)})
 out['dns']={'passed':ips==[r['service']],'addresses':ips}
except Exception as e: out['dns']={'passed':False,'error':str(e)}
print(json.dumps(out))
'''


def manifests(namespace):
    labels = {'fds-cni-trial': namespace}
    result = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace,
               'labels': dict(labels, **{'pod-security.kubernetes.io/enforce': 'restricted',
                                        'pod-security.kubernetes.io/enforce-version': 'v1.36'})}}]
    for name in ('a', 'b'):
        result.append({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name,
                       'namespace': namespace, 'labels': dict(labels, **{'trial-peer': name})},
                       'spec': {'nodeSelector': {'kubernetes.io/hostname': 'k8s-control01'},
                        'tolerations': [{'key': 'node-role.kubernetes.io/control-plane', 'operator': 'Exists', 'effect': 'NoSchedule'}],
                        'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
                        'automountServiceAccountToken': False, 'enableServiceLinks': False,
                        'restartPolicy': 'Never', 'activeDeadlineSeconds': 960, 'terminationGracePeriodSeconds': 5,
                        'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000,
                                            'seccompProfile': {'type': 'RuntimeDefault'}},
                        'containers': [{'name': 'probe', 'image': IMAGE, 'imagePullPolicy': 'IfNotPresent',
                          'command': ['python3', '-I', '-B', '-c', SERVER],
                          'env': [{'name': 'FDS_RESPONSE', 'value': namespace + '/' + name}],
                          'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                              'capabilities': {'drop': ['ALL']}},
                          'resources': {'requests': {'cpu': '20m', 'memory': '32Mi'},
                                        'limits': {'cpu': '100m', 'memory': '64Mi'}},
                          'readinessProbe': {'tcpSocket': {'port': 8080}, 'periodSeconds': 2}}]}})
    result.append({'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'peer-b',
                   'namespace': namespace, 'labels': labels}, 'spec': {'type': 'ClusterIP',
                   'selector': dict(labels, **{'trial-peer': 'b'}),
                   'ports': [{'port': 8080, 'targetPort': 8080, 'protocol': 'TCP'}]}})
    return result


def disarm(io):
    need(io.result.get('cleanup_complete') is True, 'Cannot disarm before cleanup confirmation')
    _, recovery = unit_names(io.job)
    units = json.loads(safe_read(io.job / 'UNITS.json'))
    for name, text in units.items():
        if (UNITS / name).exists():
            need(safe_read(UNITS / name).decode() == text, 'Recovery unit changed; do not remove')
        else:
            need(io.result.get('disarm_intent') is True, 'Recovery unit unexpectedly absent')
    io.result['disarm_intent'] = True
    io.save(io.result)
    if (UNITS / (recovery + '.timer')).exists():
        system(['disable', '--now', recovery + '.timer'])
    if (UNITS / (recovery + '.service')).exists():
        system(['disable', recovery + '.service'])
    for name in units:
        if (UNITS / name).exists():
            (UNITS / name).unlink()
    system(['daemon-reload'])
    io.result['recovery_disarmed'] = True
    io.save(io.result)


def cleanup(io):
    io.cleanup_mode = True
    io.result['phase'] = 'RECOVERING'
    io.save(io.result)
    if 'baseline' in io.result:
        io.core.Transaction(io, io.result).withdraw()
    io.cleanup_namespace()
    if 'baseline' in io.result:
        need(io.legacy_snapshot() == io.result['original_after'], 'Final original receipt state mismatch')
    if 'baseline' in io.result:
        io.management()
    probes = io.result.get('probes', {})
    values = [probes.get(p, {}).get(k, {}).get('passed')
              for p, k in (('a', 'direct'), ('b', 'direct'), ('a', 'service'), ('a', 'dns'), ('b', 'dns'))]
    io.result['positive_summary'] = {'passed': sum(v is True for v in values),
        'failed': sum(v is False for v in values), 'not_tested': sum(v is None for v in values), 'total': 5}
    io.result.update(cleanup_complete=True, phase='COMPLETE', ended_at=time.time(),
                     finished_within_window=not io.boot_changed and
                         time.time() <= io.request['budget']['wall_end'] and
                         time.clock_gettime(time.CLOCK_BOOTTIME) <= io.request['budget']['boot_start'] + 1200,
                     status='DIAGNOSTIC_ONLY_REVIEW_REQUIRED', runtime_negative='NOT_FULLY_TESTED')
    io.save(io.result)
    disarm(io)


def run_job(job, recovery=False):
    safe_dir(job)
    need(job.parent == ROOT and re.fullmatch(r'[0-9]{14}-[a-f0-9]{12}', job.name), 'Invalid job path')
    if recovery:
        atomic(job / 'STOP', b'recovery-only\n')
        work, _ = unit_names(job)
        # Stop and kill the entire cgroup, including a command interrupted after mutation.
        # An absent/finished unit needs no signal; unexpected query errors still surface.
        load = system(['show', work + '.service', '-p', 'LoadState', '--value'])
        if load != 'not-found':
            system(['stop', work + '.service'], timeout=12)
    with lock(job / 'transaction.lock'):
        io = Linux(job, recovery)
        if io.result.get('cleanup_complete'):
            if not io.result.get('recovery_disarmed'):
                disarm(io)
            return
        try:
            if not recovery:
                io.preflight()
                peers = io.create_workload()
                io.result['probes_before'] = io.probes(peers, bound=False, include_negative=False)
                io.save(io.result)
                io.core.Transaction(io, io.result).prepare(peers, job)
                # Retain generated native rules for independent path review.
                nft = json.loads(io.cmd(['nft', '-j', 'list', 'table', 'inet', 'firewalld']))
                save_json(job / 'ACTIVE_NFT.json', nft)
                io.result['active_nft_sha256'] = sha(safe_read(job / 'ACTIVE_NFT.json'))
                io.result['native_scope'] = io.core.validate_native_policy(nft, peers)
                io.result['probes'] = io.probes(peers)
                io.save(io.result)
        except Exception as error:
            io.result['diagnostic_error'] = io.helper.redact(str(error))
            io.save(io.result)
        finally:
            try:
                cleanup(io)
            except Exception as error:
                io.result.update(cleanup_complete=False, phase='RECOVERY_REQUIRED',
                                 cleanup_error=io.helper.redact(str(error)))
                try:
                    io.emergency_disable()
                except Exception as emergency:
                    io.result['emergency_disable_error'] = io.helper.redact(str(emergency))
                io.save(io.result)
                raise  # Timer remains armed; never resume diagnosis.


def main():
    if 'INSTALL_DATA' in globals():
        install(INSTALL_DATA)
        return
    need(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'k8s-control01', 'CP1 root required')
    need(len(sys.argv) in (3, 4) and sys.argv[1] in ('run', 'recover', 'status', 'poll', 'ack'), 'Invalid node invocation')
    job = Path(sys.argv[2])
    need(job.parent == ROOT and re.fullmatch(r'[0-9]{14}-[a-f0-9]{12}', job.name), 'Invalid job path')
    if sys.argv[1] == 'ack':
        need(len(sys.argv) == 4 and re.fullmatch(r'[a-f0-9]{32}', sys.argv[3]), 'Invalid acknowledgement')
        state = json.loads(safe_read(job / 'STATE.json'))
        need(state.get('phase') == 'WAIT_CONTROLLER_SSH' and state.get('management_nonce') == sys.argv[3],
             'Job not awaiting this acknowledgement')
        save_json(job / 'ACK.json', {'nonce': sys.argv[3], 'observed_at': time.time()}, exclusive=True)
        print(json.dumps({'management_acknowledged': True}))
    elif sys.argv[1] == 'poll':
        state = json.loads(safe_read(job / 'STATE.json'))
        print(json.dumps({k: state.get(k) for k in ('phase', 'management_nonce', 'cleanup_complete', 'status')}))
    elif sys.argv[1] == 'status':
        result = json.loads(safe_read(job / 'STATE.json'))
        files = {}
        for name in ('STATE.json', 'REQUEST.json', 'ARMED.json', 'ACK.json', 'COMMANDS.jsonl', 'ACTIVE_NFT.json',
                     'negative-a.pcap', 'negative-b.pcap'):
            path = job / name
            if path.exists():
                raw = safe_read(path)
                need(len(raw) <= 8_000_000, 'Evidence too large for transport; retrieve from protected job path')
                files[name] = {'sha256': sha(raw), 'base64': base64.b64encode(raw).decode()}
        print(json.dumps({'status': result.get('status', result['phase']), 'state': result, 'files': files}))
    else:
        run_job(job, sys.argv[1] == 'recover')


if __name__ == '__main__':
    main()
