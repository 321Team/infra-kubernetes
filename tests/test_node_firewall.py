import copy
import datetime as dt
import importlib.util
import io
import contextlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


n = load("node_test", "scripts/node-firewall.py")
c = load("controller_test", "scripts/run-node-firewall.py")


class MemoryJournal:
    def __init__(self):
        self.saved = []
        self.fail_intent = False

    def save(self, receipt):
        if self.fail_intent and any(x["status"] in ("pending", "undo_pending") for x in receipt["operations"]):
            raise OSError("disk full")
        self.saved.append(copy.deepcopy(receipt))


class FakeBackend:
    def __init__(self, journal, name="k8s-worker01"):
        self.node = name
        self.journal = journal
        self.state = {"hostname": name, "nm_uuid": "unchanged", "planes": {
            p: {"fields": {"services": "cockpit dhcpv6-client ssh" if name in n.WORKERS else "dhcpv6-client ssh",
                           "forward": "yes", "masquerade": "no"},
                "rules": [] if name in n.WORKERS else [n.canonical_rule(n.LB_RULE)]}
            for p in ("permanent", "runtime")}, "residual_metadata": []}
        self.edits = []
        self.fail_at = self.timeout_at = self.warning_at = self.drift_at = None

    def snapshot(self):
        return copy.deepcopy(self.state)

    def query(self, row):
        return n.present_in(self.state, row)

    def edit(self, row, desired):
        assert any(x["plane"] == row["plane"] and x["value"] == row["value"] and x["status"] in ("pending", "undo_pending")
                   for x in self.journal.saved[-1]["operations"]), "mutation before durable intent"
        self.edits.append((copy.deepcopy(row), desired))
        index = len(self.edits)
        if index == self.fail_at:
            return {"rc": 2, "stdout": "", "stderr": "denied"}
        n.set_presence(self.state, row, desired)
        if index == self.drift_at:
            self.state["nm_uuid"] = "external change"
        if index == self.timeout_at:
            return {"rc": 124, "stdout": "", "stderr": "timeout after mutation"}
        return {"rc": 0, "stdout": "success", "stderr": "NOT_ENABLED" if index == self.warning_at else ""}


