"""Focused regression tests for a one-shot resume of the submitted CP1 session.

No VM commands run. Happy-path evidence is the original submitted archive;
negative cases alter one authoritative boundary at a time.
"""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
ARCHIVE = WORKSPACE / 'upload/ICR_CP_Session_20260918T010834Z_72393be0.partial.tar.gz'
EXTRACTED = WORKSPACE / 'cp-session-failure-20260918/ICR_CP_Session_20260918T010834Z_72393be0'
CLOCK = dt.datetime(2026, 9, 18, 2, 0, tzinfo=dt.timezone.utc)
COMMIT = '06286f31735a80982e50e5f94fd0e1f4a027eb8d'
REVIEW_ID = 5243182569
JOB = 'c564ae97530dfe80'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = load('resume_under_test', ROOT / 'tools/RUN_CP_RESUME.py')
core = load('resume_core_fixture', ROOT / 'resume-kit/source/scripts/run-cp1-minimal-change.py').m


def fixture_files():
    return {str(p.relative_to(EXTRACTED)): p.read_bytes()
            for p in EXTRACTED.rglob('*') if p.is_file()}


def replace_json(files, name, update):
    data = json.loads(files[name])
    update(data)
    files[name] = (json.dumps(data, sort_keys=True, indent=2) + '\n').encode()


def authorities():
    repo = {'full_name': '321Team/infra-kubernetes'}
    pr = {'number': 15, 'state': 'open',
          'url': 'https://api.github.com/repos/321Team/infra-kubernetes/pulls/15',
          'html_url': 'https://github.com/321Team/infra-kubernetes/pull/15',
          'head': {'sha': COMMIT, 'repo': copy.deepcopy(repo)},
          'base': {'repo': copy.deepcopy(repo)}}
    review = {'id': REVIEW_ID, 'state': 'APPROVED', 'commit_id': COMMIT,
              'user': {'login': 'hayeounglee', 'id': 95111439},
              'submitted_at': '2026-09-18T01:22:52Z', 'body': '',
              'html_url': 'https://github.com/321Team/infra-kubernetes/pull/15#pullrequestreview-' + str(REVIEW_ID),
              'pull_request_url': 'https://api.github.com/repos/321Team/infra-kubernetes/pulls/15'}
    return pr, [review]


class ReviewAuthorityTests(unittest.TestCase):
    def test_empty_body_exact_source_review_is_sufficient(self):
        pr, reviews = authorities()
        proof = entry.verify_fix_review(pr, reviews, CLOCK)
        self.assertTrue(proof)
        self.assertEqual(reviews[0]['body'], '')

    def test_new_pr_head_invalidates_old_review(self):
        pr, reviews = authorities()
        pr['head']['sha'] = 'a' * 40
        with self.assertRaises((ValueError, RuntimeError)):
            entry.verify_fix_review(pr, reviews, CLOCK)

    def test_same_pr_number_in_wrong_repository_rejected(self):
        pr, reviews = authorities()
        pr['base']['repo']['full_name'] = 'another/infra-kubernetes'
        with self.assertRaises((ValueError, RuntimeError)):
            entry.verify_fix_review(pr, reviews, CLOCK)

    def test_wrong_person_cannot_supply_fix_approval(self):
        pr, reviews = authorities()
        reviews[0]['user'] = {'login': 'GWONUK', 'id': 277643063}
        with self.assertRaises((ValueError, RuntimeError)):
            entry.verify_fix_review(pr, reviews, CLOCK)

    def test_later_changes_requested_revokes_approval(self):
        pr, reviews = authorities()
        later = copy.deepcopy(reviews[0])
        later.update(id=REVIEW_ID + 1, state='CHANGES_REQUESTED', submitted_at='2026-09-18T01:40:00Z')
        reviews.append(later)
        with self.assertRaises((ValueError, RuntimeError)):
            entry.verify_fix_review(pr, reviews, CLOCK)

    def test_dismissed_approval_is_not_a_valid_source_decision(self):
        pr, reviews = authorities()
        reviews[0]['state'] = 'DISMISSED'
        with self.assertRaises((ValueError, RuntimeError)):
            entry.verify_fix_review(pr, reviews, CLOCK)

    def test_review_url_for_different_pr_rejected(self):
        pr, reviews = authorities()
        reviews[0]['html_url'] = reviews[0]['html_url'].replace('/15#', '/14#')
        with self.assertRaises((ValueError, RuntimeError)):
            entry.verify_fix_review(pr, reviews, CLOCK)

    def test_review_api_record_for_different_pr_rejected(self):
        pr, reviews = authorities()
        reviews[0]['pull_request_url'] = reviews[0]['pull_request_url'].replace('/15', '/14')
        with self.assertRaises((ValueError, RuntimeError)):
            entry.verify_fix_review(pr, reviews, CLOCK)

    def test_future_review_cannot_authorize_current_run(self):
        pr, reviews = authorities()
        reviews[0]['submitted_at'] = '2026-09-18T03:00:00Z'
        with self.assertRaises((ValueError, RuntimeError)):
            entry.verify_fix_review(pr, reviews, CLOCK)

    def test_latest_approval_of_other_commit_not_inherited(self):
        pr, reviews = authorities()
        later = copy.deepcopy(reviews[0])
        later.update(id=REVIEW_ID + 1, commit_id='b' * 40, submitted_at='2026-09-18T01:40:00Z')
        reviews.append(later)
        with self.assertRaises((ValueError, RuntimeError)):
            entry.verify_fix_review(pr, reviews, CLOCK)


