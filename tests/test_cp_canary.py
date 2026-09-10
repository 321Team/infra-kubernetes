import argparse
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


n = module("canary_node_test", "scripts/cp-canary-node.py")
c = module("canary_controller_test", "scripts/run-cp-canary.py")


class MemoryJournal:
    def __init__(self):
        self.saved = []
        self.fail_pending = False

    def save(self, receipt):
        if self.fail_pending and any(o["status"] == "add_pending" for o in receipt["operations"]):
            raise OSError("disk full")
        self.saved.append(copy.deepcopy(receipt))


class FakeBackend:
    def __init__(self, journal):
        self.journal = journal
        self.state = {"hostname": "k8s-control01", "planes": {
            p: {"fields": {"services": "ssh dhcpv6-client"}, "rules": ["existing-lb-rule"]}
            for p in ("permanent", "runtime")}}
        self.stable = {"nodes": [["k8s-control01", "uid1"]], "members": [{"name": "k8s-control01", "id": "1",
                       "peerURLs": ["https://10.1.93.102:2380"], "clientURLs": ["https://10.1.93.102:2379"], "isLearner": False}]}
        self.edits = []
        self.fail_at = None
        self.timeout_at = None
        self.race_at = None

    def snapshot(self):
        return copy.deepcopy(self.state)

    def topology(self):
        return {"stable": copy.deepcopy(self.stable), "sha256": n.fingerprint(self.stable)}

    def query(self, plane, rule):
        return n.canonical_rule(rule) in self.state["planes"][plane]["rules"]

    def edit(self, plane, rule, action):
        expected = "add_pending" if action == "add" else "remove_pending"
        assert any(o["plane"] == plane and o["rule"] == rule and o["status"] == expected for o in self.journal.saved[-1]["operations"]), "mutation preceded durable intent"
        self.edits.append((plane, rule, action))
        index = len(self.edits)
        if index == self.fail_at:
            return {"rc": 2, "stdout": "", "stderr": "permission denied"}
        rules = self.state["planes"][plane]["rules"]
        if action == "add":
            rules.append(n.canonical_rule(rule))
            rules.sort()
        else:
            rules.remove(n.canonical_rule(rule))
        if index == self.timeout_at:
            return {"rc": 124, "stdout": "", "stderr": "timeout"}
        return {"rc": 0, "stdout": "ALREADY_ENABLED" if index == self.race_at else "success", "stderr": ""}


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.journal = MemoryJournal()
        self.backend = FakeBackend(self.journal)
        self.request = {"run_id": "20260910T120000Z-abcdef123456", "source_sha256": "a" * 64,
                        "source_commit": "b" * 40, "before_sha256": n.fingerprint(self.backend.state),
                        "topology_sha256": self.backend.topology()["sha256"]}

    def apply(self):
        return n.apply_transaction(self.backend, self.journal, self.request)

    def test_full_apply_and_reverse_rollback(self):
        before = self.backend.snapshot()
        receipt = self.apply()
        self.assertEqual(receipt["status"], "APPLIED_REVIEW_REQUIRED")
        self.assertEqual(len(self.backend.edits), 12)
        added = list(self.backend.edits)
        rolled = n.rollback_transaction(self.backend, self.journal, receipt, self.request)
        self.assertEqual(rolled["status"], "ROLLED_BACK_REVIEW_REQUIRED")
        self.assertEqual(self.backend.snapshot(), before)
        self.assertEqual([(p, r) for p, r, a in self.backend.edits[12:]], [(p, r) for p, r, a in reversed(added)])

    def test_preexisting_rule_never_removed(self):
        self.backend.state["planes"]["runtime"]["rules"].append(n.canonical_rule(n.RULES[0]))
        self.backend.state["planes"]["runtime"]["rules"].sort()
        self.request["before_sha256"] = n.fingerprint(self.backend.state)
        receipt = self.apply()
        self.assertEqual(len(self.backend.edits), 11)
        n.rollback_transaction(self.backend, self.journal, receipt, self.request)
        self.assertTrue(self.backend.query("runtime", n.RULES[0]))
        self.assertNotIn(("runtime", n.RULES[0], "remove"), self.backend.edits)

    def test_snapshot_drift_before_apply_changes_nothing(self):
        self.backend.state["drift"] = True
        with self.assertRaisesRegex(ValueError, "differs"):
            self.apply()
        self.assertEqual(self.backend.edits, [])

    def test_new_membership_blocks_rollback(self):
        receipt = self.apply()
        self.backend.stable["nodes"].append(["k8s-control02", "uid2"])
        with self.assertRaisesRegex(ValueError, "membership changed"):
            n.rollback_transaction(self.backend, self.journal, receipt, self.request)
        self.assertEqual(len(self.backend.edits), 12)

    def test_initial_canary_rejects_existing_second_member(self):
        self.backend.stable["members"].append({"name": "k8s-control02"})
        self.request["topology_sha256"] = self.backend.topology()["sha256"]
        with self.assertRaisesRegex(ValueError, "single CP1"):
            self.apply()
        self.assertEqual(self.backend.edits, [])

    def test_partial_failure_rolls_back_only_confirmed_adds(self):
        self.backend.fail_at = 4
        receipt = self.apply()
        self.assertEqual(receipt["status"], "PARTIAL_APPLY_REVIEW_REQUIRED")
        self.assertEqual(receipt["operations"][-1]["status"], "not_added")
        n.rollback_transaction(self.backend, self.journal, receipt, self.request)
        self.assertEqual(sum(a == "remove" for p, r, a in self.backend.edits), 3)

    def test_uncertain_timeout_refuses_automatic_rollback(self):
        self.backend.timeout_at = 2
        receipt = self.apply()
        self.assertEqual(receipt["operations"][-1]["status"], "add_uncertain")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            n.rollback_transaction(self.backend, self.journal, receipt, self.request)
        self.assertEqual(len(self.backend.edits), 2)

    def test_raced_existing_rule_does_not_claim_ownership(self):
        self.backend.race_at = 1
        receipt = self.apply()
        self.assertEqual(receipt["operations"][0]["status"], "add_uncertain")

    def test_failed_durable_intent_prevents_mutation(self):
        self.journal.fail_pending = True
        with self.assertRaises(OSError):
            self.apply()
        self.assertEqual(self.backend.edits, [])

    def test_rollback_firewall_drift_blocks_removal(self):
        receipt = self.apply()
        self.backend.state["planes"]["runtime"]["rules"].append("someone-elses-rule")
        with self.assertRaisesRegex(ValueError, "drift"):
            n.rollback_transaction(self.backend, self.journal, receipt, self.request)
        self.assertEqual(len(self.backend.edits), 12)