class Transactions(unittest.TestCase):
    def setUp(self):
        self.journal = MemoryJournal()
        self.backend = FakeBackend(self.journal)
        self.request = self.request_for(self.backend)

    def request_for(self, backend):
        return {"node": backend.node, "run_id": "20260910T070000Z-abcdef123456", "source_commit": "a" * 40,
                "source_sha256": "b" * 64, "authorization_sha256": "c" * 64, "topology_sha256": "d" * 64,
                "policy_sha256": n.policy_hash(), "residual_metadata_sha256": n.fingerprint(backend.state["residual_metadata"]),
                "zone_controls": {p: {"forward": "yes", "masquerade": "no"} for p in ("runtime", "permanent")},
                "cp1_receipt_sha256": "e" * 64, "cp1_acceptance_sha256": "f" * 64,
                "plan_sha256": n.plan_hash(backend.node), "before_sha256": n.fingerprint(backend.state)}

    def apply(self):
        return n.apply_transaction(self.backend, self.journal, self.request)

    def rollback(self, receipt):
        return n.rollback_transaction(self.backend, self.journal, receipt, self.request)

    def test_worker_combined_apply_and_exact_reverse_restore(self):
        before = self.backend.snapshot()
        receipt = self.apply()
        self.assertEqual(receipt["status"], "RUNTIME_APPLIED_REVIEW_REQUIRED")
        self.assertEqual(len(self.backend.edits), 4)
        self.assertEqual(before["planes"]["permanent"], self.backend.state["planes"]["permanent"])
        for plane in ("runtime",):
            self.assertNotIn("cockpit", self.backend.state["planes"][plane]["fields"]["services"])
            self.assertIn("ssh", self.backend.state["planes"][plane]["fields"]["services"])
            self.assertEqual(len(self.backend.state["planes"][plane]["rules"]), 3)
        edited = copy.deepcopy(self.backend.edits)
        self.assertEqual(self.rollback(receipt)["status"], "ROLLED_BACK_REVIEW_REQUIRED")
        self.assertEqual(self.backend.snapshot(), before)
        self.assertEqual([(r["plane"], r["value"], d) for r, d in self.backend.edits[4:]],
                         [(r["plane"], r["value"], not d) for r, d in reversed(edited)])

    def test_cp2_and_cp3_preserve_lb_permanent_and_only_add_six_runtime_entries(self):
        for name in ("k8s-control02", "k8s-control03"):
            backend = FakeBackend(MemoryJournal(), name)
            req = self.request_for(backend)
            before = backend.snapshot()
            receipt = n.apply_transaction(backend, backend.journal, req)
            self.assertEqual(receipt["status"], "RUNTIME_APPLIED_REVIEW_REQUIRED")
            self.assertEqual(len(backend.edits), 6)
            self.assertEqual(before["planes"]["permanent"], backend.state["planes"]["permanent"])
            self.assertTrue(all(row["kind"] == "rich-rule" for row, _ in backend.edits))
            self.assertEqual(n.rollback_transaction(backend, backend.journal, receipt, req)["status"], "ROLLED_BACK_REVIEW_REQUIRED")
            self.assertEqual(before, backend.snapshot())

    def test_existing_rule_and_absent_cockpit_are_preserved(self):
        rows = n.plan_for(self.backend.node)
        n.set_presence(self.backend.state, rows[0], True)
        n.set_presence(self.backend.state, rows[3], False)
        self.request = self.request_for(self.backend)
        before = self.backend.snapshot()
        receipt = self.apply()
        self.assertEqual(len(self.backend.edits), 2)
        self.rollback(receipt)
        self.assertEqual(before, self.backend.snapshot())

    def test_inspection_drift_stops_before_edit(self):
        self.backend.state["nm_uuid"] = "changed"
        with self.assertRaisesRegex(ValueError, "differs"):
            self.apply()
        self.assertEqual(self.backend.edits, [])

    def test_mid_apply_drift_stops_next_edit(self):
        self.backend.drift_at = 1
        receipt = self.apply()
        self.assertEqual(receipt["status"], "PARTIAL_APPLY_REVIEW_REQUIRED")
        self.assertEqual(len(self.backend.edits), 1)
        with self.assertRaisesRegex(ValueError, "drift"):
            self.rollback(receipt)

    def test_confirmed_failure_allows_owned_prefix_rollback(self):
        before = self.backend.snapshot()
        self.backend.fail_at = 3
        receipt = self.apply()
        self.assertEqual(receipt["operations"][-1]["status"], "not_changed")
        self.assertEqual(self.rollback(receipt)["status"], "ROLLED_BACK_REVIEW_REQUIRED")
        self.assertEqual(before, self.backend.snapshot())

    def test_timeout_after_add_blocks_automatic_rollback(self):
        self.backend.timeout_at = 1
        receipt = self.apply()
        self.assertEqual(receipt["operations"][0]["status"], "uncertain")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.rollback(receipt)
        self.assertEqual(len(self.backend.edits), 1)

    def test_timeout_after_service_removal_blocks_automatic_rollback(self):
        self.backend.timeout_at = 4
        receipt = self.apply()
        self.assertEqual(receipt["operations"][3]["kind"], "service")
        self.assertEqual(receipt["operations"][3]["status"], "uncertain")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.rollback(receipt)

    def test_idempotent_warning_is_not_proof_of_ownership(self):
        self.backend.warning_at = 4
        receipt = self.apply()
        self.assertEqual(receipt["operations"][3]["status"], "uncertain")

    def test_failed_intent_persistence_prevents_mutation(self):
        self.journal.fail_intent = True
        with self.assertRaises(OSError):
            self.apply()
        self.assertEqual(self.backend.edits, [])

    def test_rollback_restoration_timeout_never_claims_success(self):
        receipt = self.apply()
        self.backend.timeout_at = 5
        result = self.rollback(receipt)
        self.assertEqual(result["status"], "PARTIAL_ROLLBACK_REVIEW_REQUIRED")
        self.assertEqual(result["operations"][-1]["status"], "undo_uncertain")

    def test_malformed_or_foreign_receipt_stops(self):
        receipt = self.apply()
        for modify in (lambda r: r.update(hostname="k8s-worker02"), lambda r: r.update(source_commit="f" * 40),
                       lambda r: r["operations"].reverse(), lambda r: r["operations"].append(r["operations"][0]),
                       lambda r: r["operations"][0].update(present_before=True)):
            changed = copy.deepcopy(receipt)
            modify(changed)
            with self.assertRaises(ValueError):
                self.rollback(changed)
        self.assertEqual(len(self.backend.edits), 4)


