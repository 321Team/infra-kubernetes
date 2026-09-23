"""Synthetic evidence only. The embedded historical probe is hashed, never run."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_node_firewall import load, cp1_producer_receipt, FakeBackend

a = load('trial_analysis_test', 'scripts/analyze-cp1-cni-trial.py')
PROBE = (a.ROOT / 'tests/fixtures/CP1_CNI_TRIAL_PROBE.txt').read_text()
NS = 'fds-cni-static-mock'
T0, T1, T2, T3, T4, T5, END = [f'2026-09-15T01:22:{s:02d}+00:00' for s in (0, 1, 2, 3, 4, 5, 8)]


def query(argv, stdout='', when=T1):
    return dict(argv=argv, stdout=stdout if isinstance(stdout, str) else json.dumps(stdout),
                stderr='', rc=0, truncated=False, started_at=when, ended_at=when)


def fixture():
    request = dict(schema='fds.cp1-cni-trial-controller.v2', source_commit=a.REFERENCE_SOURCE,
                   acceptance='REVIEW_REQUIRED', kit_hashes=copy.deepcopy(a.REFERENCE_KIT),
                   request=dict(namespace=NS, until='2026-09-15T01:42:00+00:00', max_seconds=1200,
                                cleanup_seconds=180))
    peers = [dict(metadata=dict(name=name, namespace=NS, uid='MOCK-' + name),
                  spec=dict(nodeName='k8s-control01', hostNetwork=False),
                  status=dict(podIP=ip, phase='Running', containerStatuses=[dict(ready=True)]))
             for name, ip in (('a', '10.244.246.80'), ('b', '10.244.246.81'))]
    service = dict(metadata=dict(name='peer-b', namespace=NS),
                   spec=dict(clusterIP='10.108.82.56', selector={'fds-cni-trial': NS, 'trial-peer': 'b'},
                             ports=[dict(port=8080, targetPort=8080, protocol='TCP')]))
    qs = [query(['get', 'pods', '-n', NS, '-o', 'json'], {'items': peers}),
          query(['get', 'service', 'peer-b', '-n', NS, '-o', 'json'], service)]
    checks = {}
    for name, other, ip in (('a', 'b', '10.244.246.81'), ('b', 'a', '10.244.246.80')):
        http = [dict(ip=ip, expected=NS + '/' + other, test=name + '_to_' + other + '_pod')]
        if name == 'a':
            http.append(dict(ip='10.108.82.56', expected=NS + '/b', test='a_to_service'))
        payload = dict(http=http, dns_name='peer-b.' + NS + '.svc', dns_ip='10.108.82.56')
        checks[name] = dict(http=[dict(item, status=200, body=item['expected'], passed=True) for item in http],
                            dns=dict(name=payload['dns_name'], expected=payload['dns_ip'],
                                     addresses=[payload['dns_ip']], passed=True))
        qs.append(query(['exec', '-n', NS, name, '-c', 'probe', '--', 'python3', '-I', '-B', '-c',
                         PROBE, json.dumps(payload)], checks[name], T2 if name == 'a' else T3))
    qs.extend([query(['delete', '--raw=/api/v1/namespaces/' + NS, '-f', '-'], when=T4),
               query(['get', 'namespace', NS, '--ignore-not-found', '-o', 'json'], when=T5)])
    fw = dict(hostname='k8s-control01', ip='10.1.93.102', planes={'runtime': {'rules': ['MOCK-original']}})
    result = dict(schema='fds.cp1-cni-trial.v2', **request['request'], started_at=T0, ended_at=END,
                  queries=qs, checks=checks, cross_node='NOT_TESTED', acceptance='REVIEW_REQUIRED',
                  cleanup='CONFIRMED_ABSENT', firewall_before=fw, firewall_after=copy.deepcopy(fw),
                  firewall_unchanged=True, health_before={'node_uid': 'MOCK-uid'},
                  health_after={'node_uid': 'MOCK-uid'}, health_unchanged=True, test_status='PASS')
    request['kit_hashes']['expected-firewall.json'] = a.sha(a.encoded(fw))
    return request, dict(copy.deepcopy(request), rc=0, stderr='', result=result)


def sync_probe(wrapper, name='a'):
    for q in wrapper['result']['queries']:
        if q['argv'][0] == 'exec' and q['argv'][3] == name:
            q['stdout'] = json.dumps(wrapper['result']['checks'][name])


def full_cp1_proof():
    original_init = FakeBackend.__init__
    def with_ip(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.state['ip'] = '10.1.93.102'
    # Supply a fuller mock snapshot before the real pinned producer executes.
    with patch.object(FakeBackend, '__init__', with_ip):
        return cp1_producer_receipt()


def pack(request, result, mutate=None):
    files = [('REQUEST.json', a.encoded(request)), ('RESULT.json', a.encoded(result))]
    sums = ''.join(a.sha(raw) + '  ' + name + '\n' for name, raw in files).encode()
    files.append(('SHA256SUMS', sums))
    if mutate:
        mutate(files)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as t:
        for name, raw in files:
            info = tarfile.TarInfo('FDS_CP1_CNI_Trial_mock/' + name)
            if isinstance(raw, tarfile.TarInfo):
                info = raw
                t.addfile(info)
            else:
                info.size = len(raw)
                t.addfile(info, io.BytesIO(raw))
    return stream.getvalue()


class TrialEvidence(unittest.TestCase):
    def setUp(self):
        self.request, self.wrapper = fixture()

    def report(self):
        return a.analyze(self.request, self.wrapper)

    def blocked(self, label):
        result = self.report()
        self.assertIn(label, result['issues'])
        self.assertEqual(result['status'], 'INCOMPLETE_REVIEW_REQUIRED')

    def test_five_semantic_flows_pass_without_packet_trace_or_service_qualification(self):
        r = self.report()
        self.assertEqual(r['passed_flows'], 5)
        self.assertEqual(r['issues'], [])
        self.assertEqual(r['status'], 'FIVE_FLOWS_REVIEW_REQUIRED')
        self.assertFalse(r['cp1_accepted'] or r['authorizes_cp2_change'] or r['authorizes_join'])

    def test_original_failure_pattern_is_three_of_five_despite_rc_zero(self):
        for name in ('a', 'b'):
            h = self.wrapper['result']['checks'][name]['http'][0]
            h.pop('status'); h.pop('body'); h.update(passed=False, error='MOCK unreachable')
            sync_probe(self.wrapper, name)
        self.wrapper['result']['test_status'] = 'FAIL'
        r = self.report()
        self.assertEqual(r['passed_flows'], 3)
        self.assertEqual(r['flows']['a_to_service'], 'PASS')
        self.assertEqual(r['flows']['a_to_b_pod'], 'FAIL')
        self.assertEqual(r['issues'], [])

    def test_summary_only_edit_is_rejected(self):
        self.wrapper['result']['checks']['a']['http'][0]['body'] = 'wrong'
        self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_rehashed_true_flags_cannot_hide_http_failures(self):
        for field, value in (('body', 'wrong'), ('status', 503), ('status', True), ('error', 'failed')):
            with self.subTest(field=field, value=value):
                self.request, self.wrapper = fixture()
                self.wrapper['result']['checks']['a']['http'][0][field] = value
                sync_probe(self.wrapper)
                self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_dns_must_resolve_exact_service(self):
        for answers in ([], ['10.1.93.102'], ['10.108.82.56', '10.1.93.102']):
            with self.subTest(answers=answers):
                self.request, self.wrapper = fixture()
                self.wrapper['result']['checks']['a']['dns']['addresses'] = answers
                sync_probe(self.wrapper)
                self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_missing_duplicate_or_extra_flow_does_not_become_five_passes(self):
        for mutation in (lambda x: x.pop(), lambda x: x.append(x[0]), lambda x: x.reverse()):
            self.request, self.wrapper = fixture()
            mutation(self.wrapper['result']['checks']['a']['http'])
            sync_probe(self.wrapper)
            self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_probe_source_or_payload_change_is_rejected(self):
        for index, value in ((-2, 'print("PASS")'), (-1, '{}')):
            self.request, self.wrapper = fixture()
            self.wrapper['result']['queries'][2]['argv'][index] = value
            self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_duplicate_exec_does_not_substitute_for_other_direction(self):
        self.wrapper['result']['queries'][3] = copy.deepcopy(self.wrapper['result']['queries'][2])
        self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_endpoint_from_different_observed_pod_is_rejected(self):
        q = self.wrapper['result']['queries'][0]
        p = json.loads(q['stdout']); p['items'][1]['status']['podIP'] = '10.244.246.82'
        q['stdout'] = json.dumps(p)
        self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_nonlocal_hostnetwork_or_unready_pod_is_rejected(self):
        for kind in ('node', 'host', 'ready'):
            self.request, self.wrapper = fixture()
            q = self.wrapper['result']['queries'][0]; pods = json.loads(q['stdout'])
            p = pods['items'][0]
            if kind == 'node': p['spec']['nodeName'] = 'k8s-control02'
            if kind == 'host': p['spec']['hostNetwork'] = True
            if kind == 'ready': p['status']['containerStatuses'][0]['ready'] = False
            q['stdout'] = json.dumps(pods)
            self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_later_pod_observation_cannot_supply_probe_identity(self):
        self.wrapper['result']['queries'][0].update(started_at=T4, ended_at=T4)
        self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_service_selector_or_port_change_is_rejected(self):
        q = self.wrapper['result']['queries'][1]; svc = json.loads(q['stdout'])
        svc['spec']['selector']['trial-peer'] = 'a'; q['stdout'] = json.dumps(svc)
        self.blocked('FIVE_FLOW_EVIDENCE_INCONSISTENT')

    def test_command_failure_truncation_or_warning_stays_visible_with_five_flows(self):
        for field, value in (('rc', 124), ('rc', False), ('truncated', True), ('stderr', 'warning')):
            self.request, self.wrapper = fixture()
            self.wrapper['result']['queries'][0][field] = value
            self.blocked('COMMAND_INTEGRITY_OR_TIME')

    def test_request_mismatch_and_unreviewed_probe_source_are_flagged(self):
        for mutate in (lambda w: w.update(source_commit='f' * 40),
                       lambda w: w['result'].update(namespace='fds-cni-foreign')):
            self.request, self.wrapper = fixture(); mutate(self.wrapper)
            self.blocked('ENVELOPE_SOURCE_OR_TRANSPORT')

    def test_controller_helper_or_expected_baseline_change_requires_review(self):
        for field in ('run.py', 'remote.py', 'expected-firewall.json'):
            self.request, self.wrapper = fixture()
            self.request['kit_hashes'][field] = self.wrapper['kit_hashes'][field] = 'f' * 64
            self.blocked('ENVELOPE_SOURCE_OR_TRANSPORT')

    def test_transport_rc_and_stderr_cannot_be_used_as_flow_pass(self):
        self.wrapper['rc'] = 1
        self.blocked('ENVELOPE_SOURCE_OR_TRANSPORT')

    def test_expired_budget_naive_or_out_of_window_command_is_rejected(self):
        for value in ('2026-09-15T01:21:00+00:00', '2026-09-15T01:22:02', '2026-09-15T02:22:00+00:00'):
            self.request, self.wrapper = fixture()
            self.wrapper['result']['queries'][2]['started_at'] = value
            self.blocked('COMMAND_INTEGRITY_OR_TIME')

    def test_firewall_drift_cannot_hide_behind_unchanged_flag(self):
        self.wrapper['result']['firewall_after']['planes']['runtime']['rules'].append('foreign')
        self.blocked('END_STATE_OR_CLEANUP')
        self.assertEqual(self.report()['firewall_changed_paths'], [['planes', 'runtime', 'rules']])

    def test_health_drift_is_reported_without_identity_values(self):
        self.wrapper['result']['health_after']['node_uid'] = 'PRIVATE-NEW-UUID'
        self.blocked('END_STATE_OR_CLEANUP')
        self.assertNotIn('PRIVATE-NEW-UUID', json.dumps(self.report()))

    def test_cleanup_flag_without_post_delete_absence_is_rejected(self):
        self.wrapper['result']['queries'][-1]['stdout'] = '{"metadata":{"uid":"mock"}}'
        self.blocked('END_STATE_OR_CLEANUP')

    def test_initial_namespace_absence_is_not_cleanup_proof(self):
        self.wrapper['result']['queries'][-1].update(started_at=T1, ended_at=T1)
        self.blocked('END_STATE_OR_CLEANUP')

    def test_summary_pass_claim_does_not_override_flow_failure(self):
        self.wrapper['result']['test_status'] = 'FAIL'
        self.blocked('SUMMARY_CONTRADICTS_FLOWS')

    def test_valid_legacy_receipt_compares_entire_after_without_rewriting_it(self):
        proof = full_cp1_proof(); original = copy.deepcopy(proof)
        for name in ('firewall_before', 'firewall_after'):
            self.wrapper['result'][name] = copy.deepcopy(proof['receipt']['after'])
        digest = a.sha(a.encoded(proof['receipt']['after']))
        self.request['kit_hashes']['expected-firewall.json'] = self.wrapper['kit_hashes']['expected-firewall.json'] = digest
        with patch.object(subprocess, 'run', side_effect=AssertionError('no process execution')):
            r = a.analyze(self.request, self.wrapper, proof)
        self.assertTrue(r['legacy_receipt_after_matches'])
        self.assertEqual(r['issues'], [])
        self.assertEqual(proof, original)

    def test_changed_but_stable_cni_after_still_blocks_old_receipt(self):
        proof = full_cp1_proof()
        for name in ('firewall_before', 'firewall_after'):
            self.wrapper['result'][name] = copy.deepcopy(proof['receipt']['after'])
            self.wrapper['result'][name]['planes']['runtime']['rules'].append('MOCK CNI addition')
        digest = a.sha(a.encoded(self.wrapper['result']['firewall_before']))
        self.request['kit_hashes']['expected-firewall.json'] = self.wrapper['kit_hashes']['expected-firewall.json'] = digest
        r = a.analyze(self.request, self.wrapper, proof)
        self.assertFalse(r['legacy_receipt_after_matches'])
        self.assertIn('LEGACY_RECEIPT_OR_AFTER_MISMATCH', r['issues'])
        self.assertEqual(len(r['issues']), 1)
        self.assertEqual(r['passed_flows'], 5)

    def test_invalid_legacy_receipt_is_not_promoted_by_equal_after(self):
        proof = cp1_producer_receipt(); proof['receipt']['source_commit'] = 'f' * 40
        r = a.analyze(self.request, self.wrapper, proof)
        self.assertIn('LEGACY_RECEIPT_OR_AFTER_MISMATCH', r['issues'])
        self.assertIsNone(r['legacy_receipt_after_matches'])


class ArchiveIO(unittest.TestCase):
    def read(self, raw, digest=None):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'input.tar.gz'; p.write_bytes(raw)
            return a.archive_inputs(p, digest or a.sha(raw))

    def test_valid_archive_does_not_extract_files(self):
        request, result = fixture()
        with patch.object(tarfile.TarFile, 'extractall', side_effect=AssertionError('no extraction')):
            r, w, sums = self.read(pack(request, result))
        self.assertEqual(r, request); self.assertEqual(w, result)
        self.assertEqual(set(sums), {'REQUEST.json', 'RESULT.json'})

    def test_external_hash_failure_stops_before_tar_open(self):
        with patch.object(tarfile, 'open', side_effect=AssertionError('must not parse')):
            with self.assertRaises(ValueError): self.read(b'invalid', 'f' * 64)

    def test_internal_hash_failure_is_rejected(self):
        with self.assertRaises(ValueError):
            self.read(pack(*fixture(), lambda files: files.__setitem__(0, ('REQUEST.json', b'{}'))))

    def test_duplicate_or_traversing_member_is_rejected(self):
        for name in ('REQUEST.json', './REQUEST.json', '../../outside'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.read(pack(*fixture(), lambda files: files.append((name, b'{}'))))

    def test_links_are_never_read(self):
        link = tarfile.TarInfo('FDS_CP1_CNI_Trial_mock/REQUEST.json')
        link.type = tarfile.SYMTYPE; link.linkname = '/etc/passwd'
        with self.assertRaises(ValueError):
            self.read(pack(*fixture(), lambda files: files.__setitem__(0, ('REQUEST.json', link))))

    def test_duplicate_manifest_and_json_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            self.read(pack(*fixture(), lambda files: files.__setitem__(2, ('SHA256SUMS', files[2][1] * 2))))
        for raw in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.assertRaises(ValueError): a.parse(raw)

    def test_cli_returns_review_report_with_authority_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'in.tar.gz'; raw = pack(*fixture()); p.write_bytes(raw)
            run = subprocess.run([sys.executable, str(a.ROOT / 'scripts/analyze-cp1-cni-trial.py'),
                                  str(p), '--sha256', a.sha(raw)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        r = json.loads(run.stdout)
        self.assertEqual(r['passed_flows'], 5)
        self.assertFalse(r['cp1_accepted'])

    def test_cli_invalid_input_is_bounded_json_failure(self):
        run = subprocess.run([sys.executable, str(a.ROOT / 'scripts/analyze-cp1-cni-trial.py'),
                              '/nonexistent', '--sha256', 'f' * 64], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)['status'], 'INVALID_INPUT')


if __name__ == '__main__':
    unittest.main()