class ControllerTests(unittest.TestCase):
    def test_inventory_scope_rejected(self):
        inv = {"k8s_control_plane": {"hosts": list(n.NODES)}, "_meta": {"hostvars": {
            name: {"ansible_host": ip, "ansible_user": "svc_ansible"} for name, ip in n.NODES.items()}}}
        c.validate_inventory(inv)
        inv["_meta"]["hostvars"]["k8s-control02"]["ansible_host"] = "10.1.93.105"
        with self.assertRaisesRegex(ValueError, "identity"):
            c.validate_inventory(inv)

    def test_ssh_error_retains_phase_and_redacts_token(self):
        error = subprocess.CompletedProcess([], 255, "", "Permission denied; token=abcdef.0123456789abcdef")
        with patch.object(c, "run", return_value=error):
            result = c.remote_request("k8s-control01", {"mode": "inspect"})
        self.assertEqual(result["phase"], "SSH_IDENTITY")
        self.assertIn("Permission denied", result["error"])
        self.assertNotIn("abcdef.0123456789abcdef", json.dumps(result))

    def test_inspection_hash_and_expiry_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cp1.json"
            data = {"hostname": "k8s-control01", "canary_inputs_complete": True, "snapshot": {}, "topology": {},
                    "before_sha256": n.fingerprint({}), "topology_sha256": n.fingerprint({}),
                    "created_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=16)).isoformat()}
            path.write_bytes(n.encoded({"source_commit": "b" * 40, "source_sha256": c.digest(c.NODE_SOURCE), "transport": "PASS", "result": data}))
            args = argparse.Namespace(mode="apply", input=path, input_sha256=c.digest(path))
            with self.assertRaisesRegex(ValueError, "15 minutes"):
                c.request_for(args, "b" * 40, "run")
            args.input_sha256 = "0" * 64
            with self.assertRaisesRegex(ValueError, "checksum"):
                c.request_for(args, "b" * 40, "run")

    def test_partial_archive_has_verified_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "FDS_CP1_Canary_test"
            folder.mkdir()
            (folder / "RESULT.json").write_text('{"status":"STOP"}')
            archive, sidecar = c.archive_output(folder)
            self.assertEqual(sidecar.read_text().split()[0], c.digest(archive))
            self.assertEqual((folder / "SHA256SUMS").read_text().split()[0], c.digest(folder / "RESULT.json"))


class PolicyRegressionTests(unittest.TestCase):
    # Synthetic formatted output; the original v1.0 result did not retain this query.
    active = "allow-host-ipv6 (active)\n  ingress-zones: ANY\n  egress-zones: HOST\n"

    def setUp(self):
        self.backend = n.Backend("k8s-control01")
        self.addCleanup(self.backend.cache.cleanup)
        self.calls = []

    def response(self, argv):
        self.calls.append(argv)
        values = {"--get-active-policies": self.active, "--get-policies": "allow-host-ipv6\n",
                  "--info-policy=allow-host-ipv6": self.active, "--state": "running\n",
                  "--get-default-zone": "public\n", "--get-zone-of-interface=ens160": "public\n",
                  "--get-all-rules": ""}
        if argv == ["getenforce"]:
            output = "Enforcing\n"
        elif argv[0] == "nmcli":
            output = "12345678-1234-1234-1234-123456789abc\n" if "GENERAL.CON-UUID" in argv else "\n"
        elif argv[-1] == "--list-all":
            interface = "" if "--permanent" in argv else "ens160"
            output = ('public (default, active)\n  target: default\n  interfaces: ' + interface +
                      '\n  sources: \n  services: dhcpv6-client ssh\n  ports: \n  protocols: \n  rich rules:\n' +
                      '    rule family="ipv4" source address="10.1.93.109/32" port port="6443" protocol="tcp" accept\n')
        else:
            output = values[argv[-1]]  # Unknown query, including --info-policy=ANY, fails the test.
        return {"rc": 0, "stdout": output, "stderr": ""}

    def test_full_snapshot_uses_policy_name_list_and_retains_raw_queries(self):
        with patch.object(self.backend, "identity"), patch.object(n, "command", side_effect=self.response):
            result = self.backend.snapshot()
        self.assertEqual(result["active_policies"]["active_output"], self.active.strip())
        self.assertEqual(set(result["active_policies"]["configurations"]["runtime"]), {"allow-host-ipv6"})
        self.assertEqual(result["nm_zone"], "")
        self.assertIn("firewall-cmd --get-active-policies", self.backend.snapshot_queries)
        self.assertFalse(any("--info-policy=ANY" in argv or "--info-policy=HOST" in argv for argv in self.calls))

    def test_inactive_and_permanent_policies_are_preserved(self):
        def respond(argv):
            if argv[-1] == "--get-policies":
                return {"rc": 0, "stdout": "allow-host-ipv6 dormant\n", "stderr": ""}
            if argv[-1] == "--info-policy=dormant":
                return {"rc": 0, "stdout": "dormant\n  target: CONTINUE\n", "stderr": ""}
            return self.response(argv)
        with patch.object(n, "command", side_effect=respond):
            result = self.backend.policy_snapshot()
        for plane in ("runtime", "permanent"):
            self.assertIn("dormant", result["configurations"][plane])

    def test_policy_query_failure_is_retained_and_not_ignored(self):
        def respond(argv):
            if argv[-1] == "--info-policy=allow-host-ipv6":
                return {"rc": 140, "stdout": "", "stderr": "INVALID_POLICY: allow-host-ipv6"}
            return self.response(argv)
        with patch.object(n, "command", side_effect=respond), self.assertRaisesRegex(ValueError, "rc=140"):
            self.backend.policy_snapshot()
        stored = self.backend.snapshot_queries["firewall-cmd --info-policy=allow-host-ipv6"]
        self.assertEqual(stored["rc"], 140)
        self.assertIn("INVALID_POLICY", stored["stderr"])

    def test_malformed_name_list_stops_before_policy_lookup(self):
        def respond(argv):
            if argv[-1] == "--get-policies":
                return {"rc": 0, "stdout": "allow-host-ipv6 ingress-zones: ANY", "stderr": ""}
            return self.response(argv)
        with patch.object(n, "command", side_effect=respond), self.assertRaisesRegex(ValueError, "policy-name list"):
            self.backend.policy_snapshot()
        self.assertFalse(any(any(x.startswith("--info-policy=") for x in argv) for argv in self.calls))

    def test_collection_summary_exposes_diagnostic_gaps(self):
        wrapper = {"transport": "PASS", "result": {"status": "INSPECTED_REVIEW_REQUIRED",
                   "gaps": ["FIREWALL_PRECHECK"], "failed_queries": [], "canary_inputs_complete": False}}
        summary = c.target_summary(wrapper)
        self.assertEqual(c.collection_status({"k8s-control01": summary}), "INCOMPLETE_REVIEW_REQUIRED")
        summary["diagnostic_gaps"] = []
        summary["failed_queries"] = ["nft"]
        self.assertEqual(c.collection_status({"k8s-control01": summary}), "INCOMPLETE_REVIEW_REQUIRED")
        summary["failed_queries"] = []
        self.assertEqual(c.collection_status({"k8s-control02": summary}), "COLLECTED_REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
