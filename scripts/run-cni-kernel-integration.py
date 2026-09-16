#!/usr/bin/env python3
"""Real kernel/firewalld COMPONENT test; never a production qualification.

A private D-Bus and firewalld live in newly owned, unconnected network namespaces.
The unchanged production Transaction is exercised against that real daemon.
Default PLAN_ONLY. No import-time host IO except loading the adjacent source.
"""
import argparse
import copy
import ctypes
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import tarfile
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = Path('/var/lib/fds/cni-kernel-lab')
UNITS = Path('/etc/systemd/system')
SOURCES = ('run-cni-kernel-integration.py', 'cni_diag_core.py',
           'cp1-cni-diagnostic-node.py', 'node-firewall.py')
SCENARIOS = ('normal', 'kill', 'deadline', 'cleanup-retry', 'reboot')
PACKAGES = ('firewalld-2.4.3-4.el10.noarch', 'nftables-1.1.5-6.el10.x86_64',
            'NetworkManager-1.58.0-1.el10.x86_64', 'iptables-nft-1.8.11-16.el10.x86_64')
FLAGS = dict(stage='KERNEL_COMPONENT_PENDING', fixture='SYNTHETIC_NETNS',
             runtime_component_started=False, mutation_attempted=False,
             qualification_created=False, acceptance_criteria_credit=False,
             authorizes_cp1_acceptance=False, authorizes_cp2_change=False,
             production_node_recovery_tested=False, physical_paths_tested=False,
             actual_calico_pods_tested=False)
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'LANG': 'C',
       'SYSTEMD_PAGER': 'cat', 'PAGER': 'cat'}
LABELS = {'bus.conf': 'dbusd_etc_t', 'dbus': 'system_dbusd_var_run_t',
          'firewalld': 'firewalld_etc_rw_t', 'firewalld/zones': 'firewalld_etc_rw_t',
          'firewalld/firewalld.conf': 'firewalld_etc_rw_t',
          'firewalld/zones/public.xml': 'firewalld_etc_rw_t',
          'log': 'var_log_t', 'log/firewalld.log': 'firewalld_var_log_t',
          'run': 'firewalld_var_run_t'}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


core = load('kernel_lab_core', HERE / 'cni_diag_core.py')
node = load('kernel_lab_storage', HERE / 'cp1-cni-diagnostic-node.py')
need = core.need


def read_json(path):
    return json.loads(node.safe_read(path))


def hashes():
    return {n: hashlib.sha256((HERE / n).read_bytes()).hexdigest() for n in SOURCES}


