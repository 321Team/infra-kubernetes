"""Regression for real job e97d5bbe64efd9cb. Policy/systemd/VM IO is mocked."""
from contextlib import ExitStack
import ctypes
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import tarfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('startup_kernel', SCRIPTS / 'run-cni-kernel-integration.py')
tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)


class PolicyAccess(unittest.TestCase):
    def policy(self, allowed=3, flags=0, rc=0):
        def compute(source, target, kind, wanted, pointer):
            value = ctypes.cast(pointer, ctypes.POINTER(tool.AccessDecision)).contents
            value.allowed = allowed; value.flags = flags
            return rc
        obj = tool.SELinuxPolicy.__new__(tool.SELinuxPolicy)
        obj.domains = {'bus': 'system_u:system_r:system_dbusd_t:s0'}
        obj.lib = SimpleNamespace(security_getenforce=Mock(return_value=1),
            string_to_security_class=Mock(return_value=1),
            string_to_av_perm=Mock(side_effect=lambda cls, value: {b'read': 1, b'write': 2}.get(value, 0)),
            security_compute_av_flags_raw=Mock(side_effect=compute))
        return obj

    def test_allowed_bits_are_required(self):
        self.policy().require('bus', 'target', 'file', 'read write')
        for allowed in (0, 1, 2):
            with self.subTest(allowed=allowed), self.assertRaises(ValueError):
                self.policy(allowed=allowed).require('bus', 'target', 'file', 'read write')

    def test_domain_permissive_is_rejected_even_when_bits_allow(self):
        with self.assertRaises(ValueError): self.policy(flags=1).require('bus', 'target', 'file', 'read')

    def test_global_permissive_is_rejected_before_computation(self):
        obj = self.policy(); obj.lib.security_getenforce.return_value = 0
        with self.assertRaises(ValueError): obj.require('bus', 'target', 'file', 'read')
        obj.lib.security_compute_av_flags_raw.assert_not_called()

    def test_invalid_context_query_cannot_succeed(self):
        with self.assertRaises(ValueError): self.policy(rc=-1).require('bus', 'target', 'file', 'read')

    def test_unknown_permission_and_class_rejected(self):
        obj = self.policy()
        with self.assertRaises(ValueError): obj.require('bus', 'target', 'file', 'bogus')
        obj.lib.string_to_security_class.return_value = 0
        with self.assertRaises(ValueError): obj.require('bus', 'target', 'file', 'read')
        obj.lib.security_compute_av_flags_raw.assert_not_called()


