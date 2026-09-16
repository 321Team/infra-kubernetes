"""Synthetic transition records; no real CP1, systemd or packet tests."""
import copy
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_node_firewall as original_tests
from test_node_firewall import load, cp1_producer_receipt, n, c

h = load("handoff_test", "scripts/check-cp1-cni-handoff.py")
ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 9, 17, 1, 0, tzinfo=dt.timezone.utc)


def fixture():
    class FullFixture(original_tests.FakeBackend):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.state["active_policies"] = {"configurations": {"runtime": {}, "permanent": {}}, "active_output": ""}
    with patch.object(original_tests, "FakeBackend", FullFixture):
        original = cp1_producer_receipt()
    receipt0 = c.validate_cp1_receipt(original)
    # These bytes stand in for one supplied original file, distinct from receipt SHA.
    original_hash = h.fingerprint(original)
    fw = copy.deepcopy(receipt0["after"])
    # The real pinned producer emitted this state from the synthetic backend.
    before = {"firewall": fw, "protection": {"calico": {"uid": "fixture", "ready": True}},
              "zone_inventory": {p: {"public": copy.deepcopy(fw["planes"][p])} for p in ("runtime", "permanent")}}
    changes = [
        {"path": ["zone_inventory", "runtime", h.ZONE], "before_exists": False, "before": None,
         "after": {"target": "default", "interfaces": ["cali+"], "forward": False}},
        {"path": ["firewall", "active_policies", "configurations", "runtime", h.POLICY],
         "before_exists": False, "before": None,
         "after": {"target": "CONTINUE", "ingress": h.ZONE, "egress": h.ZONE}},
    ]
    contract = {"schema": "fds.cp1-cni-handoff-contract.v1", "scope": copy.deepcopy(h.SCOPE),
                "status": "REVIEWED_CONTRACT", "original_input_sha256": original_hash,
                "original_receipt_sha256": h.fingerprint(receipt0), "before": before, "changes": changes,
                "producer": {"source_commit": "a" * 40, "source_sha256": "b" * 64, "plan_sha256": "c" * 64},
                "review": {"reviewer": "hayeounglee", "decision": "APPROVED",
                           "url": "https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-1",
                           "reviewed_at": "2026-09-17T00:29:00+00:00"},
                "window": {"start": "2026-09-17T00:30:00+00:00", "end": "2026-09-17T02:30:00+00:00", "end_exclusive": True}}
    after = h.expected_after(before, changes)
    receipt = {"schema": "fds.cp1-cni-change-receipt.v1", "status": "ACTIVE_REVIEW_REQUIRED",
               "run_id": "20260917T003000Z-abcdef123456", "contract_sha256": h.fingerprint(contract),
               "producer": copy.deepcopy(contract["producer"]), "original_input_sha256": original_hash,
               "original_receipt_sha256": h.fingerprint(receipt0), "before": before, "after": after,
               "started_at": "2026-09-17T00:30:00+00:00", "ended_at": "2026-09-17T00:35:00+00:00",
               "boot_id": "12345678-1234-1234-1234-123456789abc", "operations": [
                   {"change": copy.deepcopy(change), "intent_at": "2026-09-17T00:31:00+00:00",
                    "completed_at": "2026-09-17T00:31:00+00:00", "status": "CHANGED",
                    "result": {"rc": 0, "stdout": "success", "stderr": "", "truncated": False}}
                   for change in changes]}
    evidence = {category: {"category": category, "kind": "ACTUAL_CP1_CALICO_RUNTIME", "status": "PASS",
                          "run_id": receipt["run_id"], "state_sha256": h.fingerprint(after),
                          "measured_at": "2026-09-17T00:36:00+00:00", "raw_archive_sha256": "d" * 64}
                for category in ("positive", "negative", "regression")}
    acceptance = {"decision": "ACCEPTED", "reviewer": "hayeounglee", "change_receipt_sha256": h.fingerprint(receipt),
                  "state_sha256": h.fingerprint(after), "reviewed_at": "2026-09-17T00:40:00+00:00",
                  "review_url": "https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-2",
                  "evidence_sha256": {key: h.fingerprint(value) for key, value in evidence.items()}}
    bundle = {"schema": "fds.cp1-cni-handoff-bundle.v1", "change_receipt": receipt,
              "acceptance": acceptance, "evidence": evidence}
    observation = {"schema": "fds.cp1-cni-handoff-observation.v1", "stage": "BEFORE_TARGET", "target": "k8s-control02",
                   "target_registered": False, "run_id": receipt["run_id"], "change_status": "ACTIVE_REVIEW_REQUIRED",
                   "observed_at": NOW.isoformat(), "boot_id": receipt["boot_id"], "stable": copy.deepcopy(after),
                   "state_sha256": h.fingerprint(after), "recovery": {"armed": True, "pending": False,
                       "run_id": receipt["run_id"], "deadline": contract["window"]["end"]}}
    return {"contract": contract, "original": original, "original_input_sha256": original_hash,
            "bundle": bundle, "observation": observation, "current": NOW}