class Gates(unittest.TestCase):
    def topology(self):
        stable = {"nodes": [["k8s-control01", "uid"]], "members": [{"name": "k8s-control01", "id": "1755679419291778130", "isLearner": False}]}
        return {"stable": stable, "sha256": n.fingerprint(stable)}

    def test_membership_change_blocks_gate(self):
        with self.assertRaisesRegex(ValueError, "membership changed"):
            c.topology_gate(self.topology(), "k8s-worker01", "f" * 64, unjoined=True)

    def test_joined_target_blocks_firewall_stage(self):
        topology = self.topology()
        topology["stable"]["nodes"].append(["k8s-worker01", "uid2"])
        topology["sha256"] = n.fingerprint(topology["stable"])
        with self.assertRaisesRegex(ValueError, "already registered"):
            c.topology_gate(topology, "k8s-worker01", unjoined=True)

    def test_unnamed_etcd_member_peer_blocks_cp_stage(self):
        topology = self.topology()
        topology["stable"]["members"].append({"name": "", "peerURLs": ["https://10.1.93.103:2380"]})
        topology["sha256"] = n.fingerprint(topology["stable"])
        with self.assertRaisesRegex(ValueError, "member already"):
            c.topology_gate(topology, "k8s-control02", unjoined=True)

    def test_expired_future_and_naive_inspection_fail(self):
        for instant in (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=16),
                        dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1), dt.datetime.now()):
            with self.assertRaises((ValueError, TypeError)):
                n.fresh(instant.isoformat())

    def test_unfilled_authorization_blocks(self):
        with self.assertRaises(ValueError):
            c.validate_authorization({}, "a" * 40, "k8s-worker01", "apply")

    def test_inspection_cannot_substitute_for_cp1_apply_receipt(self):
        with self.assertRaisesRegex(ValueError, "CP1 apply receipt"):
            c.validate_cp1_receipt({"status": "INSPECTED_REVIEW_REQUIRED"})

    def test_cp1_and_arbitrary_destination_not_in_mutation_plan(self):
        for name in ("k8s-control01", "other"):
            with self.assertRaises(ValueError):
                n.plan_for(name)

    def test_fixed_plan_counts_no_broad_network_or_direct_6443(self):
        self.assertEqual(sum(len(n.plan_for(name)) for name in n.WORKERS), 12)
        for name in n.TARGETS:
            for row in n.plan_for(name):
                self.assertNotIn("/24", row["value"])
                self.assertNotIn("6443", row["value"])

    def test_approved_ssh_identity_transport(self):
        argv = c.ssh_argv("k8s-worker01", ["sudo", "-n", "/usr/bin/python3", "-"])
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("IdentityAgent=none", argv)
        self.assertIn("ControlMaster=no", argv)
        self.assertIn("10.250.10.10", argv)
        self.assertEqual(argv[-2], "svc_ansible@10.1.93.105")

    def test_hash_mismatch_prevents_input_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "input.json"
            file.write_text('{}')
            with self.assertRaisesRegex(ValueError, "checksum"):
                c.load_checked(str(file), "f" * 64)

    def test_output_archive_covers_all_evidence(self):
        import tarfile
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "result"
            folder.mkdir()
            (folder / "RESULT.json").write_text('{"status":"STOP"}')
            (folder / "TARGET.json").write_text('{"transport":"ERROR"}')
            archive, sidecar = c.archive_output(folder)
            self.assertIn(c.digest(archive), sidecar.read_text())
            self.assertEqual(len((folder / "SHA256SUMS").read_text().splitlines()), 2)
            with tarfile.open(archive) as tar:
                self.assertTrue(any(x.name.endswith('/TARGET.json') for x in tar.getmembers()))