class SubmittedEvidenceTests(unittest.TestCase):
    def test_actual_pinned_archive_loads(self):
        files = entry.read_evidence(ARCHIVE)
        self.assertEqual(files['SESSION_DECISION.json'], fixture_files()['SESSION_DECISION.json'])
        self.assertIn('CP2_APPLY/RESULT.json', files)

    def test_altered_archive_rejected_before_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ARCHIVE.name
            path.write_bytes(ARCHIVE.read_bytes() + b'not-submitted')
            with self.assertRaises((ValueError, RuntimeError)):
                entry.read_evidence(path)

    def test_original_lower_level_no_mutation_proof_accepted(self):
        entry.check_prior_failure(fixture_files())

    def test_uncertain_runtime_outcome_cannot_be_replayed(self):
        files = fixture_files()
        replace_json(files, 'CP2_APPLY/RESULT.json',
                     lambda x: x.update(runtime_applied='attempted_requires_receipt_review'))
        with self.assertRaises((ValueError, RuntimeError)):
            entry.check_prior_failure(files)

    def test_permanent_mutation_disqualifies_narrow_resume(self):
        files = fixture_files()
        replace_json(files, 'CP2_APPLY/RESULT.json', lambda x: x.update(permanent_applied='applied'))
        with self.assertRaises((ValueError, RuntimeError)):
            entry.check_prior_failure(files)

    def test_request_written_at_mutation_boundary_disqualifies_resume(self):
        files = fixture_files()
        files['CP2_APPLY/REQUEST.json'] = b'{}'
        with self.assertRaises((ValueError, RuntimeError)):
            entry.check_prior_failure(files)

    def test_target_response_disqualifies_no_mutation_assumption(self):
        files = fixture_files()
        files['CP2_APPLY/TARGET.json'] = b'{}'
        with self.assertRaises((ValueError, RuntimeError)):
            entry.check_prior_failure(files)

    def test_prior_cp3_execution_disqualifies_cp2_first_resume(self):
        files = fixture_files()
        files['CP3_APPLY/RESULT.json'] = b'{}'
        with self.assertRaises((ValueError, RuntimeError)):
            entry.check_prior_failure(files)

    def test_different_cp1_job_disqualifies_pinned_resume(self):
        files = fixture_files()
        replace_json(files, 'RUN_STATE.json', lambda x: x.update(job_id='a' * 16))
        with self.assertRaises((ValueError, RuntimeError)):
            entry.check_prior_failure(files)

    def test_uncommitted_session_cannot_be_reused(self):
        files = fixture_files()
        replace_json(files, 'RUN_STATE.json', lambda x: x.update(cp1_session_committed=False))
        with self.assertRaises((ValueError, RuntimeError)):
            entry.check_prior_failure(files)

    def test_real_trial_handoff_decision_and_regression_validate(self):
        entry.validate_prior(fixture_files(), core, CLOCK)

    def test_altered_regression_bytes_rejected(self):
        files = fixture_files()
        files['HOST_EXTERNAL_REGRESSION.stdout'] += b'UNVERIFIED=PASS\n'
        with self.assertRaises((ValueError, RuntimeError)):
            entry.validate_prior(files, core, CLOCK)

    def test_extending_original_decision_deadline_rejected(self):
        files = fixture_files()
        replace_json(files, 'SESSION_DECISION.json',
                     lambda x: x.update(expires_at='2026-09-18T17:00:00+09:00'))
        with self.assertRaises((ValueError, RuntimeError)):
            entry.validate_prior(files, core, CLOCK)

    def test_join_authority_cannot_be_added_to_continuation(self):
        files = fixture_files()
        replace_json(files, 'SESSION_DECISION.json', lambda x: x.update(join_authorized=True))
        with self.assertRaises((ValueError, RuntimeError)):
            entry.validate_prior(files, core, CLOCK)

    def test_retired_work_window_cannot_be_resumed(self):
        after_withdrawal = dt.datetime(2026, 9, 18, 6, 16, tzinfo=dt.timezone.utc)
        with self.assertRaises((ValueError, RuntimeError)):
            entry.validate_prior(fixture_files(), core, after_withdrawal)


class SingleAttemptTests(unittest.TestCase):
    def test_second_attempt_fails_and_preserves_original_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'resume-attempt.json'
            entry.claim_attempt(path, {'job': JOB, 'evidence': 'first'})
            original = path.read_bytes()
            with self.assertRaises((ValueError, RuntimeError, FileExistsError)):
                entry.claim_attempt(path, {'job': JOB, 'evidence': 'second'})
            self.assertEqual(path.read_bytes(), original)

    def test_resume_dispatch_does_not_reapply_cp1_or_recommit_session(self):
        wrapper = types.SimpleNamespace(followup_nodes=mock.Mock(), continuation=mock.Mock())
        work = types.SimpleNamespace(run=mock.Mock(), save=mock.Mock(), state={})
        handoff, decision = {'job': JOB}, {'decision': 'existing'}
        entry.run_followups(wrapper, work, handoff, decision)
        wrapper.followup_nodes.assert_called_once_with(work, handoff, decision)
        work.run.assert_not_called()
        wrapper.continuation.assert_not_called()

    def test_followup_failure_does_not_auto_retry_any_workflow(self):
        wrapper = types.SimpleNamespace(followup_nodes=mock.Mock(side_effect=RuntimeError('CP2 failed')),
                                        continuation=mock.Mock())
        work = types.SimpleNamespace(run=mock.Mock(), save=mock.Mock(), state={})
        with self.assertRaisesRegex(RuntimeError, 'CP2 failed'):
            entry.run_followups(wrapper, work, {}, {})
        self.assertEqual(wrapper.followup_nodes.call_count, 1)
        work.run.assert_not_called()
        wrapper.continuation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