class PolicyPreflightRegression(unittest.TestCase):
    """Use an append-only policy model; no live VM/policy result is synthesized."""
    def policy(self, denied=None, query_rc=0, permissive=False):
        denied = denied or {}
        names = ('open read write append getattr setattr search add_name remove_name '
                 'create rmdir rename unlink connectto connect getopt setopt shutdown').split()
        bits = {name: 1 << index for index, name in enumerate(names)}
        obj = tool.SELinuxPolicy.__new__(tool.SELinuxPolicy)
        obj.domains = {'bus': 'system_u:system_r:system_dbusd_t:s0',
                       'firewall': 'system_u:system_r:firewalld_t:s0'}
        def compute(source, target, kind, wanted, pointer):
            target = target.decode()
            forbidden = denied.get(target, set())
            # Policy grants append/setattr but not arbitrary file writes.
            if ':firewalld_var_log_t:' in target:
                forbidden = forbidden | {'write'}
            # Peer domain grants connectto, not read/write of peer-owned socket.
            if target == obj.domains['bus']:
                forbidden = forbidden | {'read', 'write'}
            result = ctypes.cast(pointer, ctypes.POINTER(tool.AccessDecision)).contents
            result.allowed = wanted & ~sum(bits[p] for p in forbidden)
            result.flags = int(permissive)
            return query_rc
        obj.lib = SimpleNamespace(security_getenforce=Mock(return_value=1),
            string_to_security_class=Mock(return_value=1),
            string_to_av_perm=Mock(side_effect=lambda cls, value: bits.get(value.decode(), 0)),
            security_compute_av_flags_raw=Mock(side_effect=compute))
        return obj

    def check(self, policy):
        with patch.object(tool.Path, 'exists', return_value=False):
            return policy.check()

    def failure(self, policy):
        output = io.StringIO()
        with patch('sys.stdout', output), self.assertRaises(ValueError):
            self.check(policy)
        line = output.getvalue().strip()
        self.assertTrue(line.startswith('SELINUX_POLICY_REPORT='))
        return json.loads(line.split('=', 1)[1])

    def test_append_only_log_and_peer_connect_policy_passes(self):
        report = self.check(self.policy())
        self.assertEqual(report['failed_checks'], 0)
        self.assertEqual(report['checks'], 13)
        log = next(r for r in report['access_decisions'] if ':firewalld_var_log_t:' in r['target_context'])
        self.assertEqual(set(log['required_permissions']), {'open', 'append', 'getattr', 'setattr'})
        sockets = [r for r in report['access_decisions'] if r['object_class'] == 'unix_stream_socket']
        self.assertEqual(sockets[0]['required_permissions'], ['connectto'])
        self.assertEqual(sockets[1]['source_context'], sockets[1]['target_context'])
        self.assertIn('read', sockets[1]['required_permissions'])
        self.assertIn('write', sockets[1]['required_permissions'])

    def test_required_log_append_and_fchmod_remain_mandatory(self):
        context = 'system_u:object_r:firewalld_var_log_t:s0'
        for permission in ('open', 'append', 'getattr', 'setattr'):
            with self.subTest(permission=permission):
                report = self.failure(self.policy({context: {permission}}))
                failed = [r for r in report['access_decisions'] if not r['ok']]
                self.assertEqual(len(failed), 1)
                self.assertEqual(failed[0]['denied_permissions'], [permission])

    def test_reports_all_denials_including_after_first_failure(self):
        report = self.failure(self.policy({
            'system_u:object_r:dbusd_etc_t:s0': {'read'},
            'system_u:object_r:firewalld_var_log_t:s0': {'append', 'setattr'},
            'system_u:system_r:firewalld_t:s0': {'write'}}))
        failed = [r for r in report['access_decisions'] if not r['ok']]
        self.assertEqual(report['checks'], 13)
        self.assertEqual(report['failed_checks'], 3)
        self.assertEqual(failed[1]['denied_permissions'], ['append', 'setattr'])
        self.assertEqual(failed[-1]['denied_permissions'], ['write'])

    def test_failed_api_does_not_claim_denied_permissions(self):
        report = self.failure(self.policy(query_rc=-1))
        self.assertEqual(report['failed_checks'], 13)
        for result in report['access_decisions']:
            self.assertIsNone(result['denied_permissions'])
            self.assertIsNone(result['allowed_mask'])
            self.assertEqual(result['query_rc'], -1)

    def test_domain_permissive_is_not_reported_as_policy_pass(self):
        report = self.failure(self.policy(permissive=True))
        self.assertEqual(report['failed_checks'], 13)
        self.assertTrue(all(r['domain_permissive'] for r in report['access_decisions']))

    def test_unknown_permission_is_recorded_and_remaining_checks_continue(self):
        policy = self.policy()
        original = policy.lib.string_to_av_perm.side_effect
        policy.lib.string_to_av_perm.side_effect = lambda cls, value: 0 if value == b'append' else original(cls, value)
        report = self.failure(policy)
        self.assertEqual(report['checks'], 13)
        self.assertEqual(report['failed_checks'], 1)
        failed = next(r for r in report['access_decisions'] if not r['ok'])
        self.assertIn('Unknown SELinux permission: append', failed['query_error'])

    def test_enforcing_loss_during_query_cannot_produce_pass(self):
        policy = self.policy()
        policy.lib.security_getenforce.side_effect = [1] * 13 + [0]
        report = self.failure(policy)
        self.assertFalse(report['enforcing'])