def cp1_producer_receipt(preserve_first=False):
    """Run byte-pinned PR2 producer code against a fake backend. Not onsite acceptance."""
    path = ROOT / "scripts/cp-canary-node.py"
    if c.digest(path) != n.CP1_CONTRACT["source_sha256"]:
        raise AssertionError("integrated PR2 producer source differs from receipt contract")
    producer = load("integrated_pr2_producer", "scripts/cp-canary-node.py")
    journal = MemoryJournal()
    base = FakeBackend(journal, "k8s-control01")
    base.state.pop("residual_metadata")
    if preserve_first:
        base.state["planes"]["runtime"]["rules"].append(producer.canonical_rule(producer.RULES[0]))
        base.state["planes"]["runtime"]["rules"].sort()
    stable = {"nodes": [["k8s-control01", "unit-node"]],
              "members": [{"name": "k8s-control01", "id": "unit-member", "isLearner": False,
                           "peerURLs": ["https://10.1.93.102:2380"]}]}

    class ProducerBackend:
        node = "k8s-control01"

        def snapshot(self):
            return copy.deepcopy(base.state)

        def topology(self):
            return {"stable": copy.deepcopy(stable), "sha256": producer.fingerprint(stable)}

        def query(self, plane, rule):
            return producer.canonical_rule(rule) in base.state["planes"][plane]["rules"]

        def edit(self, plane, rule, action):
            assert plane == "runtime" and action == "add"
            assert journal.saved[-1]["operations"][-1]["status"] == "add_pending"
            base.state["planes"][plane]["rules"].append(producer.canonical_rule(rule))
            base.state["planes"][plane]["rules"].sort()
            return {"rc": 0, "stdout": "success", "stderr": ""}

    backend = ProducerBackend()
    request = {**n.CP1_CONTRACT, "run_id": "20260910T070000Z-abcdef123456", "authorization_sha256": "c" * 64,
               "before_sha256": producer.fingerprint(backend.snapshot()), "topology_sha256": producer.fingerprint(stable)}
    with patch.object(dt, "datetime", FixedWeekday):
        current = dt.datetime.now(dt.timezone.utc)
        window = producer.OperationWindow(current - dt.timedelta(minutes=1), current + dt.timedelta(minutes=5))
        receipt = producer.apply_transaction(backend, journal, request, window)
    assert receipt["status"] == "RUNTIME_APPLIED_REVIEW_REQUIRED", receipt
    return {"status": receipt["status"], "receipt": receipt, "receipt_sha256": producer.fingerprint(receipt)}


def acceptance_for(proof, input_sha):
    receipt = proof["receipt"]
    return {"schema": "fds.cp1-runtime-acceptance.v1", "decision": "ACCEPTED", **n.CP1_CONTRACT,
            "run_id": receipt["run_id"], "receipt_sha256": proof["receipt_sha256"], "receipt_input_sha256": input_sha,
            "independent_reviewer": "hayeounglee", "review_decision": "APPROVED",
            "review_url": "https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-1",
            "reviewed_at": n.now(),
            "checks": {key: {"status": "PASS", "evidence_sha256": "e" * 64}
                       for key in ("positive", "negative", "regression")}}


