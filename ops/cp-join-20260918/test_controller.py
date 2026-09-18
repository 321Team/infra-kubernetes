import argparse
import base64
import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


c = module('controller_template')
h = module('cp1_join_handoff')
c.CONTRACT = json.loads((ROOT / 'CONTRACT.json').read_text())


def topology(names):
    return {'nodes': [{'name': n, 'uid': '75735bd5-937a-463f-a93f-a8e7e51fa7a1' if n == 'k8s-control01' else 'new-' + n,
                       'ready': True, 'control_plane': True} for n in names],
            'members': [{'name': n, 'id': '1755679419291778130' if n == 'k8s-control01' else '123',
                         'peer_urls': ['https://' + c.REMOTE[n] + ':2380'], 'learner': False} for n in names],
            'endpoint_health': [{'health': True} for n in names], 'api_ready': True}


class ControllerTests(unittest.TestCase):
    def test_review_attestation_matches_handoff_api(self):
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        review = {'user': {'login': 'hayeounglee', 'id': 95111439}, 'id': 7, 'state': 'APPROVED',
                  'commit_id': 'a' * 40, 'submitted_at': timestamp,
                  'html_url': 'https://github.com/321Team/infra-kubernetes/pull/16#pullrequestreview-7'}
        replies = [{'head': {'sha': 'a' * 40}, 'state': 'open', 'draft': False}, [review],
                   {'content': base64.b64encode(Path(c.__file__).read_bytes()).decode()}]
        with patch.object(c, 'gh', side_effect=replies):
            authority = c.verify_review(16, 'a' * 40)
        h.validate_authority(authority)
        h.validate_request(dict(c.OLD_PINS, mode='transfer', job_id=c.OLD_JOB))

    def test_draft_has_no_execution_permission(self):
        with patch.object(c, 'gh', return_value={'head': {'sha': 'a' * 40}, 'state': 'open', 'draft': True}):
            with self.assertRaises(ValueError): c.verify_review(16, 'a' * 40)

    def test_changed_source_or_denied_review_rejected(self):
        with patch.object(c, 'gh', return_value={'head': {'sha': 'b' * 40}, 'state': 'open', 'draft': False}):
            with self.assertRaises(ValueError): c.verify_review(16, 'a' * 40)
        review = {'user': {'login': 'hayeounglee', 'id': 95111439}, 'id': 8, 'state': 'CHANGES_REQUESTED', 'commit_id': 'a' * 40}
        with patch.object(c, 'gh', side_effect=[{'head': {'sha': 'a' * 40}, 'state': 'open', 'draft': False}, [review]]):
            with self.assertRaises(ValueError): c.verify_review(16, 'a' * 40)

    def test_learner_wrong_peer_or_replaced_cp1_rejected(self):
        for mutation in ('learner', 'peer', 'uid'):
            view = topology(['k8s-control01', 'k8s-control02'])
            if mutation == 'learner': view['members'][1]['learner'] = True
            if mutation == 'peer': view['members'][1]['peer_urls'] = ['https://other:2380']
            if mutation == 'uid': view['nodes'][0]['uid'] = 'replacement'
            with self.assertRaises(ValueError): c.topology_gate(view, ['k8s-control01', 'k8s-control02'])

    def exercise(self, failure=None, cleanup_failure=False):
        calls = []
        names = ['k8s-control01']
        token = 'abcdef.0123456789abcdef'; key = 'd' * 64
        def remote(node, helper, request, timeout=180):
            mode = request['mode']; calls.append((node, helper, mode))
            if helper == 'cp_join_cluster.py':
                if mode == 'inspect': return {'configuration': {}, 'topology': topology(names)}
                if mode == 'topology': return topology(names)
                if mode == 'credentials': return {'token': token, 'certificate_key': key,
                    'endpoint': '10.1.93.109:6443', 'ca_spki_sha256': 'e' * 64}
                if mode == 'revoke':
                    if cleanup_failure: raise ValueError('cleanup failed')
                    return {'status': 'REVOKED'}
            if helper == 'cp1_join_handoff.py':
                return {'join_may_start': failure != 'transfer'}
            if mode == 'inspect': return {'status': 'PREPARE_CHECKS_PASS'}
            if mode == 'status': return {'status': 'FAILED_RECOVERY_REQUIRED', 'journal': {'state': 'FAILED_RECOVERY_REQUIRED'}}
            if mode == 'prepare': return {'status': 'FAILED_RECOVERY_REQUIRED' if failure == 'prepare' else 'PREPARED'}
            if mode == 'join':
                if failure == 'join': return {'status': 'FAILED_RECOVERY_REQUIRED', 'join_result': {'stderr': '[REDACTED]'}}
                names.append(node)
                return {'status': 'JOIN_COMMAND_SUCCEEDED_CLUSTER_VERIFICATION_REQUIRED'}
            raise AssertionError((node, helper, request))
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(execute=True, review_pr=16, source_commit='a' * 40)
            with patch.object(c, 'remote', side_effect=remote), patch.object(c, 'verify_review', return_value={}), \
                 patch.object(Path, 'is_file', return_value=True), contextlib.redirect_stdout(io.StringIO()):
                try: c.run(args, Path(tmp))
                except SystemExit as error: self.assertEqual(error.code, 2)
            directory = next(p for p in Path(tmp).iterdir() if p.is_dir())
            result = json.loads((directory / 'RESULT.json').read_text())
            for file in directory.glob('*.json'):
                self.assertNotIn(token, file.read_text()); self.assertNotIn(key, file.read_text())
            self.assertEqual(len(list(Path(tmp).glob('*.tar.gz'))), 1)
            return result, calls, [p.name for p in directory.iterdir()]

    def test_success_sequential_and_credentials_excluded(self):
        result, calls, files = self.exercise()
        self.assertEqual(result['status'], 'THREE_CP_READY_RUNTIME_RETAINED_REVIEW_REQUIRED')
        joins = [n for n, helper, mode in calls if mode == 'join']
        self.assertEqual(joins, ['k8s-control02', 'k8s-control03'])
        self.assertIn('CREDENTIAL_REVOCATION.json', files)

    def test_uncommitted_transfer_never_prepares_or_joins(self):
        result, calls, files = self.exercise('transfer')
        self.assertFalse(result['cp1_transferred'])
        self.assertFalse(any(mode in ('prepare', 'join', 'credentials') for n, helper, mode in calls))

    def test_prepare_failure_never_joins(self):
        result, calls, files = self.exercise('prepare')
        self.assertFalse(any(mode == 'join' for n, helper, mode in calls))
        self.assertEqual(result['current_node'], 'k8s-control02')
        self.assertIn('FAILURE_NODE.json', files)

    def test_cp2_failure_does_not_touch_cp3_or_reset(self):
        result, calls, files = self.exercise('join')
        self.assertFalse(any(n == 'k8s-control03' and mode in ('prepare', 'join') for n, helper, mode in calls))
        self.assertFalse(any(mode in ('reset', 'remove', 'recover') for n, helper, mode in calls))
        self.assertFalse(result['recovery_complete'])

    def test_cleanup_failure_not_complete_success(self):
        result, calls, files = self.exercise(cleanup_failure=True)
        self.assertEqual(result['status'], 'THREE_CP_READY_CREDENTIAL_CLEANUP_REQUIRED')


if __name__ == '__main__': unittest.main()
