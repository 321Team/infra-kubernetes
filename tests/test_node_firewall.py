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
            p: {"fields": {"services": "cockpit dhcpv6-client ssh" if name in n.WORKERS else "dhcpv6-client ssh"},
                "rules": [] if name in n.WORKERS else [n.canonical_rule(n.LB_RULE)]}
            for p in ("permanent", "runtime")}}
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
                "plan_sha256": n.plan_hash(backend.node), "before_sha256": n.fingerprint(backend.state)}

    def apply(self):
        return n.apply_transaction(self.backend, self.journal, self.request)

    def rollback(self, receipt):
        return n.rollback_transaction(self.backend, self.journal, receipt, self.request)

    def test_worker_combined_apply_and_exact_reverse_restore(self):
        before = self.backend.snapshot()
        receipt = self.apply()
        self.assertEqual(receipt["status"], "APPLIED_REVIEW_REQUIRED")
        self.assertEqual(len(self.backend.edits), 8)
        for plane in ("permanent", "runtime"):
            self.assertNotIn("cockpit", self.backend.state["planes"][plane]["fields"]["services"])
            self.assertIn("ssh", self.backend.state["planes"][plane]["fields"]["services"])
            self.assertEqual(len(self.backend.state["planes"][plane]["rules"]), 3)
        edited = copy.deepcopy(self.backend.edits)
        self.assertEqual(self.rollback(receipt)["status"], "ROLLED_BACK_REVIEW_REQUIRED")
        self.assertEqual(self.backend.snapshot(), before)
        self.assertEqual([(r["plane"], r["value"], d) for r, d in self.backend.edits[8:]],
                         [(r["plane"], r["value"], not d) for r, d in reversed(edited)])

    def test_cp2_and_cp3_preserve_lb_and_only_add_twelve_entries(self):
        for name in ("k8s-control02", "k8s-control03"):
            backend = FakeBackend(MemoryJournal(), name)
            req = self.request_for(backend)
            before = backend.snapshot()
            receipt = n.apply_transaction(backend, backend.journal, req)
            self.assertEqual(receipt["status"], "APPLIED_REVIEW_REQUIRED")
            self.assertEqual(len(backend.edits), 12)
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
        self.assertEqual(len(self.backend.edits), 6)
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
        self.backend.fail_at = 5
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
        self.backend.timeout_at = 9
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
        self.assertEqual(len(self.backend.edits), 8)


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
        self.assertEqual(sum(len(n.plan_for(name)) for name in n.WORKERS), 24)
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