def authorization_for(inputs, inspection_sha, mode="apply"):
    current = dt.datetime.now(dt.timezone.utc)
    return {"schema": "fds.node-firewall-authorization.v2", "phase": "runtime",
            "source_commit": inputs["source_commit"], "source_sha256": inputs["source_sha256"],
            "target": inputs["hostname"], "mode": mode, "operator": "GWONUK", "independent_reviewer": "hayeounglee",
            "review_url": "https://github.com/321Team/infra-kubernetes/pull/4#issuecomment-1", "review_decision": "APPROVED",
            "maintenance_freeze": True, "console_recovery_available": True, "backup_verified": True,
            "protection_reviewed": True, "rollback_runbook_reviewed": True, "runtime_only_acknowledged": True,
            "target_state_reviewed": True, "policy_sha256": inputs["policy_sha256"], "plan_sha256": inputs["plan_sha256"],
            "before_sha256": inputs["before_sha256"], "residual_metadata_sha256": inputs["residual_metadata_sha256"],
            "cp1_state_sha256": inputs["cp1_state_sha256"], "inspection_sha256": inspection_sha,
            "zone_controls": {p: {key: inputs["snapshot"]["planes"][p]["fields"][key] for key in ("forward", "masquerade")}
                              for p in ("runtime", "permanent")},
            "window_start": (current - dt.timedelta(minutes=1)).isoformat(),
            "window_end": (current + dt.timedelta(minutes=30)).isoformat()}


class FixedWeekday(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 9, 10, 12, 0, tzinfo=dt.timezone.utc)
        return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)