class TransitionChecks(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def reject(self, change, match=None):
        data = copy.deepcopy(self.data)
        change(data)
        with self.assertRaisesRegex(ValueError, match or ".+"):
            h.validate(**data)

    def test_valid_synthetic_chain_does_not_authorize_runtime_or_join(self):
        original = copy.deepcopy(self.data)
        with patch("subprocess.run", side_effect=AssertionError("no external process")):
            result = h.validate(**self.data)
        self.assertEqual(result["status"], "CONSISTENT_REVIEW_REQUIRED")
        for key in ("authorizes_cp1_acceptance", "authorizes_cp2_change", "authorizes_join",
                    "production_consumer_connected", "runtime_independently_verified"):
            self.assertIs(result[key], False)
        self.assertEqual(self.data, original)

    def test_old_consumer_still_blocks_the_new_after(self):
        fw = self.data["observation"]["stable"]["firewall"]
        stable = {"firewall": fw, "protection": {"fixture": True}}
        with self.assertRaisesRegex(ValueError, "no longer present"):
            c.cp1_state_gate({"stable": stable, "sha256": n.fingerprint(stable)},
                             receipt=self.data["original"]["receipt"])

    def test_source_and_plan_mismatch_cannot_be_fixed_by_rehashing_receipt(self):
        for key in ("source_commit", "source_sha256", "plan_sha256"):
            def change(d):
                d["bundle"]["change_receipt"]["producer"][key] = "f" * (40 if key == "source_commit" else 64)
                d["bundle"]["acceptance"]["change_receipt_sha256"] = h.fingerprint(d["bundle"]["change_receipt"])
            with self.subTest(key=key):
                self.reject(change, "producer differs")

    def test_original_links_and_receipt_validation(self):
        for field in ("original_input_sha256", "original_receipt_sha256"):
            with self.subTest(field=field):
                self.reject(lambda d: d["contract"].update({field: "f" * 64}), "original receipt link")
        self.reject(lambda d: d["original"]["receipt"].update(source_commit="f" * 40), "CP1 source/plan")

    def test_missing_or_wrong_review_record(self):
        self.reject(lambda d: d["contract"].update(status="DRAFT"), "draft")
        self.reject(lambda d: d["contract"]["review"].update(reviewer="GWONUK"), "review absent")
        self.reject(lambda d: d["contract"]["review"].update(url="<https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-1>"), "URL invalid")

    def test_forbidden_and_duplicate_delta_paths(self):
        for path in (["firewall", "planes", "permanent"], ["protection"], ["firewall", "nm_uuid"],
                     ["zone_inventory", "runtime", "public"], ["zone_inventory", "runtime", "trusted"],
                     ["firewall", "active_policies", "configurations", "runtime", "foreign"]):
            with self.subTest(path=path):
                self.reject(lambda d: d["contract"]["changes"][0].update(path=path), "outside owned")
        self.reject(lambda d: d["contract"]["changes"].append(copy.deepcopy(d["contract"]["changes"][0])), "duplicate")

    def test_preexisting_object_is_not_claimed(self):
        before = copy.deepcopy(self.data["contract"]["before"])
        before["zone_inventory"]["runtime"][h.ZONE] = {"foreign": True}
        changes = copy.deepcopy(self.data["contract"]["changes"])
        changes[0].update(before_exists=True, before={"foreign": True})
        with self.assertRaisesRegex(ValueError, "not owned"):
            h.expected_after(before, changes)

    def test_original_before_and_after_edits_rejected(self):
        self.reject(lambda d: d["contract"]["before"]["firewall"]["planes"]["runtime"]["rules"].pop(), "original after")
        self.reject(lambda d: d["bundle"]["change_receipt"]["after"]["firewall"].update(nm_zone="trusted"), "replayed delta")

    def test_unknown_and_reordered_mutation_outcomes(self):
        for result in ({"rc": 124}, {"rc": False, "stdout": "success", "stderr": "", "truncated": False},
                       {"rc": 0, "stdout": "success", "stderr": "ALREADY_ENABLED", "truncated": False},
                       {"rc": 0, "stdout": "success", "stderr": "", "truncated": True}):
            with self.subTest(result=result):
                self.reject(lambda d: d["bundle"]["change_receipt"]["operations"][0].update(result=result), "uncertain")
        self.reject(lambda d: d["bundle"]["change_receipt"]["operations"].reverse(), "reordered")
        self.reject(lambda d: d["bundle"]["change_receipt"]["operations"].pop(), "incomplete")

    def test_operation_intent_cannot_postdate_completion(self):
        self.reject(lambda d: d["bundle"]["change_receipt"]["operations"][0].update(intent_at="2026-09-17T00:34:00+00:00"), "ordering")

    def test_acceptance_is_linked_to_change_and_after(self):
        for key in ("change_receipt_sha256", "state_sha256"):
            with self.subTest(key=key):
                self.reject(lambda d: d["bundle"]["acceptance"].update({key: "e" * 64}), "subject differs")
        self.reject(lambda d: d["bundle"]["acceptance"].update(decision="NOT_VERIFIED"), "absent")

    def test_synthetic_failed_and_rehashed_foreign_evidence_rejected(self):
        for key, value in (("kind", "SYNTHETIC_NETNS"), ("status", "FAIL"), ("run_id", "other"),
                           ("state_sha256", "f" * 64), ("raw_archive_sha256", None)):
            def change(d):
                e = d["bundle"]["evidence"]["positive"]
                e[key] = value
                d["bundle"]["acceptance"]["evidence_sha256"]["positive"] = h.fingerprint(e)
            with self.subTest(key=key):
                self.reject(change)

    def test_missing_category_or_evidence_hash_rejected(self):
        self.reject(lambda d: d["bundle"]["evidence"].pop("negative"), "incomplete")
        self.reject(lambda d: d["bundle"]["acceptance"]["evidence_sha256"].update(regression="f" * 64), "checksum")

    def test_drift_rechecked_before_and_after_target(self):
        for stage in ("BEFORE_TARGET", "AFTER_TARGET"):
            for component in ("protection", "zone_inventory", "firewall"):
                def change(d):
                    d["observation"]["stage"] = stage
                    d["observation"]["stable"][component]["unexpected"] = True
                    d["observation"]["state_sha256"] = h.fingerprint(d["observation"]["stable"])
                with self.subTest(stage=stage, component=component):
                    self.reject(change, "drift")

    def test_end_is_exclusive_and_rollback_reserve_applies(self):
        self.reject(lambda d: d.update(current=h.instant(d["contract"]["window"]["end"])), "outside reviewed window")
        def insufficient(d):
            d["current"] = h.instant(d["contract"]["window"]["end"]) - dt.timedelta(seconds=659)
            d["observation"]["observed_at"] = d["current"].isoformat()
        self.reject(insufficient, "insufficient")
        data = copy.deepcopy(self.data)
        insufficient(data)
        data["observation"]["stage"] = "AFTER_TARGET"
        self.assertEqual(h.validate(**data)["stage"], "AFTER_TARGET")
        data["current"] += dt.timedelta(seconds=480)
        data["observation"]["observed_at"] = data["current"].isoformat()
        with self.assertRaisesRegex(ValueError, "insufficient"):
            h.validate(**data)

    def test_stale_and_future_observations_rejected(self):
        for seconds in (-901, 1):
            with self.subTest(seconds=seconds):
                self.reject(lambda d: d["observation"].update(observed_at=(NOW + dt.timedelta(seconds=seconds)).isoformat()), "stale/future")

    def test_recovery_and_boot_lifecycle(self):
        for change in (lambda d: d["observation"].update(boot_id="00000000-0000-0000-0000-000000000000"),
                       lambda d: d["observation"].update(change_status="ROLLED_BACK"),
                       lambda d: d["observation"]["recovery"].update(armed=False),
                       lambda d: d["observation"]["recovery"].update(pending=True),
                       lambda d: d["observation"]["recovery"].update(run_id="other"),
                       lambda d: d["observation"]["recovery"].update(deadline="2026-09-17T02:31:00+00:00")):
            with self.subTest(change=change):
                self.reject(change)

    def test_joined_target_and_worker_scope_rejected(self):
        self.reject(lambda d: d["observation"].update(target_registered=True), "pre-join")
        self.reject(lambda d: d["observation"].update(target="k8s-worker01"), "pre-join")

    def test_scope_does_not_expand_to_cluster_or_other_protocol_contract(self):
        for key, value in (("source_cidr", "10.244.0.0/16"), ("egress", "ens160"),
                           ("layer4", "TCP8080"), ("same_node_only", 1)):
            with self.subTest(key=key):
                self.reject(lambda d: d["contract"]["scope"].update({key: value}), "scope differs")

    def test_dependency_drift_stops_original_validator_import(self):
        with patch.object(h, "sha", return_value="f" * 64), self.assertRaisesRegex(ValueError, "validator changed"):
            h.validate(**self.data)


class FileInterface(unittest.TestCase):
    def test_distributed_contract_is_an_unapproved_draft(self):
        template = json.loads((ROOT / "policies/icr-d-net-01-01/cp1-cni-handoff-contract.template.json").read_text())
        self.assertEqual(template["scope"], h.SCOPE)
        self.assertEqual(template["status"], "DRAFT_NOT_EXECUTABLE")
        self.assertFalse(template["changes"])
        self.assertTrue(all(value is None for value in template["producer"].values()))
        self.assertTrue(all(value is None for value in template["review"].values()))
        data = fixture()
        data["contract"] = template
        with self.assertRaisesRegex(ValueError, "still draft"):
            h.validate(**data)

    def test_raw_byte_digest_duplicate_keys_and_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
                path.write_bytes(raw)
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    h.load_checked(path, h.sha(raw))
            path.write_bytes(b'{"a":1}')
            with self.assertRaisesRegex(ValueError, "supplied SHA256"):
                h.load_checked(path, "f" * 64)

    def test_cli_bad_input_returns_nonzero_and_no_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text('{}')
            args = []
            for name in ("contract", "original", "bundle", "observation"):
                args += ["--" + name, str(path), "--" + name + "-sha256", h.sha(path.read_bytes())]
            result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/check-cp1-cni-handoff.py"), *args],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertFalse(report["authorizes_cp2_change"])
            self.assertFalse(report["authorizes_join"])


if __name__ == "__main__":
    unittest.main()
