"""Synthetic authority records and fake backends; no GitHub/SSH/Runtime claims."""
import copy
import datetime as dt
import json
import unittest
from unittest.mock import patch

import test_node_firewall as original
from test_node_firewall import n, c, ROOT, FixedWeekday, cp1_producer_receipt


def lead_record(decision):
    return {"schema": "fds.icr-lead-decision.v1", "policy_commit": n.REVIEW_AUTHORITY_POLICY,
            "scope": "ICR-D-NET-01-01", "lead": "GWONUK", "decision": decision,
            "decision_url": "https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-1",
            "decided_at": n.now()}


def to_lead_authorization(value):
    value = copy.deepcopy(value)
    for key in ("independent_reviewer", "review_decision", "review_url"):
        value.pop(key)
    value.update(schema="fds.node-firewall-authorization.v3", lead_decision=lead_record("EXECUTE"),
                 source_review={"scope": "CODE", "source_commit": value["source_commit"],
                                "author": "GWONUK", "reviewer": "hayeounglee", "decision": "APPROVED",
                                "review_url": "https://github.com/321Team/infra-kubernetes/pull/17#pullrequestreview-1",
                                "reviewed_at": (dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=5)).isoformat()})
    return value


def to_lead_acceptance(value):
    value = copy.deepcopy(value)
    for key in ("independent_reviewer", "review_decision", "review_url", "reviewed_at"):
        value.pop(key)
    value.update(schema="fds.cp1-runtime-acceptance.v2", lead_decision=lead_record("ACCEPTED"))
    return value