class ControllerWorkflow(unittest.TestCase):
    """Synthetic controller/remote integration. No network or real host mutation."""
    def exercise(self, fail=False):
        journal = MemoryJournal()
        backend = FakeBackend(journal)
        backend.timeout_at = 4 if fail else None
        topology = Gates().topology()
        topology.update(hostname="k8s-control01", status="TOPOLOGY_REVIEW_REQUIRED")
        commit = "a" * 40
        calls = []
        def remote(name, request):
            calls.append((name, request["mode"]))
            if request["mode"] == "topology":
                result = topology
            elif request["mode"] == "inspect":
                result = {"status": "INSPECTED_REVIEW_REQUIRED", "hostname": name,
                          "snapshot": backend.snapshot(), "before_sha256": n.fingerprint(backend.state)}
            elif request["mode"] == "apply":
                receipt = n.apply_transaction(backend, journal, request)
                result = {"status": receipt["status"], "receipt": receipt, "receipt_sha256": n.fingerprint(receipt)}
            else:
                raise AssertionError("unexpected remote mode")
            return {"transport": "PASS", "result": copy.deepcopy(result)}
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, "verify_source", return_value=commit), \
                patch.object(c, "preflight", return_value={}), patch.object(c, "remote_request", side_effect=remote):
            try:
                os.chdir(tmp)
                with patch("sys.argv", ["run-node-firewall.py", "inspect", "--node", backend.node]), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as code:
                    c.main()
                self.assertEqual(code.exception.code, 0)
                inspection = next(Path(tmp).glob('FDS_Node_Firewall_*/INSPECTION.json'))
                cp1_receipt = {"schema": "fds.cp1-canary-receipt.v1", "hostname": "k8s-control01", "status": "APPLIED_REVIEW_REQUIRED",
                               "source_commit": "068661e076de02669170d3582f3d596d6e1a8eb3",
                               "source_sha256": "5b1c9bccaf80cafc7f52eaf1ad9799d4a342981784f8fdb484013e27b19ae41b",
                               "plan_sha256": "2c1e6101724d078a0a8fd58497d4d84ae5453e938529496c3045b96ea0fef7d7",
                               "operations": [{"status": "added"}] * 12}
                proof = Path(tmp) / 'synthetic-cp1.json'
                proof.write_bytes(n.encoded({"status": "APPLIED_REVIEW_REQUIRED", "receipt": cp1_receipt, "receipt_sha256": n.fingerprint(cp1_receipt)}))
                auth = Path(tmp) / 'synthetic-authorization.json'
                auth.write_text('{}')
                argv = ["run-node-firewall.py", "apply", "--node", backend.node, "--inspection", str(inspection),
                        "--inspection-sha256", c.digest(inspection), "--authorization", str(auth), "--authorization-sha256", c.digest(auth),
                        "--cp1-receipt", str(proof), "--cp1-receipt-sha256", c.digest(proof)]
                # Human review is a separate gate; this test isolates orchestration and retained evidence.
                with patch("sys.argv", argv), patch.object(c, "validate_authorization"), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as code:
                    c.main()
                self.assertEqual(code.exception.code, 2 if fail else 0)
                summaries = [json.loads(p.read_bytes()) for p in Path(tmp).glob('FDS_Node_Firewall_*/RESULT.json')]
                applied = next(x for x in summaries if x['mode'] == 'apply')
                self.assertEqual(applied['status'], 'STOP_REVIEW_REQUIRED' if fail else 'APPLIED_REVIEW_REQUIRED')
                self.assertEqual(applied['runtime_applied'], 'attempted_requires_receipt_review' if fail else 'applied')
                targets = [json.loads(p.read_bytes()) for p in Path(tmp).glob('FDS_Node_Firewall_*/TARGET.json')]
                receipts = [x['result']['receipt'] for x in targets if 'receipt' in x.get('result', {})]
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0]['status'], 'PARTIAL_APPLY_REVIEW_REQUIRED' if fail else 'APPLIED_REVIEW_REQUIRED')
                if fail:
                    self.assertNotIn(('k8s-worker01', 'rollback'), calls)
                else:
                    self.assertEqual(calls[-2:], [('k8s-worker01', 'inspect'), ('k8s-control01', 'topology')])
            finally:
                os.chdir(old_cwd)

    def test_inspect_apply_and_new_management_session_flow(self):
        self.exercise()

    def test_partial_mutation_retains_receipt_without_automatic_retry(self):
        self.exercise(fail=True)

    def test_self_review_record_fails_before_time_window_check(self):
        data = {'schema': 'fds.node-firewall-authorization.v1', 'source_commit': 'a' * 40, 'target': 'k8s-worker01',
                'mode': 'apply', 'operator': 'GWONUK', 'independent_reviewer': 'GWONUK'}
        with self.assertRaisesRegex(ValueError, 'independent review'):
            c.validate_authorization(data, 'a' * 40, 'k8s-worker01', 'apply')


class BackendSemantics(unittest.TestCase):
    def test_zone_parser_retains_ssh_and_normalizes_service_order(self):
        zone = 'public (active)\n  target: default\n  interfaces: ens160\n  sources:\n  services: ssh cockpit dhcpv6-client\n  ports:\n  protocols:\n  rich rules:\n'
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