def bundle_digest(source_hashes):
    return hashlib.sha256(json.dumps(source_hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_staged_sources(job, executing=False):
    need(job == job_path(job.name), 'Outside lab root')
    node.safe_dir(job)
    request = read_json(job / 'REQUEST.json')
    expected = request['observed']['sources_sha256']
    need(set(expected) == set(SOURCES), 'Staged source manifest differs')
    for name in SOURCES:
        need(hashlib.sha256(node.safe_read(job / name)).hexdigest() == expected[name], 'Staged source changed')
    if executing:
        need(HERE == job and hashes() == expected, 'Execute only the verified job staged source')
    return request


def manual_recover(job):
    """A mutable checkout locates/validates an old job, never recovers it itself."""
    verify_staged_sources(job)
    docs = read_json(job / 'UNITS.json')
    need(set(docs) == set(documents(job, 'normal')), 'Unexpected staged unit names')
    result = read_json(job / 'RESULT.json')
    allow_missing = bool(result.get('disarm_intent')) or result.get('mutation_attempted') is False
    for name, body in docs.items():
        path = UNITS / name
        need(not path.is_symlink(), 'Unit symlink is not owned: ' + name)
        if path.exists():
            need(node.safe_read(path).decode() == body, 'Unit ownership mismatch: ' + name)
        else:
            need(allow_missing, 'Missing owned unit: ' + name)
    # The old source revalidates its own generated unit bodies before stopping
    # any cgroup. Do not compare old unit bodies with this checkout's generator.
    os.execve('/usr/bin/python3', ['/usr/bin/python3', '-I', '-B',
              str(job / SOURCES[0]), '_recover', job.name], ENV)


def call(argv, timeout=15, env=None, accepted=(0,), output_limit=4_000_000):
    """Bounded output, process group termination, no shell or inherited bus address."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                             env=ENV if env is None else env, start_new_session=True)
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            raise ValueError('Command timeout; inspect durable resource journal')
        out.seek(0); err.seek(0)
        stdout, stderr = out.read(output_limit + 1), err.read(16001)
        need(len(stdout) <= output_limit and len(stderr) <= 16000, 'Command output truncated')
        result = dict(argv=argv, rc=p.returncode, stdout=stdout.decode(), stderr=stderr.decode())
        need(p.returncode in accepted, 'Command failed: ' + json.dumps(result))
        return result


def system(args, accepted=(0,)):
    return call(['/usr/bin/systemctl', *args], accepted=accepted)['stdout'].strip()


def job_path(value):
    need(re.fullmatch(r'[a-f0-9]{16}', value or ''), 'Invalid lab job ID')
    return ROOT / value


def ns_names(job):
    return {role: 'fdski-' + job.name + '-' + role for role in ('r', 'a', 'b', 'c')}


def unit_name(job, role):
    need(role in ('work', 'recover', 'bus', 'firewall', 'a', 'b', 'c'), 'Invalid unit role')
    return 'fds-ki-' + job.name + '-' + role


def private_env(job):
    return dict(ENV, DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(job / 'dbus/bus.sock'))


def file_context(path):
    need(not path.is_symlink(), 'SELinux path is a symlink: ' + str(path))
    return os.getxattr(path, 'security.selinux', follow_symlinks=False).rstrip(b'\0').decode('ascii')


class AccessDecision(ctypes.Structure):
    # libselinux's public struct av_decision, including domain permissive flags.
    _fields_ = [(name, ctypes.c_uint) for name in
                ('allowed', 'decided', 'auditallow', 'auditdeny', 'seqno', 'flags')]


class SELinuxPolicy:
    """Query the loaded policy; never load an allow module or change enforcing."""
    def __init__(self):
        self.lib = ctypes.CDLL('libselinux.so.1', use_errno=True)
        signatures = {
            'security_getenforce': ([], ctypes.c_int),
            'string_to_security_class': ([ctypes.c_char_p], ctypes.c_ushort),
            'string_to_av_perm': ([ctypes.c_ushort, ctypes.c_char_p], ctypes.c_uint),
            'security_compute_av_flags_raw': ([ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ushort,
                                              ctypes.c_uint, ctypes.POINTER(AccessDecision)], ctypes.c_int),
            'security_compute_create_raw': ([ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ushort,
                                            ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int),
            'freecon': ([ctypes.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, name); function.argtypes = args; function.restype = result
        need(self.lib.security_getenforce() == 1, 'SELinux must remain Enforcing')
        parent = Path('/proc/1/attr/current').read_text().strip().rstrip('\0')
        need(parent.split(':')[2] == 'init_t', 'Unexpected systemd SELinux domain')
        self.domains = {}
        for role, exe, domain in (('bus', '/usr/bin/dbus-daemon', 'system_dbusd_t'),
                                  ('firewall', '/usr/sbin/firewalld', 'firewalld_t')):
            context = ctypes.c_void_p()
            try:
                rc = self.lib.security_compute_create_raw(parent.encode(), file_context(Path(exe)).encode(),
                    self.lib.string_to_security_class(b'process'), ctypes.byref(context))
                need(rc == 0 and context.value, 'Cannot compute daemon SELinux transition: ' + role)
                value = ctypes.string_at(context).decode('ascii')
                need(value.split(':')[2] == domain, 'Unexpected daemon SELinux domain: ' + value)
                self.domains[role] = value
            finally:
                if context.value: self.lib.freecon(context)

    def access(self, role, context, kind, permissions):
        """Return named access-vector evidence without changing policy or objects."""
        need(self.lib.security_getenforce() == 1, 'SELinux enforcing changed')
        cls = self.lib.string_to_security_class(kind.encode())
        need(cls != 0, 'Unknown SELinux class: ' + kind)
        wanted = 0
        bits = {}
        for permission in permissions.split():
            bit = self.lib.string_to_av_perm(cls, permission.encode())
            need(bit != 0, 'Unknown SELinux permission: ' + permission)
            bits[permission] = bit
            wanted |= bit
        decision = AccessDecision()
        ctypes.set_errno(0)
        rc = self.lib.security_compute_av_flags_raw(self.domains[role].encode(), context.encode(),
                                                    cls, wanted, ctypes.byref(decision))
        error_number = ctypes.get_errno() if rc != 0 else 0
        # On API failure the vector is not evidence of which permission was denied.
        denied = [name for name, bit in bits.items() if not decision.allowed & bit] if rc == 0 else None
        return dict(role=role, source_context=self.domains[role], target_context=context,
                    object_class=kind, required_permissions=list(bits), denied_permissions=denied,
                    query_rc=rc, query_errno=error_number, requested_mask=wanted,
                    allowed_mask=decision.allowed if rc == 0 else None,
                    decision_flags=decision.flags if rc == 0 else None,
                    domain_permissive=bool(decision.flags & 1) if rc == 0 else None,
                    ok=rc == 0 and not denied and not decision.flags & 1)

    def require(self, role, context, kind, permissions):
        result = self.access(role, context, kind, permissions)
        need(result['ok'], 'Loaded SELinux policy does not allow enforced access: ' +
             json.dumps(result, sort_keys=True))
        return result

    def check(self):
        checks = (
            ('bus', 'dbusd_etc_t', 'file', 'open read getattr'),
            ('bus', 'system_dbusd_var_run_t', 'dir', 'search write add_name remove_name getattr'),
            ('bus', 'system_dbusd_var_run_t', 'sock_file', 'create write getattr setattr unlink'),
            ('firewall', 'system_dbusd_var_run_t', 'dir', 'search'),
            ('firewall', 'system_dbusd_var_run_t', 'sock_file', 'write'),
            ('firewall', 'firewalld_etc_rw_t', 'dir', 'search open read getattr write add_name remove_name create rmdir'),
            ('firewall', 'firewalld_etc_rw_t', 'file', 'open read getattr write create rename unlink setattr'),
            ('firewall', 'var_log_t', 'dir', 'search'),
            # firewalld 2.4.3 uses FileLog(..., "a"): O_APPEND, then fchmod.
            # The file already exists; arbitrary write/create are not required.
            ('firewall', 'firewalld_var_log_t', 'file', 'open append getattr setattr'),
            ('firewall', 'firewalld_var_run_t', 'dir', 'search open read getattr write add_name remove_name create rmdir'),
            ('firewall', 'firewalld_var_run_t', 'file', 'create open read write getattr setattr unlink'),
        )
        results = []
        def record(role, context, kind, permissions):
            try:
                results.append(self.access(role, context, kind, permissions))
            except (ValueError, OSError) as exc:
                results.append(dict(role=role, target_context=context, object_class=kind,
                                    required_permissions=permissions.split(),
                                    query_error=str(exc), ok=False))
        for role, label, kind, permissions in checks:
            record(role, 'system_u:object_r:' + label + ':s0', kind, permissions)
        # connectto targets the peer; ordinary socket IO targets our own socket.
        record('firewall', self.domains['bus'], 'unix_stream_socket', 'connectto')
        record('firewall', self.domains['firewall'], 'unix_stream_socket',
               'create connect read write getattr getopt setopt shutdown')
        for path in reversed([ROOT] + list(ROOT.parents)):
            if path.exists():
                node.safe_dir(path)
                for role in self.domains: record(role, file_context(path), 'dir', 'search')
        failures = [result for result in results if not result['ok']]
        report = dict(schema='fds.cni-selinux-access.v1', domains=self.domains,
                      checks=len(results), failed_checks=len(failures),
                      enforcing=self.lib.security_getenforce() == 1, access_decisions=results,
                      labels=LABELS, method='loaded_policy_access_vectors')
        if failures or not report['enforcing']:
            # inspect cannot produce a valid decision on failure, but all queried
            # vectors survive in the captured log for a single review round.
            print('SELINUX_POLICY_REPORT=' + json.dumps(report, sort_keys=True), flush=True)
            raise ValueError('Loaded SELinux policy preflight failed; see SELINUX_POLICY_REPORT')
        return report


def label_owned_path(job, relative):
    """Label only this source's allowlisted, newly-created job objects."""
    need(job == job_path(job.name) and relative in LABELS, 'Outside owned label scope')
    path = job / relative
    node.safe_dir(path.parent)
    st = path.lstat()
    need(st.st_uid == 0 and not st.st_mode & 0o022 and not stat.S_ISLNK(st.st_mode) and
         (stat.S_ISDIR(st.st_mode) or (stat.S_ISREG(st.st_mode) and st.st_nlink == 1)),
         'Unsafe label target: ' + str(path))
    context = 'system_u:object_r:' + LABELS[relative] + ':s0'
    os.setxattr(path, 'security.selinux', context.encode(), follow_symlinks=False)
    need(file_context(path) == context, 'SELinux label did not persist: ' + relative)
    return dict(path=relative, context=context, device=st.st_dev, inode=st.st_ino)


def prepare_daemon_files(job):
    policy = SELinuxPolicy(); observed = policy.check()
    # These paths belong to this new job. No reuse/recursive relabel of old jobs.
    for relative in ('dbus', 'firewalld', 'firewalld/zones', 'log', 'run'):
        (job / relative).mkdir(mode=0o700)
        label_owned_path(job, relative)
    config = job / 'firewalld'
    node.atomic(config / 'firewalld.conf', b'DefaultZone=public\nFirewallBackend=nftables\nFlushAllOnReload=yes\nCleanupOnExit=yes\n', exclusive=True)
    node.atomic(config / 'zones/public.xml', b'<zone><interface name="ens160"/><service name="ssh"/></zone>\n', exclusive=True)
    node.atomic(job / 'log/firewalld.log', b'', exclusive=True)
    bus = '<busconfig><type>system</type><listen>' + private_env(job)['DBUS_SYSTEM_BUS_ADDRESS'] + '</listen><auth>EXTERNAL</auth><policy context="default"><deny user="*"/></policy><policy user="root"><allow user="root"/><allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>\n'
    node.atomic(job / 'bus.conf', bus.encode(), exclusive=True)
    labels = [label_owned_path(job, relative) for relative in LABELS]
    # Verify traversal after creation, including the job's actual inherited label.
    for role in policy.domains: policy.require(role, file_context(job), 'dir', 'search')
    node.save_json(job / 'SELINUX_LAYOUT.json', dict(policy=observed, objects=labels), exclusive=True)


def service_state(job, role):
    raw = system(['show', unit_name(job, role) + '.service', '-p', 'ActiveState', '-p', 'SubState',
                  '-p', 'MainPID', '-p', 'ExecMainStatus'])
    state = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    need(state.get('ActiveState') not in ('failed', 'inactive', 'deactivating') and
         state.get('ActiveState') in ('active', 'activating'),
         'Private ' + role + ' stopped during initialization: ' + json.dumps(state, sort_keys=True))
    return state


def private_socket(job):
    path = job / 'dbus/bus.sock'
    node.safe_dir(path.parent)
    st = path.lstat()
    need(stat.S_ISSOCK(st.st_mode) and st.st_uid == 0 and
         file_context(path).split(':')[2] == 'system_dbusd_var_run_t', 'Unexpected private bus socket')
    return path


def wait_for_bus(lab):
    deadline = time.monotonic() + 15
    last_error = 'socket not created'
    while True:
        state = service_state(lab.job, 'bus')
        try:
            private_socket(lab.job)
        except FileNotFoundError:
            pass
        else:
            try:
                answer = lab.cmd(['/usr/bin/busctl', '--address=' + private_env(lab.job)['DBUS_SYSTEM_BUS_ADDRESS'],
                    '--no-pager', '--timeout=2', 'call', 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                    'org.freedesktop.DBus', 'GetConnectionUnixProcessID', 's', 'org.freedesktop.DBus'])
            except ValueError as error:
                last_error = str(error)
            else:
                need(re.fullmatch(r'u [1-9][0-9]*', answer) and answer.split()[1] == state.get('MainPID'),
                     'Private bus PID does not match owned service')
                need(service_state(lab.job, 'bus').get('MainPID') == state['MainPID'], 'Bus process changed')
                return
        lab.guard()
        need(time.monotonic() < deadline, 'Private bus readiness timeout: ' + last_error)
        time.sleep(0.1)


def verify_runtime_view(job):
    state = service_state(job, 'firewall')
    pid = state.get('MainPID', '')
    need(re.fullmatch(r'[1-9][0-9]*', pid), 'Private firewall has no main process')
    process = Path('/proc') / pid
    source = (job / 'run').stat()
    view = (process / 'root/run/firewalld').stat()
    host = Path('/run/firewalld').stat()
    identity = lambda item: [item.st_dev, item.st_ino]
    need(identity(view) == identity(source) and identity(view) != identity(host), 'Private runtime mount differs')
    need((process / 'ns/mnt').stat().st_ino != Path('/proc/1/ns/mnt').stat().st_ino, 'Host mount namespace forbidden')
    need((process / 'ns/net').stat().st_ino == (Path('/run/netns') / ns_names(job)['r']).stat().st_ino,
         'Private firewall network namespace differs')
    need(file_context(process / 'root/run/firewalld').split(':')[2] == 'firewalld_var_run_t', 'Runtime label differs')
    need(service_state(job, 'firewall').get('MainPID') == pid, 'Firewall process changed during mount check')
    node.save_json(job / 'RUNTIME_VIEW.json', dict(private_runtime=identity(view), host_runtime=identity(host),
                   main_pid=int(pid), private_mount_verified=True, private_network_verified=True))


def start_private_daemons(lab):
    system(['start', unit_name(lab.job, 'bus') + '.service'])
    wait_for_bus(lab)
    system(['start', unit_name(lab.job, 'firewall') + '.service'])
    deadline = time.monotonic() + 30
    while True:
        service_state(lab.job, 'bus')
        service_state(lab.job, 'firewall')
        try:
            need(lab.command(['--state']) == 'running', 'Private firewalld not running')
        except ValueError:
            lab.guard()
            if time.monotonic() >= deadline: raise
            time.sleep(0.2)
        else:
            verify_runtime_view(lab.job)
            return


def daemon_journal(job):
    """Bounded, job/time-scoped read. Collection failure never replaces test failure."""
    state = dict(status='UNAVAILABLE', rc=None)
    try:
        request = read_json(job / 'REQUEST.json')
        start_time = float(request['started_at'])
        end_time = time.time()
        need(0 < start_time <= end_time, 'Invalid journal time window')
        fmt = lambda value: dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        argv = ['/usr/bin/journalctl', '--no-pager', '--output=short-iso-precise', '--lines=2000',
                '--since', fmt(start_time), '--until', fmt(end_time + 1)]
        for role in ('bus', 'firewall', 'work', 'recover'):
            argv += ['--unit', unit_name(job, role) + '.service']
        output = call(argv, timeout=5, accepted=(0, 1), output_limit=2_000_000)
        state.update(status='COLLECTED' if output['rc'] == 0 else 'UNAVAILABLE', rc=output['rc'],
                     started_at=start_time, until=end_time, line_limit=2000, stderr=output['stderr'])
        return output['stdout'].encode(), state
    except Exception as error:
        state['error'] = str(error)
        return b'', state


def capture_initialization_failure(job):
    # Keep the first daemon failure before stopping its cgroup, even if collect is later.
    try:
        raw, state = daemon_journal(job)
        node.atomic(job / 'INITIALIZATION_JOURNAL.log', raw, exclusive=True)
        node.save_json(job / 'INITIALIZATION_JOURNAL_STATE.json', state, exclusive=True)
    except Exception:
        pass  # The original exception and cleanup path must still be preserved.


def redact_export(evidence):
    """Sanitize copies only. Durable job/decision files retain their original bytes."""
    uuid = re.compile(rb'(?i)(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])')
    ledger = {}
    for path in sorted(evidence.iterdir()):
        raw = node.safe_read(path)
        matches = list(uuid.finditer(raw))
        if matches:
            need(path.suffix in ('.json', '.jsonl', '.log', '.trace'), 'UUID in binary evidence needs manual export review')
            exported = uuid.sub(lambda m: ('[UUID_SHA256:' + hashlib.sha256(m[0].lower()).hexdigest() + ']').encode(), raw)
            node.atomic(path, exported)
        else:
            exported = raw
        ledger[path.name] = dict(source_bytes_sha256=hashlib.sha256(raw).hexdigest(),
                                export_bytes_sha256=hashlib.sha256(exported).hexdigest(), redacted_uuid_count=len(matches))
    node.save_json(evidence / 'EXPORT_REDACTION.json', dict(schema='fds.cni-export-redaction.v1',
                   strategy='uuid-shaped text replaced with lowercase-value SHA256; binary match blocks export',
                   original_job_preserved=True, exported_decision_is_not_execution_input=True, files=ledger), exclusive=True)


def fingerprint_host():
    # Network/daemon access here is read-only, with a fresh environment.
    result = {'nm_files': node.Linux.configuration(None)}
    for key, args in {
        'default': ['--get-default-zone'], 'zones': ['--list-all-zones'],
        'policies': ['--list-all-policies'],
        'permanent_zones': ['--permanent', '--list-all-zones'],
        'permanent_policies': ['--permanent', '--list-all-policies'],
        'direct': ['--direct', '--get-all-rules'],
        'permanent_direct': ['--permanent', '--direct', '--get-all-rules'],
    }.items():
        result[key] = call(['/usr/bin/firewall-cmd', *args])['stdout']
    return result


def prerequisites():
    need(os.geteuid() == 0, 'Root required')
    need(Path('/proc/1/comm').read_text().strip() == 'systemd', 'PID1 must be systemd')
    status = Path('/proc/self/status').read_text()
    cap = int(re.search(r'^CapEff:\s*([0-9a-f]+)$', status, re.M)[1], 16)
    need(all(cap & (1 << b) for b in (12, 13, 21)), 'NET_ADMIN/NET_RAW/SYS_ADMIN required')
    need(call(['/usr/bin/systemd-detect-virt', '--vm'])['stdout'].strip() == 'vmware', 'VMware lab required')
    need(call(['/usr/sbin/getenforce'])['stdout'].strip() == 'Enforcing', 'SELinux must remain Enforcing')
    versions = call(['/usr/bin/rpm', '-q', 'firewalld', 'nftables', 'NetworkManager', 'iptables-nft'])['stdout'].splitlines()
    need(versions == list(PACKAGES), 'Observed reference package set differs')
    for path in ('/usr/bin/dbus-daemon', '/usr/sbin/firewalld', '/usr/bin/firewall-cmd',
                 '/usr/sbin/ip', '/usr/sbin/nft', '/usr/sbin/conntrack', '/usr/sbin/tcpdump',
                 '/usr/bin/unshare', '/usr/bin/mount', '/usr/bin/busctl', '/usr/bin/journalctl'):
        need(Path(path).is_file() and os.access(path, os.X_OK), 'Missing prerequisite ' + path)
    help_text = call(['/usr/sbin/firewalld', '--help'])['stdout']
    need(all(option in help_text for option in ('--system-config', '--default-config', '--nopid', '--nofork', '--log-file')),
         'Private firewalld instance options unavailable')
    return dict(packages=versions, product_uuid=Path('/sys/class/dmi/id/product_uuid').read_text().strip(),
                machine_id_sha256=hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest(),
                sources_sha256=hashes(), checked_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                selinux=SELinuxPolicy().check(),
                policy_packages=call(['/usr/bin/rpm', '-q', 'selinux-policy', 'selinux-policy-targeted',
                                     'libselinux', 'systemd', 'dbus-daemon'])['stdout'].splitlines())


def authorize(decision, observed):
    need(decision.get('schema') == 'fds.cni-kernel-lab-decision.v2', 'Lab decision v2 required; refresh inspect/decision')
    need(decision.get('lab_vm') == 'cp1-cni-lab' and decision.get('lan_segment') == 'FDS-CNI-LAB', 'Wrong lab identity')
    for field in ('product_uuid', 'machine_id_sha256', 'sources_sha256'):
        need(decision.get(field) == observed[field], 'Decision binding differs: ' + field)
    need(decision.get('execution_approved') is True, 'Lab execution decision required')
    need(all(isinstance(decision.get(k), str) and decision[k].strip()
             for k in ('snapshot_reference', 'operator')), 'Snapshot/operator reference required')
    need(decision.get('reviewed_bundle_sha256') == bundle_digest(observed['sources_sha256']),
         'Reviewed bundle digest differs or missing')
    need(re.fullmatch(r'https://github\.com/321Team/infra-kubernetes/pull/11#(?:pullrequestreview|issuecomment)-[0-9]+',
                      decision.get('review_url', '')), 'Source review URL required')
    need(decision.get('scope') == 'KERNEL_COMPONENT_ONLY', 'Full production qualification is not supported')


def documents(job, scenario):
    program = str(job / SOURCES[0])
    prefix = '[Unit]\nDescription=FDS private kernel component test\n[Service]\nUMask=0077\nKillMode=control-group\nTimeoutStopSec=5\n'
    unit = {}
    unit[unit_name(job, 'work') + '.service'] = (prefix + 'Type=oneshot\nTimeoutStartSec=1020\n'
        'ExecStart=/usr/bin/python3 -I -B ' + program + ' _work ' + job.name + '\n')
    unit[unit_name(job, 'recover') + '.service'] = (prefix + 'Type=oneshot\nTimeoutStartSec=150\n'
        'ExecStart=/usr/bin/python3 -I -B ' + program + ' _recover ' + job.name + '\n[Install]\nWantedBy=multi-user.target\n')
    # Accelerated cases test this wrapper's recovery, not production 20-minute behavior.
    delay = 90 if scenario in ('kill', 'deadline', 'cleanup-retry') else 1020
    unit[unit_name(job, 'recover') + '.timer'] = ('[Unit]\nDescription=FDS component deadline/retry\n[Timer]\n'
        'OnActiveSec=' + str(delay) + 's\nOnUnitInactiveSec=30s\nAccuracySec=1s\nUnit=' + unit_name(job, 'recover') + '.service\n[Install]\nWantedBy=timers.target\n')
    unit[unit_name(job, 'bus') + '.service'] = (prefix + 'Type=exec\nRuntimeMaxSec=1200\n'
        'ProtectSystem=strict\nReadWritePaths=' + str(job / 'dbus') + '\n'
        'ExecStart=/usr/bin/dbus-daemon --nofork --nosyslog --config-file=' + str(job / 'bus.conf') + '\n')
    # A private bus prevents connection to the host firewalld/NM. Read-only /etc
    # also prevents the daemon from updating real ifcfg or NetworkManager files.
    unit[unit_name(job, 'firewall') + '.service'] = (prefix + 'Type=exec\nRuntimeMaxSec=1200\n'
        'ProtectSystem=strict\nReadWritePaths=' + str(job / 'firewalld') + ' ' + str(job / 'log/firewalld.log') + '\n'
        'BindPaths=' + str(job / 'run') + ':/run/firewalld:norbind\n'
        'Environment=DBUS_SYSTEM_BUS_ADDRESS=' + private_env(job)['DBUS_SYSTEM_BUS_ADDRESS'] + '\n'
        'NetworkNamespacePath=/run/netns/' + ns_names(job)['r'] + '\n'
        'ExecStart=/usr/sbin/firewalld --nofork --nopid --system-config=' + str(job / 'firewalld') +
        ' --default-config=/usr/lib/firewalld --log-file=' + str(job / 'log/firewalld.log') + '\n')
    for role in ('a', 'b', 'c'):
        unit[unit_name(job, role) + '.service'] = (prefix + 'Type=exec\nRuntimeMaxSec=1200\n'
            'NetworkNamespacePath=/run/netns/' + ns_names(job)[role] + '\n'
            'ExecStart=/usr/bin/python3 -I -B ' + program + ' _server ' + job.name + ' ' + role + '\n')
    return unit


def validate_units(job, allow_missing=False):
    docs = read_json(job / 'UNITS.json')
    need(docs == documents(job, read_json(job / 'REQUEST.json')['scenario']), 'Unit manifest differs from source')
    for name, text in docs.items():
        path = UNITS / name
        need(not path.is_symlink(), 'Unit symlink is not owned: ' + name)
        if not path.exists():
            need(allow_missing, 'Missing owned unit: ' + name)
        else:
            need(node.safe_read(path).decode() == text, 'Unit ownership mismatch: ' + name)
    return docs


def recovery_unit_check(job, result):
    # An interrupted installation may fail and retry before any namespace command.
    # Recovery phase changes must not make its partially installed units unrecoverable.
    # After the first mutation, missing units are accepted only during owned disarm.
    return validate_units(job, allow_missing=bool(result.get('disarm_intent')) or
                          result.get('mutation_attempted') is False)


def start(decision_path, scenario):
    observed = prerequisites()
    decision = json.loads(decision_path.read_text())
    authorize(decision, observed)
    node.safe_dir(ROOT, create=True)
    with node.lock(ROOT / 'launch.lock', nonblock=True):
        active = ROOT / 'ACTIVE.json'
        if active.exists():
            previous = job_path(read_json(active)['job_id'])
            need(read_json(previous / 'RESULT.json').get('recovery_disarmed') is True, 'Prior component job needs recovery')
        job = job_path(secrets.token_hex(8)); job.mkdir(mode=0o700)
        for name in SOURCES:
            raw = (HERE / name).read_bytes()
            need(hashlib.sha256(raw).hexdigest() == observed['sources_sha256'][name], 'Source changed during staging')
            node.atomic(job / name, raw, exclusive=True)
        request = dict(observed=observed, decision=decision, scenario=scenario, boot_id=node.boot_id(),
                       started_at=time.time(), deadline=time.time() + 1200,
                       boot_start=time.clock_gettime(time.CLOCK_BOOTTIME))
        node.save_json(job / 'REQUEST.json', request, exclusive=True)
        node.save_json(job / 'RESULT.json', dict(FLAGS, phase='STAGED', job_id=job.name,
                       cleanup_complete=False, recovery_disarmed=False, component_flows={}, recovery_attempts=0), exclusive=True)
        node.save_json(job / 'RESOURCES.json', dict(namespaces={}, pending_namespace=None), exclusive=True)
        docs = documents(job, scenario); node.save_json(job / 'UNITS.json', docs, exclusive=True)
        node.save_json(active, {'job_id': job.name})
        print('JOB_ID=' + job.name, flush=True)
        for name, value in docs.items():
            need(system(['show', name, '-p', 'LoadState', '--value']) == 'not-found', 'Existing unit collision')
            node.atomic(UNITS / name, value.encode(), exclusive=True)
        system(['daemon-reload'])
        recovery = unit_name(job, 'recover')
        system(['enable', recovery + '.service'])
        system(['enable', '--now', recovery + '.timer'])
        need(system(['is-active', recovery + '.timer']) == 'active', 'Timer not armed')
        need(system(['is-enabled', recovery + '.service']) == 'enabled', 'Boot recovery not armed')
        next_time = system(['show', recovery + '.timer', '-p', 'NextElapseUSecMonotonic', '--value'])
        need(next_time not in ('', '0'), 'Timer has no scheduled deadline')
        node.save_json(job / 'ARMED.json', dict(next_elapse=next_time, armed_at=time.time()))
        system(['start', '--no-block', unit_name(job, 'work') + '.service'])
    print('STATUS=STARTED_KERNEL_COMPONENT_ONLY')


class Lab:
    def __init__(self, job, recovery=False):
        self.job = job
        self.request = verify_staged_sources(job, executing=True)
        self.recovery = recovery
        self.end = time.monotonic() + 110 if recovery else None
        self.result = read_json(job / 'RESULT.json')
        self.ns = ns_names(job)
        self.j = read_json(job / 'FW_JOURNAL.json') if (job / 'FW_JOURNAL.json').exists() else {}
        self.boot_changed = self.request['boot_id'] != node.boot_id()

    def guard(self, cleanup=False):
        if self.recovery:
            need(time.monotonic() < self.end - 1, 'Recovery attempt expired')
            return self.end - time.monotonic()
        need(not self.boot_changed, 'Boot changed: recovery only')
        reserve = 0 if cleanup else 180
        remain = min(self.request['deadline'] - time.time(), self.request['boot_start'] + 1200 -
                     time.clock_gettime(time.CLOCK_BOOTTIME)) - reserve
        need(remain > 1, 'Component window exhausted')
        need(cleanup or not (self.job / 'STOP').exists(), 'Recovery requested')
        return remain

    def cmd(self, argv, env=None, cleanup=False, accepted=(0,)):
        remaining = self.guard(cleanup)
        start_time = time.time()
        try:
            result = call(argv, timeout=min(12, remaining - 0.5),
                          env=env, accepted=accepted)
            return result['stdout'].strip()
        finally:
            record = dict(started_at=start_time, ended_at=time.time(), argv=argv,
                          result=locals().get('result'), bus='private' if env else 'none')
            fd = os.open(self.job / 'COMMANDS.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as stream:
                stream.write(json.dumps(record) + '\n'); stream.flush(); os.fsync(stream.fileno())

    def net(self, role, argv, **kwargs):
        need(role in self.ns, 'Unknown namespace role')
        self.check_namespace(role)
        return self.cmd(['/usr/sbin/ip', 'netns', 'exec', self.ns[role], *argv], **kwargs)

    def check_namespace(self, role):
        saved = read_json(self.job / 'RESOURCES.json')['namespaces'].get(role)
        path = Path('/run/netns') / self.ns[role]
        need(saved and path.exists() and [path.stat().st_dev, path.stat().st_ino] == saved,
             'Network namespace ownership mismatch: ' + role)
        need(path.stat().st_ino != Path('/proc/self/ns/net').stat().st_ino, 'Host namespace forbidden')

    def create_namespace(self, role):
        ledger = read_json(self.job / 'RESOURCES.json')
        path = Path('/run/netns') / self.ns[role]
        need(not path.exists() and role not in ledger['namespaces'], 'Namespace collision')
        ledger['pending_namespace'] = role; node.save_json(self.job / 'RESOURCES.json', ledger)
        self.result['mutation_attempted'] = True; node.save_json(self.job / 'RESULT.json', self.result)
        self.cmd(['/usr/sbin/ip', 'netns', 'add', self.ns[role]])
        ledger['namespaces'][role] = [path.stat().st_dev, path.stat().st_ino]
        ledger['pending_namespace'] = None; node.save_json(self.job / 'RESOURCES.json', ledger)
        self.result.update(stage='RUNTIME_KERNEL_COMPONENT', runtime_component_started=True)
        node.save_json(self.job / 'RESULT.json', self.result)
        self.net(role, ['/usr/sbin/ip', 'link', 'set', 'lo', 'up'])

    def command(self, args, cleanup=False):
        # firewall-cmd can edit matching ifcfg files. A PRIVATE mount namespace
        # hides the host directory even though the target D-Bus is already private.
        return self.net('r', ['/usr/bin/unshare', '--mount', '--propagation', 'private',
            '/usr/bin/python3', '-I', '-B', str(self.job / SOURCES[0]), '_firewall', self.job.name, *args],
            env=private_env(self.job), cleanup=cleanup)

    def snapshot(self):
        snap = {'default': self.command(['--get-default-zone'])}
        for plane in ('runtime', 'permanent'):
            prefix = ['--permanent'] if plane == 'permanent' else []
            snap[plane] = dict(zones=core.parse_objects(self.command(prefix + ['--list-all-zones'])),
                               policies=core.parse_objects(self.command(prefix + ['--list-all-policies'])),
                               direct=self.command(prefix + ['--direct', '--get-all-rules']))
        return snap

    def save(self, journal):
        self.j = journal
        node.save_json(self.job / 'FW_JOURNAL.json', journal)

    def management(self):
        # A synthetic management binding is checked; no production SSH/API proof.
        state = self.snapshot()
        need(state['runtime']['zones']['public']['interfaces'] == 'ens160', 'Synthetic binding differs')
        need(set(core.six_rules()) <= set(state['runtime']['zones']['public']['rich rules']), 'Fixture six not restored')

    def verify_peers(self, peers, bound):
        snap = self.snapshot()
        links = json.loads(self.net('r', ['/usr/sbin/ip', '-j', '-d', 'link', 'show']))
        for peer in peers:
            link = next((x for x in links if x['ifname'] == peer['interface']), None)
            need(link and link['ifindex'] == peer['host_ifindex'] and link.get('linkinfo', {}).get('info_kind') == 'veth', 'Fixture veth changed')
            for plane in ('runtime', 'permanent'):
                owners = [n for n, x in snap[plane]['zones'].items() if peer['interface'] in x['interfaces'].split()]
                need(owners == ([core.ZONE] if bound and plane == 'runtime' else []), 'Fixture binding differs')

    def verify_cleanup_interface(self, interface, peers):
        peer = next((x for x in peers if x['interface'] == interface), None)
        links = json.loads(self.net('r', ['/usr/sbin/ip', '-j', '-d', 'link', 'show'], cleanup=True))
        link = next((x for x in links if x['ifname'] == interface), None)
        need(peer and link and link['ifindex'] == peer['host_ifindex'] and link.get('linkinfo', {}).get('info_kind') == 'veth', 'Do not unbind reused fixture interface')

    def build(self):
        validate_units(self.job)
        need((self.job / 'ARMED.json').exists(), 'Recovery not armed')
        need(system(['is-active', unit_name(self.job, 'recover') + '.timer']) == 'active', 'Timer inactive')
        node.save_json(self.job / 'HOST_BEFORE.json', fingerprint_host())
        prepare_daemon_files(self.job)
        for role in self.ns:
            self.create_namespace(role)
        self.net('r', ['/usr/sbin/ip', 'link', 'add', 'ens160', 'type', 'dummy'])
        self.net('r', ['/usr/sbin/ip', 'link', 'set', 'ens160', 'up'])
        self.net('r', ['/usr/bin/python3', '-I', '-c', 'from pathlib import Path; Path("/proc/sys/net/ipv4/ip_forward").write_text("1\\n")'])
        peers = []
        for i, role in enumerate(('a', 'b', 'c')):
            iface, address, gateway = 'caliki' + role, '10.244.246.' + str(80+i), '169.254.31.' + str(1+4*i)
            # Both ends are born in owned namespaces. No host link is moved.
            self.net('r', ['/usr/sbin/ip', 'link', 'add', iface, 'type', 'veth', 'peer', 'name', 'eth0', 'netns', self.ns[role]])
            for args in (['addr', 'add', gateway + '/30', 'dev', iface], ['link', 'set', iface, 'up'],
                         ['route', 'add', address + '/32', 'dev', iface]):
                self.net('r', ['/usr/sbin/ip', *args])
            for args in (['addr', 'add', address + '/32', 'dev', 'eth0'], ['link', 'set', 'eth0', 'up'],
                         ['route', 'add', gateway + '/32', 'dev', 'eth0'], ['route', 'add', 'default', 'via', gateway, 'dev', 'eth0']):
                self.net(role, ['/usr/sbin/ip', *args])
            link = json.loads(self.net('r', ['/usr/sbin/ip', '-j', '-d', 'link', 'show', iface]))[0]
            peers.append(dict(name=role, ip=address, interface=iface, host_ifindex=link['ifindex']))
        node.save_json(self.job / 'FIXTURE.json', dict(scope='SYNTHETIC_NETNS_ONLY', peers=peers, production_receipt_used=False))
        (self.job / 'empty-ifcfg').mkdir(mode=0o700)
        start_private_daemons(self)
        for role in ('a', 'b', 'c'):
            system(['start', unit_name(self.job, role) + '.service'])
        for rule in core.six_rules():
            self.command(['--zone=public', '--add-rich-rule=' + rule])
        baseline = self.snapshot()
        self.save(dict(baseline=baseline, expected=copy.deepcopy(baseline), peers=peers[:2]))
        core.validate_reload_baseline(baseline)
        for name, xml in core.xml_documents(self.job.name, peers[:2]).items():
            node.atomic(self.job / name, xml.encode(), exclusive=True)
        return peers

    def flow(self, source, destination, port):
        label = source['name'] + '-' + destination['name'] + '-' + str(port)
        source_port = 40000 + secrets.randbelow(20000)
        query = ['/usr/sbin/conntrack', '-L', '-f', 'ipv4', '-p', 'tcp', '--orig-src', source['ip'],
                 '--orig-dst', destination['ip'], '--sport', str(source_port), '--dport', str(port)]
        need(not self.net('r', query), 'Selected tuple already in conntrack')
        listener = self.net(destination['name'], ['/usr/bin/python3', '-I', '-B', str(self.job / SOURCES[0]),
                   '_probe', self.job.name, destination['name'], '127.0.0.1', str(port), '0'])
        need(json.loads(listener).get('tcp_connected') is True and json.loads(listener).get('status') == 200 and
             json.loads(listener).get('body') == self.job.name + '/' + destination['name'], 'Destination listener not ready')
        self.net('r', ['/usr/sbin/nft', 'add', 'table', 'inet', 'fds_ki_trace'])
        self.net('r', ['/usr/sbin/nft', 'add', 'chain', 'inet', 'fds_ki_trace', 'pre',
                      '{ type filter hook prerouting priority -301; policy accept; }'])
        self.net('r', ['/usr/sbin/nft', 'add', 'rule', 'inet', 'fds_ki_trace', 'pre',
                      'iifname', source['interface'], 'ip', 'saddr', source['ip'], 'ip', 'daddr', destination['ip'],
                      'tcp', 'sport', str(source_port), 'tcp', 'dport', str(port), 'meta', 'nftrace', 'set', '1'])
        capture, trace = self.job / (label + '.pcap'), self.job / (label + '.trace')
        processes = []
        with open(trace, 'xb') as trace_out, tempfile.TemporaryFile() as cap_err, tempfile.TemporaryFile() as trace_err:
            try:
                processes.append(subprocess.Popen(['/usr/sbin/ip', 'netns', 'exec', self.ns['r'], '/usr/sbin/nft', 'monitor', 'trace'],
                    stdout=trace_out, stderr=trace_err, env=ENV, start_new_session=True))
                expression = 'src host %s and dst host %s and tcp src port %s and tcp dst port %s' % (source['ip'], destination['ip'], source_port, port)
                processes.append(subprocess.Popen(['/usr/sbin/ip', 'netns', 'exec', self.ns['r'], '/usr/sbin/tcpdump',
                    '-Z', 'root', '-nn', '-i', source['interface'], '-U', '-s', '128', '-w', str(capture), expression],
                    stdout=subprocess.DEVNULL, stderr=cap_err, env=ENV, start_new_session=True))
                ready = time.monotonic() + 3
                while True:
                    need(all(p.poll() is None for p in processes), 'Packet observation process exited')
                    cap_err.seek(0)
                    if b'listening on' in cap_err.read(8000):
                        break
                    need(time.monotonic() < ready, 'Capture readiness timeout'); time.sleep(0.05)
                observed = json.loads(self.net(source['name'], ['/usr/bin/python3', '-I', '-B', str(self.job / SOURCES[0]),
                                      '_probe', self.job.name, source['name'], destination['ip'], str(port), str(source_port)]))
                time.sleep(0.2)
            finally:
                for p in processes:
                    if p.poll() is None:
                        os.killpg(p.pid, signal.SIGINT)
                        try: p.wait(timeout=2)
                        except subprocess.TimeoutExpired: os.killpg(p.pid, signal.SIGKILL); p.wait()
            trace_out.flush(); os.fsync(trace_out.fileno())
        self.net('r', ['/usr/sbin/nft', 'delete', 'table', 'inet', 'fds_ki_trace'])
        arrived = self.cmd(['/usr/sbin/tcpdump', '-nn', '-r', str(capture), '-c', '1', expression])
        trace_raw = node.safe_read(trace)
        observed.update(scope='KERNEL_COMPONENT_ONLY', source_ip=source['ip'], destination_ip=destination['ip'],
                        source_port=source_port, destination_port=port, fresh_direct_flow=True,
                        listener_confirmed=True, ingress_arrival_observed=bool(arrived),
                        expected_body=self.job.name + '/' + destination['name'],
                        expected_connected=port == 8080 and source['name'] != 'c' and destination['name'] != 'c',
                        packet_capture_sha256=hashlib.sha256(node.safe_read(capture)).hexdigest(),
                        nft_trace_sha256=hashlib.sha256(trace_raw).hexdigest(), nft_trace_nonempty=bool(trace_raw),
                        component_attribution='REQUIRES_TRACE_REVIEW')
        errors = []
        if observed.get('tcp_connected') is not observed['expected_connected']:
            errors.append('TCP_CONNECT_EXPECTATION_MISMATCH')
        if observed['expected_connected'] and (observed.get('status') != 200 or
                                              observed.get('body') != observed['expected_body']):
            errors.append('HTTP_RESPONSE_EXPECTATION_MISMATCH')
        if not arrived or not trace_raw:
            errors.append('PACKET_TRACE_EVIDENCE_MISSING')
        observed.update(expectation_errors=errors, expected_result_matches=not errors,
                        verdict='COMPONENT_FAILED_REVIEW_REQUIRED' if errors else 'OBSERVED_REVIEW_REQUIRED')
        self.result['component_flows'][label] = observed; node.save_json(self.job / 'RESULT.json', self.result)
        need(not errors, 'Component flow failed: ' + label + ': ' + ', '.join(errors))


def cleanup(lab):
    """Recover core transaction when available, then remove only owned fixture resources.

    Namespace teardown is the independent containment fallback. Failure to restore
    the private firewall is retained as a failed component test, never hidden.
    """
    docs = recovery_unit_check(lab.job, lab.result)
    if lab.result.get('cleanup_complete'):
        disarm(lab, docs)
        return
    ledger = read_json(lab.job / 'RESOURCES.json')
    if lab.j.get('baseline') and not lab.boot_changed:
        try:
            core.Transaction(lab, lab.j).withdraw()
            lab.result['shared_transaction_restored'] = True
        except Exception as error:
            lab.result['shared_transaction_restored'] = False
            lab.result['shared_transaction_cleanup_error'] = str(error)
    # Stop service cgroups before deleting namespace mounts. Never signal arbitrary PIDs.
    for role in ('a', 'b', 'c', 'firewall', 'bus'):
        name = unit_name(lab.job, role) + '.service'
        if system(['show', name, '-p', 'LoadState', '--value']) != 'not-found':
            system(['stop', name])
    pending = ledger.get('pending_namespace')
    need(not pending or not (Path('/run/netns') / lab.ns[pending]).exists(),
         'Unknown namespace create outcome: console ownership review required')
    for role in reversed(list(lab.ns)):
        path = Path('/run/netns') / lab.ns[role]
        if path.exists():
            lab.check_namespace(role)
            need(not lab.cmd(['/usr/sbin/ip', 'netns', 'pids', lab.ns[role]], cleanup=True), 'Foreign process retains namespace')
            lab.cmd(['/usr/sbin/ip', 'netns', 'delete', lab.ns[role]], cleanup=True)
        need(not path.exists(), 'Namespace still present')
    lab.result['fixture_namespaces_absent'] = True
    if (lab.job / 'HOST_BEFORE.json').exists():
        after = fingerprint_host(); node.save_json(lab.job / 'HOST_AFTER.json', after)
        lab.result['host_configuration_unchanged'] = after == read_json(lab.job / 'HOST_BEFORE.json')
    failed = bool(lab.result.get('component_error')) or lab.result.get('shared_transaction_restored') is False or lab.result.get('host_configuration_unchanged') is False
    lab.result.update(cleanup_complete=True, phase='COMPONENT_COMPLETE', boot_changed=lab.boot_changed,
                      status='COMPONENT_FAILED_REVIEW_REQUIRED' if failed else 'COMPONENT_EVIDENCE_REVIEW_REQUIRED', completed_at=time.time())
    node.save_json(lab.job / 'RESULT.json', lab.result)
    disarm(lab, docs)


def disarm(lab, docs):
    need(lab.result.get('cleanup_complete') is True, 'Cannot disarm before owned resource cleanup')
    lab.result['disarm_intent'] = True; node.save_json(lab.job / 'RESULT.json', lab.result)
    if (UNITS / (unit_name(lab.job, 'recover') + '.timer')).exists():
        system(['disable', '--now', unit_name(lab.job, 'recover') + '.timer'])
    if (UNITS / (unit_name(lab.job, 'recover') + '.service')).exists():
        system(['disable', unit_name(lab.job, 'recover') + '.service'])
    for name in docs:
        if (UNITS / name).exists():
            need(node.safe_read(UNITS / name).decode() == docs[name], 'Unit changed before removal')
            (UNITS / name).unlink()
    system(['daemon-reload'])
    lab.result['recovery_disarmed'] = True; node.save_json(lab.job / 'RESULT.json', lab.result)


def execute(job, recovery=False):
    # Even direct _recover/_work entry must reject a newer checkout before STOP,
    # service/cgroup actions or transaction mutation.
    verify_staged_sources(job, executing=True)
    recovery_unit_check(job, read_json(job / 'RESULT.json'))
    if recovery:
        node.atomic(job / 'STOP', b'recovery-only\n')
        observed_work = system(['show', unit_name(job, 'work') + '.service', '-p', 'ActiveState', '-p', 'Result', '-p', 'ExecMainCode', '-p', 'ExecMainStatus'])
        node.atomic(job / ('WORK_RECOVERY_' + str(time.time_ns()) + '.log'), observed_work.encode(), exclusive=True)
        if system(['show', unit_name(job, 'work') + '.service', '-p', 'LoadState', '--value']) != 'not-found':
            system(['stop', unit_name(job, 'work') + '.service'])
    with node.lock(job / 'transaction.lock'):
        lab = Lab(job, recovery)
        if lab.result.get('recovery_disarmed'):
            return
        if recovery:
            lab.result['recovery_attempts'] += 1; node.save_json(job / 'RESULT.json', lab.result)
            if lab.request['scenario'] == 'cleanup-retry' and lab.result['recovery_attempts'] == 1:
                lab.result['injected_cleanup_retry'] = True; node.save_json(job / 'RESULT.json', lab.result)
                raise ValueError('Intentional first recovery attempt failure; retry timer remains armed')
            try:
                cleanup(lab)
            except Exception as error:
                lab.result.update(phase='RECOVERY_REQUIRED', recovery_error=str(error))
                node.save_json(job / 'RESULT.json', lab.result)
                raise
            return
        try:
            peers = lab.build()
            core.Transaction(lab, lab.j).prepare(peers[:2], job)
            nft = json.loads(lab.net('r', ['/usr/sbin/nft', '-j', 'list', 'table', 'inet', 'firewalld']))
            node.save_json(job / 'ACTIVE_NFT.json', nft)
            lab.result['native_scope'] = core.validate_native_policy(nft, peers[:2])
            lab.result['phase'] = 'PRIVATE_POLICY_ACTIVE'; node.save_json(job / 'RESULT.json', lab.result)
            scenario = lab.request['scenario']
            if scenario in ('kill', 'cleanup-retry'):
                lab.result['intentional_sigkill_at'] = time.time(); node.save_json(job / 'RESULT.json', lab.result)
                os.kill(os.getpid(), signal.SIGKILL)
            if scenario in ('deadline', 'reboot'):
                lab.result['phase'] = 'WAIT_REBOOT' if scenario == 'reboot' else 'WAIT_TIMER'
                node.save_json(job / 'RESULT.json', lab.result)
                if scenario == 'reboot': return  # Actual VM reboot is operator-controlled.
                while True: lab.guard(); time.sleep(0.2)
            for a, b, port in ((0,1,8080),(1,0,8080),(0,1,8081),(1,0,8081),(2,0,8080),(0,2,8080)):
                lab.flow(peers[a], peers[b], port)
        except Exception as error:
            lab.result['component_error'] = str(error); node.save_json(job / 'RESULT.json', lab.result)
            capture_initialization_failure(job)
        else:
            lab.result['measurement_sequence_complete'] = True
        # Not a finally block: intentional SIGKILL/deadline/reboot leave recovery armed.
        lab.recovery = True; lab.end = time.monotonic() + 110
        try:
            cleanup(lab)
        except Exception as error:
            lab.result.update(phase='RECOVERY_REQUIRED', recovery_error=str(error))
            node.save_json(job / 'RESULT.json', lab.result)
            raise
        need(lab.result.get('status') != 'COMPONENT_FAILED_REVIEW_REQUIRED',
             'Component test failed; cleanup outcome and evidence are preserved')


def collect(job):
    node.safe_dir(job)
    with node.lock(job / 'transaction.lock', nonblock=True):
        result = read_json(job / 'RESULT.json')
        node.safe_dir(ROOT / 'exports', create=True)
        output = Path(tempfile.mkdtemp(prefix='FDS_CNI_Kernel_Component_', dir=ROOT / 'exports'))
        evidence = output / 'evidence'; evidence.mkdir(mode=0o700)
        for path in sorted(job.iterdir()):
            if path.is_file() and path.suffix in ('.json', '.jsonl', '.pcap', '.trace', '.log'):
                raw = node.safe_read(path); need(len(raw) <= 8_000_000, 'Evidence requires manual size review')
                node.atomic(evidence / path.name, raw, exclusive=True)
        log = job / 'log/firewalld.log'
        if log.exists():
            raw = node.safe_read(log); need(len(raw) <= 8_000_000, 'Firewalld log requires manual size review')
            node.atomic(evidence / 'PRIVATE_FIREWALL.log', raw, exclusive=True)
        raw, journal_state = daemon_journal(job)
        node.atomic(evidence / 'COMPONENT_JOURNAL.log', raw, exclusive=True)
        node.save_json(evidence / 'JOURNAL_COLLECTION.json', journal_state, exclusive=True)
        node.save_json(evidence / 'EXPORT_STATE.json', dict(recovery_pending=not result.get('recovery_disarmed'), snapshot_at=time.time()))
        matrix = dict(schema='fds.cni-kernel-component-matrix.v1', scope='NOT_PRODUCTION_QUALIFICATION',
                      cases={n: 'NOT_TESTED' for n in core.QUALIFICATION_CASES},
                      recovery={n: 'NOT_TESTED' for n in ('disconnect','killed_after_mutation','deadline','reboot','cleanup_retry','nm_ifcfg_unchanged')},
                      reason='Component observations do not exercise the production adapter, physical VM NIC, Calico or production recovery.')
        node.save_json(evidence / 'PRODUCTION_MATRIX.json', matrix)
        redact_export(evidence)
        sums = ''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in sorted(evidence.iterdir()))
        node.atomic(evidence / 'SHA256SUMS', sums.encode())
        archive = output / (output.name + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar: tar.add(evidence, arcname=output.name)
        sidecar = archive.with_name(archive.name + '.sha256')
        sidecar.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
        print('ARCHIVE=' + str(archive)); print('SIDECAR=' + str(sidecar))


def require_inside(job, role):
    need(role in ns_names(job), 'Invalid fixture namespace')
    node.safe_dir(job)
    expected = read_json(job / 'RESOURCES.json')['namespaces'].get(role)
    current = os.stat('/proc/self/ns/net')
    need(expected == [current.st_dev, current.st_ino], 'Refuse host or foreign network namespace')
    need(current.st_ino != os.stat('/proc/1/ns/net').st_ino, 'Host networking forbidden')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', default='plan', choices=('plan','inspect','start','status','recover','collect','_work','_recover','_server','_probe','_firewall'))
    parser.add_argument('value', nargs='?'); parser.add_argument('extra', nargs='*')
    parser.add_argument('--decision', type=Path); parser.add_argument('--scenario', choices=SCENARIOS, default='normal')
    args, unknown = parser.parse_known_args()
    if args.mode != '_firewall':
        need(not unknown, 'Unexpected arguments')
    if args.mode == 'plan':
        print(json.dumps(dict(mode='PLAN_ONLY', scope='KERNEL_COMPONENT_ONLY', scenarios=SCENARIOS,
            source_hashes=hashes(), requires_reviewed_lab_decision=True, production_qualification_supported=False), indent=2)); return
    if args.mode == 'inspect':
        observed = prerequisites()
        observed['executor_bundle_sha256'] = bundle_digest(observed['sources_sha256'])
        node.safe_dir(ROOT / 'inspect', create=True)
        output = Path(tempfile.mkdtemp(prefix='FDS_CNI_Kernel_Inspect_', dir=ROOT / 'inspect'))
        template = dict(schema='fds.cni-kernel-lab-decision.v2', lab_vm='cp1-cni-lab', lan_segment='FDS-CNI-LAB',
            product_uuid=observed['product_uuid'], machine_id_sha256=observed['machine_id_sha256'],
            sources_sha256=observed['sources_sha256'], execution_approved=False, operator='', snapshot_reference='',
            review_url='', reviewed_bundle_sha256='', scope='KERNEL_COMPONENT_ONLY')
        node.save_json(output / 'INSPECT.json', observed); node.save_json(output / 'lab-decision.template.json', template)
        print('INSPECT=' + str(output / 'INSPECT.json')); print('DECISION_TEMPLATE=' + str(output / 'lab-decision.template.json')); return
    if args.mode == '_probe':
        import http.client
        job = job_path(args.value); role, ip = args.extra[:2]
        require_inside(job, role)
        fixture = read_json(job / 'FIXTURE.json')
        need(ip in ['127.0.0.1'] + [p['ip'] for p in fixture['peers']], 'Outside fixture destination')
        port, sport = int(args.extra[2]), int(args.extra[3])
        need(port in (8080,8081) and (sport == 0 or 40000 <= sport < 60000), 'Outside fixture port')
        connection = http.client.HTTPConnection(ip, port, timeout=3, source_address=('', sport))
        result = dict(status=None, tcp_connected=False, connected=False)
        try:
            connection.connect()
            result.update(tcp_connected=True, connected=True)
            connection.auto_open = 0  # A subsequent HTTP error must not trigger a new TCP connection.
            connection.request('GET', '/'); r = connection.getresponse()
            result.update(status=r.status, body=r.read(1024).decode())
        except (OSError, http.client.HTTPException, UnicodeError) as error:
            result.update(error=str(error), error_stage='HTTP' if result['tcp_connected'] else 'CONNECT')
        finally: connection.close()
        print(json.dumps(result)); return
    need(os.geteuid() == 0, 'Root required')
    if args.mode == 'start':
        need(args.decision is not None, 'Reviewed lab decision file required'); start(args.decision, args.scenario); return
    job = job_path(args.value)
    if args.mode == '_server':
        import http.server
        import threading
        role = args.extra[0]; need(role in ('a','b','c'), 'Invalid fixture role')
        require_inside(job, role)
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write((job.name + '/' + role).encode())
            def log_message(self, *args): pass
        threading.Thread(target=http.server.HTTPServer(('0.0.0.0',8081),Handler).serve_forever,daemon=True).start()
        http.server.HTTPServer(('0.0.0.0',8080),Handler).serve_forever(); return
    if args.mode == '_firewall':
        require_inside(job, 'r')
        need(os.stat('/proc/self/ns/mnt').st_ino != os.stat('/proc/1/ns/mnt').st_ino, 'Private mount namespace required')
        call(['/usr/bin/mount', '--make-rprivate', '/'])
        node.safe_dir(job / 'empty-ifcfg')
        target = Path('/etc/sysconfig/network-scripts')
        if target.exists():
            need(target.is_dir() and not target.is_symlink(), 'Unexpected ifcfg directory')
            call(['/usr/bin/mount', '--bind', str(job / 'empty-ifcfg'), str(target)])
            call(['/usr/bin/mount', '-o', 'remount,bind,ro', str(target)])
        # Forward the original tail verbatim: argparse must not reorder firewall options.
        index = __import__('sys').argv.index('_firewall')
        tail = __import__('sys').argv[index+2:]
        value = call(['/usr/bin/firewall-cmd', *tail], env=private_env(job))
        print(value['stdout'], end=''); return
    if args.mode == 'status':
        print(json.dumps(read_json(job / 'RESULT.json'), indent=2)); return
    if args.mode == 'collect': collect(job); return
    if args.mode == 'recover':
        # Direct recovery also handles interrupted staging before a timer was armed.
        manual_recover(job); return
    execute(job, recovery=args.mode == '_recover')


if __name__ == '__main__':
    main()
