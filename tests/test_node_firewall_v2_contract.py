"""Failure-oriented v2 contract checks. All producer execution uses fake backends."""
import contextlib
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_node_firewall as original_tests
from test_node_firewall import (n, c, ROOT, FakeBackend, MemoryJournal,
                                cp1_producer_receipt, acceptance_for)


class ReceiptContract(unittest.TestCase):
    def acceptance_template(self):
        return json.loads((ROOT / "policies/icr-d-net-01-01/cp1-runtime-acceptance.template.json").read_bytes())

    def test_distributed_acceptance_template_matches_pinned_producer(self):
        template = self.acceptance_template()
        self.assertEqual({key: template[key] for key in n.CP1_CONTRACT}, n.CP1_CONTRACT)

    def test_distributed_acceptance_template_is_not_an_approval(self):
        template = self.acceptance_template()
        for key in ("decision", "independent_reviewer", "receipt_input_sha256", "receipt_sha256",
                    "review_decision", "review_url", "reviewed_at", "run_id"):
            self.assertIsNone(template[key], key)
        self.assertEqual(template["checks"], {key: {"status": "NOT_VERIFIED", "evidence_sha256": None}
                                            for key in ("positive", "negative", "regression")})
        proof = cp1_producer_receipt()
        with self.assertRaisesRegex(ValueError, "CP1 Runtime acceptance absent"):
            c.validate_cp1_acceptance(template, proof["receipt"], "f" * 64)

    def test_completed_distributed_template_accepts_real_producer_with_mock_evidence(self):
        proof = cp1_producer_receipt()
        receipt = c.validate_cp1_receipt(proof)
        template = self.acceptance_template()
        # Fill only onsite/review fields; never overwrite the distributed source/plan pins.
        completed = acceptance_for(proof, "f" * 64)
        template.update({key: value for key, value in completed.items() if key not in n.CP1_CONTRACT})
        c.validate_cp1_acceptance(template, receipt, "f" * 64)
        stale = {
            "source_commit": "f0388b1a372d19b5079e94ec6949ef443d774d2f",
            "source_sha256": "0454a238d7451c86362dd98a54d3ed15cbf7f5d78c921cd6b93447581fc40ac2",
        }
        for key, value in stale.items():
            with self.subTest(stale_field=key), self.assertRaisesRegex(ValueError, "source/plan differs"):
                c.validate_cp1_acceptance(dict(template, **{key: value}), receipt, "f" * 64)

    def test_producer_lock_matches_current_contract_and_integrated_source(self):
        lock = json.loads((ROOT / "tests/fixtures/PR2_PRODUCER_LOCK.json").read_bytes())
        self.assertEqual(lock["commit"], n.CP1_CONTRACT["source_commit"])
        self.assertEqual(lock["sha256"], n.CP1_CONTRACT["source_sha256"])
        self.assertEqual(lock["path"], "scripts/cp-canary-node.py")
        self.assertEqual(c.digest(ROOT / lock["path"]), lock["sha256"])

    def test_integrated_producer_drift_stops_before_producer_execution(self):
        with patch.object(c, "digest", return_value="f" * 64), \
                patch.object(original_tests, "load") as loader:
            with self.assertRaisesRegex(AssertionError, "integrated PR2 producer"):
                cp1_producer_receipt()
            loader.assert_not_called()

    def test_rehashed_missing_invalid_or_outside_window_is_rejected(self):
        original = cp1_producer_receipt()
        changes = {
            "missing": lambda r: r.pop("authorization_window"),
            "inclusive_end": lambda r: r["authorization_window"].update(end_exclusive=False),
            "naive_start": lambda r: r["authorization_window"].update(start="2026-09-10T11:59:00"),
            "empty_window": lambda r: r["authorization_window"].update(end=r["authorization_window"]["start"]),
            "unbounded": lambda r: r["authorization_window"].update(end="2026-09-10T23:00:00+00:00"),
            "before_window": lambda r: r.update(started_at="2026-09-10T11:00:00+00:00"),
            "operation_at_end": lambda r: r["operations"][0].update(started_at=r["authorization_window"]["end"]),
            "operation_before_start": lambda r: r["operations"][0].update(started_at="2026-09-10T11:00:00+00:00"),
        }
        for name, change in changes.items():
            proof = copy.deepcopy(original)
            change(proof["receipt"])
            proof["receipt_sha256"] = n.fingerprint(proof["receipt"])
            with self.subTest(name=name), self.assertRaises(ValueError):
                c.validate_cp1_receipt(proof)

    def test_pinned_producer_generates_accepted_v2_for_added_and_preserved_rules(self):
        for preserved in (False, True):
            proof = cp1_producer_receipt(preserved)
            receipt = c.validate_cp1_receipt(proof)
            self.assertEqual(len(receipt["operations"]), 6)
            self.assertEqual(receipt["operations"][0]["status"], "preserved" if preserved else "added")
            c.validate_cp1_acceptance(acceptance_for(proof, "f" * 64), receipt, "f" * 64)

    def test_rehashed_legacy_foreign_duplicated_or_uncertain_receipts_are_rejected(self):
        original = cp1_producer_receipt()
        changes = {
            "legacy": lambda r: r.update(schema="fds.cp1-canary-receipt.v1"),
            "old_source": lambda r: r.update(source_commit="097ddb56d87b229a9eb465f72ea9f55d16524c51"),
            "helper": lambda r: r.update(source_sha256="f" * 64),
            "reference_plan": lambda r: r.update(plan_sha256="f" * 64),
            "execution_policy": lambda r: r.update(execution_plan_sha256="f" * 64),
            "phase": lambda r: r.update(phase="permanent"),
            "reorder": lambda r: r["operations"].reverse(),
            "duplicate": lambda r: r["operations"].__setitem__(1, copy.deepcopy(r["operations"][0])),
            "plane": lambda r: r["operations"][0].update(plane="permanent"),
            "broad": lambda r: r["operations"][0].update(rule=r["operations"][0]["rule"].replace("/32", "/24")),
            "ownership": lambda r: r["operations"][0].update(present_before=True),
            "bool_type": lambda r: r["operations"][0].update(present_before=0),
            "uncertain": lambda r: r["operations"][0].update(status="add_uncertain"),
            "timeout": lambda r: r["operations"][0]["result"].update(rc=124),
            "warning": lambda r: r["operations"][0]["result"].update(stderr="ALREADY_ENABLED"),
            "after": lambda r: r["after"]["planes"]["runtime"]["rules"].pop(),
            "permanent": lambda r: r["after"]["planes"]["permanent"]["rules"].append("unexpected"),
            "future": lambda r: r.update(ended_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                proof = copy.deepcopy(original)
                change(proof["receipt"])
                proof["receipt_sha256"] = n.fingerprint(proof["receipt"])
                with self.assertRaises(ValueError):
                    c.validate_cp1_receipt(proof)

    def test_acceptance_requires_this_receipt_tests_and_independent_review(self):
        proof = cp1_producer_receipt()
        original = acceptance_for(proof, "f" * 64)
        for key, value in (("source_commit", "b" * 40), ("receipt_sha256", "e" * 64),
                           ("receipt_input_sha256", "e" * 64), ("run_id", "other"),
                           ("review_url", "https://github.com/321Team/infra-kubernetes/pull/4#issuecomment-1"),
                           ("independent_reviewer", "GWONUK"), ("decision", "PENDING")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                c.validate_cp1_acceptance(dict(original, **{key: value}), proof["receipt"], "f" * 64)
        for category in ("positive", "negative", "regression"):
            for field, value in (("status", "NOT_VERIFIED"), ("evidence_sha256", None)):
                data = copy.deepcopy(original)
                data["checks"][category][field] = value
                with self.subTest(category=category, field=field), self.assertRaises(ValueError):
                    c.validate_cp1_acceptance(data, proof["receipt"], "f" * 64)

    def test_cp1_state_drift_or_lost_runtime_rules_blocks_expansion(self):
        proof = cp1_producer_receipt()
        stable = {"firewall": proof["receipt"]["after"], "protection": {"fixture": True}}
        state = {"stable": stable, "sha256": n.fingerprint(stable)}
        c.cp1_state_gate(state, state["sha256"], proof["receipt"])
        old = state["sha256"]
        changed = copy.deepcopy(state)
        changed["stable"]["firewall"]["planes"]["runtime"]["rules"].pop()
        changed["sha256"] = n.fingerprint(changed["stable"])
        with self.assertRaises(ValueError):
            c.cp1_state_gate(changed, old)
        with self.assertRaises(ValueError):
            c.cp1_state_gate(changed, receipt=proof["receipt"])


class CP1ProtectionProjection(unittest.TestCase):
    def state(self, version="1", restarts=0, ready=True):
        backend = n.BaseBackend("k8s-control01")
        backend.snapshot_queries = {}
        pod = {"metadata": {"name": "calico-fixture", "uid": "fixture-uid", "resourceVersion": version},
               "spec": {"nodeName": "k8s-control01"},
               "status": {"phase": "Running", "containerStatuses": [
                   {"name": "calico", "ready": ready, "restartCount": restarts, "imageID": "fixture-image"}]}}
        def query(argv):
            items = [pod] if "pods" in argv else []
            return {"rc": 0, "stdout": json.dumps({"items": items}), "stderr": ""}
        try:
            with patch.object(backend, "snapshot", return_value={"fixture": "firewall"}), \
                    patch.object(backend, "kubectl", side_effect=query):
                return backend.cp1_state()
        finally:
            backend.cache.cleanup()

    def test_volatile_resource_version_is_ignored_but_restart_changes_digest(self):
        first = self.state()
        self.assertEqual(first["sha256"], self.state(version="2")["sha256"])
        self.assertNotEqual(first["sha256"], self.state(restarts=1)["sha256"])
        self.assertEqual(len(first["queries"]), 5)

    def test_nonready_calico_stops_protection_inspection(self):
        with self.assertRaisesRegex(ValueError, "not ready"):
            self.state(ready=False)


class RuntimeSafety(unittest.TestCase):
    def test_policy_file_and_five_executable_plans_are_bound(self):
        data = json.loads(c.POLICY_PATH.read_bytes())
        n.validate_policy(data)
        self.assertEqual(len(data["targets"]), 5)
        for name in n.TARGETS:
            changed = copy.deepcopy(data)
            changed["targets"][name]["operations"][0]["plane"] = "permanent"
            with self.subTest(name=name), self.assertRaises(ValueError):
                n.validate_policy(changed)

    def test_backend_never_issues_a_permanent_mutation(self):
        backend = n.Backend("k8s-worker01")
        try:
            with patch.object(n, "command", return_value={"rc": 0, "stdout": "success", "stderr": ""}) as query:
                for row in n.plan_for(backend.node):
                    backend.edit(row, row["desired"])
                self.assertTrue(all("--permanent" not in call.args[0] for call in query.call_args_list))
                row = dict(n.plan_for(backend.node)[0], plane="permanent")
                count = query.call_count
                with self.assertRaises(ValueError):
                    backend.edit(row, True)
                self.assertEqual(query.call_count, count)
        finally:
            backend.cache.cleanup()

    def test_permanent_promotion_fails_before_preflight_or_remote_execution(self):
        old = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, "verify_source") as verify, \
                patch.object(c, "preflight") as preflight, patch.object(c, "remote_request") as remote:
            try:
                os.chdir(tmp)
                with patch("sys.argv", ["run-node-firewall.py", "promote-permanent", "--node", "k8s-worker01"]), \
                        contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
                    c.main()
                self.assertEqual(result.exception.code, 2)
                verify.assert_not_called(); preflight.assert_not_called(); remote.assert_not_called()
            finally:
                os.chdir(old)

    def test_remote_entry_rejects_missing_policy_before_identity_or_mutation(self):
        output = io.StringIO()
        with patch.object(n, "Backend") as backend, contextlib.redirect_stdout(output):
            n.main({"mode": "apply", "node": "k8s-worker01", "policy": {}})
        backend.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["status"], "STOP")

    def test_initial_zone_controls_must_be_explicit_and_match(self):
        state = FakeBackend(MemoryJournal()).snapshot()
        controls = {p: {"forward": "yes", "masquerade": "no"} for p in ("runtime", "permanent")}
        n.validate_zone_controls(state, controls)
        for key in ("forward", "masquerade"):
            altered = copy.deepcopy(controls)
            altered["runtime"][key] = "no" if key == "forward" else "yes"
            with self.assertRaises(ValueError):
                n.validate_zone_controls(state, altered)
        with self.assertRaises(ValueError):
            n.validate_zone_controls(state, {})

    def test_residual_configuration_change_stops_the_next_operation(self):
        journal = MemoryJournal()
        backend = FakeBackend(journal)
        request = original_tests.Transactions().request_for(backend)
        original = backend.edit
        def edit(row, desired):
            result = original(row, desired)
            backend.state["residual_metadata"].append({"path": "unit-fixture", "exists": True})
            return result
        with patch.object(backend, "edit", side_effect=edit):
            receipt = n.apply_transaction(backend, journal, request)
        self.assertEqual(receipt["status"], "PARTIAL_APPLY_REVIEW_REQUIRED")
        self.assertEqual(len(backend.edits), 1)
        with self.assertRaises(ValueError):
            n.rollback_transaction(backend, journal, receipt, request)

    def test_success_receipt_cannot_hide_an_incomplete_prefix(self):
        journal = MemoryJournal()
        backend = FakeBackend(journal)
        req = original_tests.Transactions().request_for(backend)
        receipt = n.apply_transaction(backend, journal, req)
        receipt["operations"].pop()
        with self.assertRaises(ValueError):
            n.validate_receipt(receipt, req)

    def test_v1_receipt_is_retrievable_but_not_a_v2_rollback_input(self):
        journal = MemoryJournal()
        backend = FakeBackend(journal)
        req = original_tests.Transactions().request_for(backend)
        receipt = n.apply_transaction(backend, journal, req)
        receipt["schema"] = "fds.node-firewall-receipt.v1"
        with self.assertRaisesRegex(ValueError, "legacy"):
            n.validate_receipt(receipt, req)
        self.assertEqual(str(n.ROOT), "/var/lib/fds/icr-d-net-01-01/node-firewall-v1")


class ExecutionBounds(unittest.TestCase):
    def test_remaining_budget_caps_each_subprocess_timeout(self):
        state = {"count": 0, "timeouts": 0, "budget_exhausted": False, "deadline": 105.0}
        with patch.dict(n.COMMAND_STATE, state, clear=True), patch.object(n.time, "monotonic", return_value=100.0), \
                patch.object(n, "binary", return_value="/usr/bin/firewall-cmd"), \
                patch.object(n.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="running", stderr="")) as run:
            self.assertEqual(n.command(["firewall-cmd", "--state"], timeout=15)["rc"], 0)
            self.assertEqual(run.call_args.kwargs["timeout"], 5.0)

    def test_exhausted_time_or_count_budget_stops_without_subprocess(self):
        for deadline, count in ((99.0, 0), (200.0, n.MAX_COMMANDS)):
            state = {"count": count, "timeouts": 0, "budget_exhausted": False, "deadline": deadline}
            with patch.dict(n.COMMAND_STATE, state, clear=True), patch.object(n.time, "monotonic", return_value=100.0), \
                    patch.object(n.subprocess, "run") as run:
                result = n.command(["firewall-cmd", "--state"])
                self.assertNotEqual(result["rc"], 0)
                self.assertTrue(n.COMMAND_STATE["budget_exhausted"])
                run.assert_not_called()

    def test_controller_serializes_different_targets_with_one_lock(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp, patch.object(c.Path, "home", return_value=Path(tmp)):
            first = c.ControllerLock()
            try:
                with self.assertRaises(BlockingIOError):
                    c.ControllerLock()
            finally:
                first.close()
            second = c.ControllerLock()
            second.close()

    def test_local_bin_kubectl_still_passes_trust_and_execute_checks(self):
        with patch.object(n.Path, "exists", lambda path: str(path) == "/usr/local/bin/kubectl"), \
                patch.object(n.Path, "resolve", lambda path, strict=True: path), \
                patch.object(n, "trusted", return_value=SimpleNamespace(st_mode=0o100755)) as trusted:
            self.assertEqual(n.binary("kubectl"), "/usr/local/bin/kubectl")
            trusted.assert_called_once_with(Path("/usr/local/bin/kubectl"))

    def test_stderr_truncation_and_warning_never_prove_mutation_ownership(self):
        for data in ({"rc": 0, "stdout": "success", "stderr": "", "stderr_truncated": True},
                     {"rc": 0, "stdout": "success", "stderr": "new warning"},
                     {"rc": 0, "stdout": "ZONE_ALREADY_SET", "stderr": ""}):
            self.assertEqual(n.outcome(data, True, True, False), "uncertain")

    def test_duplicate_json_or_symlink_input_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text('{"mode":"inspect","mode":"apply"}')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                c.load_checked(path, c.digest(path))
            link = Path(tmp) / "link.json"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                c.load_checked(link, c.digest(path))

    def test_controller_does_not_inherit_unrelated_credentials_or_python_settings(self):
        with patch.dict(os.environ, {"PYTHONPATH": "unsafe", "ANSIBLE_CONFIG": "unapproved", "EXAMPLE_SECRET": "test-only"}):
            env = c.controller_env()
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("ANSIBLE_CONFIG", env)
        self.assertNotIn("EXAMPLE_SECRET", env)
        self.assertEqual(env["LC_ALL"], "C.UTF-8")

    def test_redact_handles_space_assignment_pem_and_url_credentials(self):
        for text, secret in (("--password secret-value", "secret-value"), ("token: secret-value", "secret-value"),
                             ('"client-key-data": "secret-value"', "secret-value"),
                             ("https://test-user:secret-value@example.invalid/", "secret-value"),
                             ("-----BEGIN PRIVATE KEY-----\nsecret-value\n-----END PRIVATE KEY-----", "secret-value")):
            with self.subTest(text=text):
                self.assertNotIn(secret, n.redact(text))


if __name__ == "__main__":
    unittest.main()