@patch.object(dt, "datetime", FixedWeekday)
class ControllerWorkflow(unittest.TestCase):
    """Producer/consumer integration with mocked I/O; approval records are test inputs, not real approvals."""
    def exercise(self, fail=False, bad_acceptance=False, rollback=False):
        journal = MemoryJournal()
        backend = FakeBackend(journal)
        backend.timeout_at = 4 if fail else None
        original = backend.snapshot()
        topology = Gates().topology()
        topology.update(hostname="k8s-control01", status="TOPOLOGY_REVIEW_REQUIRED")
        cp1_proof = cp1_producer_receipt()
        stable = {"firewall": cp1_proof["receipt"]["after"], "protection": {"fixture": True}}
        cp1_state = {"stable": stable, "sha256": n.fingerprint(stable),
                     "hostname": "k8s-control01", "status": "CP1_STATE_REVIEW_REQUIRED"}
        commit = "a" * 40
        calls = []

        def remote(name, request):
            calls.append((name, request["mode"]))
            if request["mode"] == "topology":
                result = topology
            elif request["mode"] == "cp1-state":
                result = cp1_state
            elif request["mode"] == "inspect":
                result = {"status": "INSPECTED_REVIEW_REQUIRED", "hostname": name, "phase": "runtime",
                          "policy_sha256": n.policy_hash(), "plan_sha256": n.plan_hash(name),
                          "snapshot": backend.snapshot(), "before_sha256": n.fingerprint(backend.state)}
            elif request["mode"] == "apply":
                receipt = n.apply_transaction(backend, journal, request)
                result = {"status": receipt["status"], "receipt": receipt, "receipt_sha256": n.fingerprint(receipt)}
            elif request["mode"] == "rollback":
                receipt = n.rollback_transaction(backend, journal, copy.deepcopy(journal.saved[-1]), request)
                result = {"status": receipt["status"], "receipt": receipt, "receipt_sha256": n.fingerprint(receipt)}
            else:
                raise AssertionError("unexpected remote mode")
            return {"transport": "PASS", "result": copy.deepcopy(result)}

        def invoke(argv, expected_code):
            with patch("sys.argv", ["run-node-firewall.py"] + argv), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as code:
                c.main()
            self.assertEqual(code.exception.code, expected_code)

        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, "verify_source", return_value=commit), \
                patch.object(c, "preflight", return_value={}), patch.object(c, "remote_request", side_effect=remote), \
                patch.object(c, "ControllerLock"):
            try:
                os.chdir(tmp)
                invoke(["inspect", "--node", backend.node], 0)
                inspection = next(Path(tmp).glob("FDS_Node_Firewall_*/INSPECTION.json"))
                inputs = json.loads(inspection.read_bytes())
                proof = Path(tmp) / "mock-producer-cp1.json"
                proof.write_bytes(n.encoded(cp1_proof))
                acceptance = Path(tmp) / "mock-cp1-acceptance.json"
                accept_data = acceptance_for(cp1_proof, c.digest(proof))
                if bad_acceptance:
                    accept_data["checks"]["negative"]["status"] = "NOT_VERIFIED"
                acceptance.write_bytes(n.encoded(accept_data))
                auth = Path(tmp) / "mock-authorization.json"
                auth_data = authorization_for(inputs, c.digest(inspection))
                auth_data.update(cp1_canary_accepted=True, cp1_receipt_input_sha256=c.digest(proof),
                                 cp1_acceptance_sha256=c.digest(acceptance))
                auth.write_bytes(n.encoded(auth_data))
                args = ["apply", "--node", backend.node, "--inspection", str(inspection),
                        "--inspection-sha256", c.digest(inspection), "--authorization", str(auth), "--authorization-sha256", c.digest(auth),
                        "--cp1-receipt", str(proof), "--cp1-receipt-sha256", c.digest(proof),
                        "--cp1-acceptance", str(acceptance), "--cp1-acceptance-sha256", c.digest(acceptance)]
                # The validator itself is exercised. Only I/O and the filesystem lock are mocked.
                invoke(args, 2 if fail or bad_acceptance else 0)
                summaries = [json.loads(p.read_bytes()) for p in Path(tmp).glob("FDS_Node_Firewall_*/RESULT.json")]
                applied = next(x for x in summaries if x["mode"] == "apply")
                expected_status = "STOP_REVIEW_REQUIRED" if fail or bad_acceptance else "RUNTIME_APPLIED_REVIEW_REQUIRED"
                self.assertEqual(applied["status"], expected_status, applied)
                self.assertEqual(applied["permanent_applied"], "not_attempted")
                if bad_acceptance:
                    self.assertEqual(backend.edits, [])
                    self.assertNotIn((backend.node, "apply"), calls)
                    return
                self.assertEqual(applied["runtime_applied"], "attempted_requires_receipt_review" if fail else "applied")
                targets = [p for p in Path(tmp).glob("FDS_Node_Firewall_*/TARGET.json")
                           if "receipt" in json.loads(p.read_bytes()).get("result", {})]
                self.assertEqual(len(targets), 1)
                receipt_file = targets[0]
                receipt = json.loads(receipt_file.read_bytes())["result"]["receipt"]
                self.assertEqual(receipt["status"], "PARTIAL_APPLY_REVIEW_REQUIRED" if fail else "RUNTIME_APPLIED_REVIEW_REQUIRED")
                if fail:
                    self.assertNotIn((backend.node, "rollback"), calls)
                else:
                    self.assertEqual(calls[-3:], [(backend.node, "inspect"), ("k8s-control01", "topology"), ("k8s-control01", "cp1-state")])
                    self.assertEqual(backend.state["planes"]["permanent"], original["planes"]["permanent"])
                if rollback:
                    existing = set(Path(tmp).glob("FDS_Node_Firewall_*/INSPECTION.json"))
                    invoke(["inspect", "--node", backend.node], 0)
                    fresh = next(iter(set(Path(tmp).glob("FDS_Node_Firewall_*/INSPECTION.json")) - existing))
                    rollback_auth = authorization_for(json.loads(fresh.read_bytes()), c.digest(fresh), "rollback")
                    rollback_auth["receipt_input_sha256"] = c.digest(receipt_file)
                    auth.write_bytes(n.encoded(rollback_auth))
                    invoke(["rollback", "--node", backend.node, "--inspection", str(fresh), "--inspection-sha256", c.digest(fresh),
                            "--authorization", str(auth), "--authorization-sha256", c.digest(auth),
                            "--receipt", str(receipt_file), "--receipt-sha256", c.digest(receipt_file)], 0)
                    self.assertEqual(backend.snapshot(), original)
            finally:
                os.chdir(old_cwd)

    def test_inspect_apply_and_new_management_session_flow(self):
        self.exercise()

    def test_partial_mutation_retains_receipt_without_automatic_retry(self):
        self.exercise(fail=True)

    def test_missing_runtime_acceptance_stops_before_target_mutation(self):
        self.exercise(bad_acceptance=True)

    def test_runtime_rollback_requires_fresh_bound_inspection_and_restores_original(self):
        self.exercise(rollback=True)

    def test_self_review_record_fails_before_time_window_check(self):
        data = {"schema": "fds.node-firewall-authorization.v2", "phase": "runtime",
                "source_commit": "a" * 40, "target": "k8s-worker01", "mode": "apply",
                "operator": "GWONUK", "independent_reviewer": "GWONUK"}
        with self.assertRaisesRegex(ValueError, "independent review"):
            c.validate_authorization(data, "a" * 40, "k8s-worker01", "apply")