class LeadAuthority(unittest.TestCase):
    def setUp(self):
        clock = patch.object(dt, "datetime", FixedWeekday)
        clock.start()
        self.addCleanup(clock.stop)
        self.proof = cp1_producer_receipt()
        self.receipt = c.validate_cp1_receipt(self.proof)
        self.acceptance = to_lead_acceptance(original.acceptance_for(self.proof, "f"*64))

    def check_acceptance(self, data):
        c.validate_cp1_acceptance(data, self.receipt, "f"*64)

    def authorization(self):
        backend = original.FakeBackend(original.MemoryJournal())
        inputs = {"hostname": backend.node, "source_commit": "a"*40, "source_sha256": "b"*64,
                  "policy_sha256": n.policy_hash(), "plan_sha256": n.plan_hash(backend.node),
                  "before_sha256": n.fingerprint(backend.state), "residual_metadata_sha256": "d"*64,
                  "cp1_state_sha256": "e"*64, "snapshot": backend.state}
        return to_lead_authorization(original.authorization_for(inputs, "f"*64))

    def check_auth(self, data):
        n.validate_authorization_record(data, "a"*40, "k8s-worker01", "apply")

    def test_lead_can_accept_own_runtime_with_complete_proof(self):
        self.check_acceptance(self.acceptance)
        self.assertNotIn("independent_reviewer", self.acceptance)

    def test_historical_independent_acceptance_still_works(self):
        self.check_acceptance(original.acceptance_for(self.proof, "f"*64))

    def test_code_approval_can_be_reused_with_new_lead_execution(self):
        auth = self.authorization()
        self.check_auth(auth)
        auth["lead_decision"]["decision_url"] = "https://github.com/321Team/infra-kubernetes/pull/17#issuecomment-2"
        self.check_auth(auth)

    def test_missing_self_foreign_or_hold_source_review_stops(self):
        changes = [lambda r: r.pop("source_review"),
                   lambda r: r["source_review"].update(reviewer="GWONUK"),
                   lambda r: r["source_review"].update(source_commit="b"*40),
                   lambda r: r["source_review"].update(decision="CHANGES_REQUESTED"),
                   lambda r: r["source_review"].update(scope="RUNTIME"),
                   lambda r: r["source_review"].update(review_url="https://github.com/321Team/infra-kubernetes/pull/17#issuecomment-1")]
        for change in changes:
            auth = self.authorization(); change(auth)
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError, TypeError)):
                self.check_auth(auth)

    def test_source_review_after_lead_decision_stops(self):
        auth = self.authorization()
        auth["lead_decision"]["decided_at"] = (dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=6)).isoformat()
        with self.assertRaises(ValueError): self.check_auth(auth)

    def test_wrong_lead_scope_policy_or_decision_stops(self):
        for key, value in (("lead", "hayeounglee"), ("scope", "P1-K8S-01"), ("policy_commit", "a"*40),
                           ("decision", "APPROVED"), ("decision_url", "https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-1")):
            data = copy.deepcopy(self.acceptance); data["lead_decision"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.check_acceptance(data)

    def test_missing_future_naive_or_predated_decision_stops(self):
        for stamp in (None, "2026-09-10T12:00:00", "2026-09-10T12:00:01+00:00", "2026-09-10T11:59:59+00:00"):
            data = copy.deepcopy(self.acceptance); data["lead_decision"]["decided_at"] = stamp
            with self.subTest(stamp=stamp), self.assertRaises((ValueError, TypeError)): self.check_acceptance(data)

    def test_mixed_authority_formats_stop(self):
        auth = self.authorization(); auth["independent_reviewer"] = "hayeounglee"
        with self.assertRaises(ValueError): self.check_auth(auth)
        data = copy.deepcopy(self.acceptance); data["review_decision"] = "APPROVED"
        with self.assertRaises(ValueError): self.check_acceptance(data)

    def test_bad_receipt_source_run_or_input_is_not_accepted(self):
        for key in ("source_commit", "source_sha256", "plan_sha256", "run_id", "receipt_sha256", "receipt_input_sha256"):
            data = copy.deepcopy(self.acceptance); data[key] = "wrong"
            with self.subTest(key=key), self.assertRaises(ValueError): self.check_acceptance(data)

    def test_lead_does_not_bypass_required_tests_or_hashes(self):
        for category in ("positive", "negative", "regression"):
            for change in ({"status": "NOT_VERIFIED"}, {"evidence_sha256": None}):
                data = copy.deepcopy(self.acceptance); data["checks"][category].update(change)
                with self.subTest(category=category, change=change), self.assertRaises(ValueError):
                    self.check_acceptance(data)

    def test_lead_does_not_bypass_scope_window_or_protection(self):
        changes = [("backup_verified", False), ("console_recovery_available", False), ("maintenance_freeze", False),
                   ("protection_reviewed", False), ("runtime_only_acknowledged", False), ("rollback_runbook_reviewed", False),
                   ("target", "k8s-control01"), ("mode", "promote-permanent"), ("policy_sha256", "f"*64),
                   ("window_end", "2026-09-10T11:59:59+00:00")]
        for key, value in changes:
            auth = self.authorization(); auth[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.check_auth(auth)

    def test_unfilled_new_templates_fail_closed(self):
        auth = json.loads((ROOT/'policies/icr-d-net-01-01/node-firewall-lead-authorization.template.json').read_bytes())
        data = json.loads((ROOT/'policies/icr-d-net-01-01/cp1-lead-acceptance.template.json').read_bytes())
        with self.assertRaises((ValueError, KeyError, TypeError)): self.check_auth(auth)
        with self.assertRaises((ValueError, KeyError, TypeError)): self.check_acceptance(data)

    def exercise_controller(self, **kwargs):
        old_auth, old_accept = original.authorization_for, original.acceptance_for
        with patch.object(original, "authorization_for", side_effect=lambda *a, **k: to_lead_authorization(old_auth(*a, **k))), \
                patch.object(original, "acceptance_for", side_effect=lambda *a, **k: to_lead_acceptance(old_accept(*a, **k))):
            original.ControllerWorkflow().exercise(**kwargs)

    def test_v3_controller_applies_with_lead_acceptance(self):
        self.exercise_controller()

    def test_v3_controller_bad_evidence_stops_before_mutation(self):
        self.exercise_controller(bad_acceptance=True)

    def test_v3_controller_keeps_partial_failure_handling(self):
        self.exercise_controller(fail=True)

    def test_v3_controller_keeps_rollback(self):
        self.exercise_controller(rollback=True)


if __name__ == "__main__":
    unittest.main()