class OwnedLabels(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.job = self.root / ('a' * 16); self.job.mkdir(mode=0o700)
        self.stack.enter_context(patch.object(tool, 'ROOT', self.root))
        self.stack.enter_context(patch.object(tool.node, 'safe_dir'))
        self.contexts = {}
        self.set_label = self.stack.enter_context(patch.object(tool.os, 'setxattr',
            side_effect=lambda path, key, value, **kwargs: self.contexts.__setitem__(str(path), value)))
        self.stack.enter_context(patch.object(tool.os, 'getxattr',
            side_effect=lambda path, key, **kwargs: self.contexts.get(str(path), b'system_u:object_r:var_lib_t:s0')))

    def test_relabel_is_limited_to_allowlisted_new_objects(self):
        path = self.job / 'bus.conf'; path.write_bytes(b'config'); path.chmod(0o600)
        tool.label_owned_path(self.job, 'bus.conf')
        self.assertEqual(self.contexts[str(path)], b'system_u:object_r:dbusd_etc_t:s0')
        for relative in ('', '.', '..', '../bus.conf', 'REQUEST.json', '/etc/shadow'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                tool.label_owned_path(self.job, relative)
        self.assertEqual(self.set_label.call_count, 1)

    def test_job_outside_root_cannot_be_relabelled(self):
        with self.assertRaises(ValueError): tool.label_owned_path(self.root, 'bus.conf')
        self.set_label.assert_not_called()

    def test_symlink_hardlink_and_writable_target_rejected(self):
        path = self.job / 'bus.conf'; other = self.job / 'other'; other.write_bytes(b'x')
        path.symlink_to(other)
        with self.assertRaises(ValueError): tool.label_owned_path(self.job, 'bus.conf')
        path.unlink(); os.link(other, path)
        with self.assertRaises(ValueError): tool.label_owned_path(self.job, 'bus.conf')
        path.unlink(); path.write_bytes(b'x'); path.chmod(0o666)
        with self.assertRaises(ValueError): tool.label_owned_path(self.job, 'bus.conf')
        self.set_label.assert_not_called()

    def test_label_write_failure_is_fatal(self):
        path = self.job / 'bus.conf'; path.write_bytes(b'config'); path.chmod(0o600)
        self.set_label.side_effect = PermissionError('enforced deny')
        with self.assertRaises(PermissionError): tool.label_owned_path(self.job, 'bus.conf')

    def test_label_readback_mismatch_is_fatal(self):
        path = self.job / 'bus.conf'; path.write_bytes(b'config'); path.chmod(0o600)
        self.set_label.side_effect = None
        with self.assertRaisesRegex(ValueError, 'did not persist'): tool.label_owned_path(self.job, 'bus.conf')

    def test_policy_failure_precedes_directory_creation(self):
        with patch.object(tool, 'SELinuxPolicy') as policy:
            policy.return_value.check.side_effect = ValueError('target policy denies')
            with self.assertRaises(ValueError): tool.prepare_daemon_files(self.job)
        self.assertEqual(list(self.job.iterdir()), []); self.set_label.assert_not_called()

    def test_configuration_and_log_exist_before_daemon_start(self):
        with patch.object(tool, 'SELinuxPolicy') as policy:
            policy.return_value.check.return_value = {'enforcing': True}
            policy.return_value.domains = {'bus': 'bus', 'firewall': 'firewall'}
            tool.prepare_daemon_files(self.job)
            with self.assertRaises(FileExistsError): tool.prepare_daemon_files(self.job)
        self.assertEqual((self.job / 'log/firewalld.log').read_bytes(), b'')
        self.assertIn('unix:path=' + str(self.job / 'dbus/bus.sock'), (self.job / 'bus.conf').read_text())
        self.assertNotIn(str(self.job), self.contexts)
        self.assertEqual(self.contexts[str(self.job / 'firewalld/zones/public.xml')],
                         b'system_u:object_r:firewalld_etc_rw_t:s0')
        self.assertEqual(self.contexts[str(self.job / 'run')], b'system_u:object_r:firewalld_var_run_t:s0')
        layout = json.loads((self.job / 'SELINUX_LAYOUT.json').read_text())
        self.assertEqual(len(layout['objects']), 9)

    def test_label_error_precedes_network_namespace_mutation(self):
        lab = tool.Lab.__new__(tool.Lab); lab.job = self.job; lab.create_namespace = Mock()
        (self.job / 'ARMED.json').write_text('{}')
        with patch.object(tool, 'validate_units'), patch.object(tool, 'system', return_value='active'), \
             patch.object(tool, 'fingerprint_host', return_value={}), \
             patch.object(tool, 'prepare_daemon_files', side_effect=ValueError('bad label')):
            with self.assertRaises(ValueError): lab.build()
        lab.create_namespace.assert_not_called()


class BusAdmissionPolicy(unittest.TestCase):
    def setUp(self):
        self.config = ET.fromstring(tool.private_bus_config(tool.job_path('a' * 16)))

    def test_connection_admission_is_never_per_user_or_group(self):
        for policy in self.config.findall('policy'):
            for rule in policy:
                if 'user' in rule.attrib or 'group' in rule.attrib:
                    self.assertIn(policy.get('context'), ('default', 'mandatory'))
                    self.assertNotIn('user', policy.attrib)
                    self.assertNotIn('group', policy.attrib)

    def test_only_root_admitted_and_private_auth_address_preserved(self):
        admissions = [(r.tag, r.attrib) for p in self.config.findall('policy')
                      for r in p if 'user' in r.attrib or 'group' in r.attrib]
        self.assertEqual(admissions, [('deny', {'user': '*'}), ('allow', {'user': 'root'})])
        self.assertEqual([a.text for a in self.config.findall('auth')], ['EXTERNAL'])
        self.assertEqual([a.text for a in self.config.findall('listen')],
                         [tool.private_env(tool.job_path('a' * 16))['DBUS_SYSTEM_BUS_ADDRESS']])
        self.assertFalse(self.config.findall('include') + self.config.findall('includedir'))
        self.assertFalse(self.config.findall('allow_anonymous'))


class Readiness(unittest.TestCase):
    def setUp(self):
        self.job = tool.job_path('a' * 16)
        self.lab = SimpleNamespace(job=self.job, cmd=Mock(return_value='u 123'), guard=Mock(), command=Mock(return_value='running'))
        self.active = {'ActiveState': 'active', 'MainPID': '123'}

    def test_dead_bus_never_starts_firewall(self):
        def system(args, **kwargs):
            if args[0] == 'show': return 'ActiveState=failed\nMainPID=0\nExecMainStatus=1'
            return ''
        with patch.object(tool, 'system', side_effect=system) as run:
            with self.assertRaisesRegex(ValueError, 'bus stopped'): tool.start_private_daemons(self.lab)
        self.assertFalse(any('firewall.service' in ' '.join(c.args[0]) for c in run.call_args_list))

    def test_unowned_socket_rejected_before_probe(self):
        with patch.object(tool, 'service_state', return_value=self.active), \
             patch.object(tool, 'private_socket', side_effect=ValueError('Unexpected private bus socket')):
            with self.assertRaises(ValueError): tool.wait_for_bus(self.lab)
        self.lab.cmd.assert_not_called()

    def test_missing_socket_times_out(self):
        with patch.object(tool, 'service_state', return_value=self.active), \
             patch.object(tool, 'private_socket', side_effect=FileNotFoundError), \
             patch.object(tool.time, 'monotonic', side_effect=[0, 16]):
            with self.assertRaisesRegex(ValueError, 'readiness timeout'): tool.wait_for_bus(self.lab)
        self.lab.cmd.assert_not_called()

    def test_probe_uses_explicit_private_address(self):
        with patch.object(tool, 'service_state', return_value=self.active), patch.object(tool, 'private_socket'):
            tool.wait_for_bus(self.lab)
        args = self.lab.cmd.call_args.args[0]
        self.assertIn('--address=unix:path=' + str(self.job / 'dbus/bus.sock'), args)
        self.assertNotIn('--system', args)

    def test_probe_wrong_pid_rejected(self):
        self.lab.cmd.return_value = 'u 456'
        with patch.object(tool, 'service_state', return_value=self.active), patch.object(tool, 'private_socket'):
            with self.assertRaisesRegex(ValueError, 'PID does not match'): tool.wait_for_bus(self.lab)

    def test_transient_bus_response_can_retry_without_host_fallback(self):
        self.lab.cmd.side_effect = [ValueError('not ready'), 'u 123']
        with patch.object(tool, 'service_state', return_value=self.active), patch.object(tool, 'private_socket'), \
             patch.object(tool.time, 'sleep'):
            tool.wait_for_bus(self.lab)
        self.assertEqual(self.lab.cmd.call_count, 2)

    def test_bus_process_change_rejected(self):
        with patch.object(tool, 'service_state', side_effect=[self.active, dict(self.active, MainPID='456')]), \
             patch.object(tool, 'private_socket'):
            with self.assertRaisesRegex(ValueError, 'Bus process changed'): tool.wait_for_bus(self.lab)

    def test_firewall_failure_does_not_wait_full_probe_timeout(self):
        def state(job, role):
            if role == 'firewall': raise ValueError('firewall stopped')
            return self.active
        with patch.object(tool, 'system'), patch.object(tool, 'wait_for_bus'), \
             patch.object(tool, 'service_state', side_effect=state):
            with self.assertRaisesRegex(ValueError, 'firewall stopped'): tool.start_private_daemons(self.lab)
        self.lab.command.assert_not_called()

    def test_wrong_socket_kind_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory); (job / 'dbus').mkdir(); (job / 'dbus/bus.sock').write_text('not a socket')
            with patch.object(tool.node, 'safe_dir'):
                with self.assertRaises(ValueError): tool.private_socket(job)


class RuntimeMount(unittest.TestCase):
    def check(self, bad=None):
        job = tool.job_path('a' * 16)
        inodes = {str(job / 'run'): 30, '/proc/123/root/run/firewalld': 30,
                  '/run/firewalld': 40, '/proc/123/ns/mnt': 50, '/proc/1/ns/mnt': 60,
                  '/proc/123/ns/net': 70, '/run/netns/' + tool.ns_names(job)['r']: 70}
        if bad == 'shared_runtime': inodes['/proc/123/root/run/firewalld'] = 40
        if bad == 'host_mount': inodes['/proc/123/ns/mnt'] = 60
        if bad == 'host_network': inodes['/proc/123/ns/net'] = 80
        label = 'var_run_t' if bad == 'wrong_label' else 'firewalld_var_run_t'
        with patch.object(tool, 'service_state', return_value={'MainPID': '123'}), \
             patch.object(tool.Path, 'stat', lambda p, **kw: SimpleNamespace(st_dev=1, st_ino=inodes[str(p)])), \
             patch.object(tool, 'file_context', return_value='system_u:object_r:' + label + ':s0'), \
             patch.object(tool.node, 'save_json') as save:
            tool.verify_runtime_view(job)
            return save.call_args.args[1]

    def test_private_runtime_and_namespaces_are_verified(self):
        result = self.check()
        self.assertTrue(result['private_mount_verified']); self.assertEqual(result['private_runtime'], [1, 30])

    def test_shared_runtime_host_namespace_and_wrong_label_rejected(self):
        for bad in ('shared_runtime', 'host_mount', 'host_network', 'wrong_label'):
            with self.subTest(bad=bad), self.assertRaises(ValueError): self.check(bad)

    def test_units_do_not_allow_job_root_or_host_runtime_writes(self):
        job = tool.job_path('a' * 16); docs = tool.documents(job, 'normal')
        fw = docs[tool.unit_name(job, 'firewall') + '.service']
        bus = docs[tool.unit_name(job, 'bus') + '.service']
        self.assertNotIn('ReadWritePaths=' + str(job) + '\n', fw)
        self.assertNotIn('ReadWritePaths=/run/firewalld', fw)
        self.assertIn('BindPaths=' + str(job / 'run') + ':/run/firewalld:norbind', fw)
        self.assertIn('ReadWritePaths=' + str(job / 'dbus') + '\n', bus)


class JournalEvidence(unittest.TestCase):
    def test_collection_is_scoped_and_reports_separate_rc(self):
        job = tool.job_path('a' * 16)
        with patch.object(tool, 'read_json', return_value={'started_at': 100}), \
             patch.object(tool.time, 'time', return_value=200), \
             patch.object(tool, 'call', return_value={'rc': 1, 'stdout': 'partial', 'stderr': 'no journal'}) as run:
            raw, state = tool.daemon_journal(job)
        self.assertEqual(raw, b'partial'); self.assertEqual(state['rc'], 1)
        argv = run.call_args.args[0]
        self.assertIn('--since', argv); self.assertIn('--until', argv)
        self.assertEqual(argv.count('--unit'), 4)
        self.assertTrue(all('fds-ki-' + job.name in argv[i+1] for i, x in enumerate(argv) if x == '--unit'))

    def test_journal_timeout_is_diagnostic_not_test_success(self):
        with patch.object(tool, 'read_json', return_value={'started_at': 1}), \
             patch.object(tool, 'call', side_effect=ValueError('Command timeout')):
            raw, state = tool.daemon_journal(tool.job_path('a' * 16))
        self.assertEqual(raw, b''); self.assertEqual(state['status'], 'UNAVAILABLE')
        self.assertIn('Command timeout', state['error'])

    def test_failed_diagnostic_storage_does_not_prevent_cleanup(self):
        with patch.object(tool, 'daemon_journal', return_value=(b'original failure', {'rc': 0})), \
             patch.object(tool.node, 'atomic', side_effect=OSError('full disk')):
            tool.capture_initialization_failure(tool.job_path('a' * 16))


class ExportPrivacy(unittest.TestCase):
    UUID = '12345678-1234-abcd-9012-123456789abc'

    def test_text_uuid_variants_redacted_and_original_hash_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); evidence = root / 'evidence'; evidence.mkdir()
            original = root / 'REQUEST.json'
            value = json.dumps({'product_uuid': self.UUID, 'copy': self.UUID.upper(), 'source_sha256': 'a' * 64}).encode()
            original.write_bytes(value); (evidence / original.name).write_bytes(value)
            with patch.object(tool.node, 'safe_dir'):
                tool.redact_export(evidence)
            exported = (evidence / original.name).read_bytes()
            self.assertNotIn(self.UUID.encode(), exported); self.assertNotIn(self.UUID.upper().encode(), exported)
            self.assertEqual(original.read_bytes(), value)
            data = json.loads(exported); self.assertEqual(data['product_uuid'], data['copy'])
            self.assertEqual(data['source_sha256'], 'a' * 64)
            ledger = json.loads((evidence / 'EXPORT_REDACTION.json').read_text())['files']['REQUEST.json']
            self.assertEqual(ledger['redacted_uuid_count'], 2)
            self.assertEqual(ledger['source_bytes_sha256'], hashlib.sha256(value).hexdigest())
            self.assertEqual(ledger['export_bytes_sha256'], hashlib.sha256(exported).hexdigest())

    def test_uuid_in_binary_blocks_automatic_export(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory); (evidence / 'flow.pcap').write_bytes(b'\x00\xff' + self.UUID.encode())
            with patch.object(tool.node, 'safe_dir'), self.assertRaisesRegex(ValueError, 'binary evidence'):
                tool.redact_export(evidence)

    def test_packet_bytes_without_uuid_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory); packet = b'\x00\xffabc123'; (evidence / 'flow.pcap').write_bytes(packet)
            with patch.object(tool.node, 'safe_dir'): tool.redact_export(evidence)
            self.assertEqual((evidence / 'flow.pcap').read_bytes(), packet)

    def test_collected_archive_redacts_uuid_and_keeps_all_checksums_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); job = root / ('a' * 16); job.mkdir()
            request = json.dumps({'started_at': 1, 'observed': {'product_uuid': self.UUID}}).encode()
            (job / 'REQUEST.json').write_bytes(request)
            (job / 'RESULT.json').write_text(json.dumps({'recovery_disarmed': True}))
            def safe_dir(path, create=False):
                if create: path.mkdir(parents=True, exist_ok=True)
            with patch.object(tool, 'ROOT', root), patch.object(tool.node, 'safe_dir', side_effect=safe_dir), \
                 patch.object(tool, 'daemon_journal', return_value=(b'', {'status': 'UNAVAILABLE', 'rc': 1})), \
                 patch('builtins.print'):
                tool.collect(job)
            archive = next((root / 'exports').glob('*/*.tar.gz'))
            with tarfile.open(archive) as tar:
                files = {Path(m.name).name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
            for name, raw in files.items(): self.assertNotIn(self.UUID.encode(), raw, name)
            for line in files['SHA256SUMS'].decode().splitlines():
                digest, name = line.split('  ', 1)
                self.assertEqual(digest, hashlib.sha256(files[name]).hexdigest())
            self.assertEqual((job / 'REQUEST.json').read_bytes(), request)
            self.assertFalse(json.loads(files['EXPORT_STATE.json'])['recovery_pending'])
            self.assertEqual(json.loads(files['JOURNAL_COLLECTION.json'])['rc'], 1)


if __name__ == '__main__': unittest.main()