class BackendSemantics(unittest.TestCase):
    def test_zone_parser_retains_ssh_and_normalizes_service_order(self):
        zone = 'public (active)\n  target: default\n  interfaces: ens160\n  sources:\n  services: ssh cockpit dhcpv6-client\n  ports:\n  protocols:\n  forward: yes\n  masquerade: no\n  rich rules:\n'
        self.assertEqual(n.parse_zone(zone)["fields"]["services"], "cockpit dhcpv6-client ssh")
        with self.assertRaisesRegex(ValueError, "SSH"):
            n.parse_zone(zone.replace('ssh ', ''))

    def test_query_requires_known_return_and_output_pair(self):
        backend = n.Backend("k8s-worker01")
        try:
            for rc, stdout, expected in ((0, "yes", True), (1, "no", False)):
                with patch.object(n, "command", return_value={"rc": rc, "stdout": stdout, "stderr": ""}):
                    self.assertIs(backend.query(n.plan_for(backend.node)[0]), expected)
            with patch.object(n, "command", return_value={"rc": 0, "stdout": "", "stderr": ""}), self.assertRaises(ValueError):
                backend.query(n.plan_for(backend.node)[0])
        finally:
            backend.cache.cleanup()

    def test_backend_rejects_unlisted_mutation(self):
        backend = n.Backend("k8s-worker01")
        row = n.plan_for(backend.node)[0]
        row["value"] = "ssh"
        try:
            with patch.object(n, "command") as command, self.assertRaises(ValueError):
                backend.edit(row, False)
            command.assert_not_called()
        finally:
            backend.cache.cleanup()

    def test_timeout_preserves_redacted_partial_output(self):
        error = subprocess.TimeoutExpired(["firewall-cmd"], 15, output=b'success abcdef.0123456789abcdef')
        with patch.object(n, "binary", return_value="/usr/bin/firewall-cmd"), patch.object(n.subprocess, "run", side_effect=error):
            result = n.command(["firewall-cmd", "--state"])
        self.assertEqual(result["rc"], 124)
        self.assertIn("success", result["stdout"])
        self.assertNotIn("abcdef.0123456789abcdef", result["stdout"])

    @unittest.skipUnless(os.geteuid() == 0, "root-owned state path validation")
    def test_unresolved_journal_blocks_another_run_id(self):
        with tempfile.TemporaryDirectory(dir="/root") as tmp, patch.object(n, "ROOT", Path(tmp)):
            first = n.Journal("20260910T070000Z-abcdef123456", True)
            first.save({"status": "PARTIAL_APPLY_REVIEW_REQUIRED"})
            os.close(first.lock)
            with self.assertRaisesRegex(ValueError, "existing transaction"):
                n.Journal("20260910T070001Z-abcdef123457", True)


if __name__ == "__main__":
    unittest.main()
