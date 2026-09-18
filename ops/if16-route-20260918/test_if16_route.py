import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("if16_route", Path(__file__).with_name("if16_route.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

UUID = "12345678-1234-1234-1234-123456789abc"

def fixture():
    return {"host": "edge01", "ip": "10.1.93.50", "device": "ens160", "addresses": [("10.1.93.50", 24)],
            "profile": UUID, "properties": {"ipv4.gateway": "10.1.93.254", "ipv4.dns": "10.1.93.52,10.1.93.53", "ipv4.route-table": "0"},
            "bindings": {UUID: "ens160"}, "rules": [],
            "kernel": [{"dst": "default", "gateway": "10.1.93.254", "dev": "ens160", "protocol": "static"}],
            "persistent": [{"ip": "192.0.2.0/24", "nh": "10.1.93.59", "mt": "80", "table": "200"}]}

class Machine:
    def __init__(self, before=None):
        self.state = before or fixture()
        self.calls = []
        self.records = []
        self.fail = None
        self.fail_after = False
        self.drift = False
        self.probe_bad = False
    def snapshot(self):
        return copy.deepcopy(self.state)
    def write(self, value):
        self.records.append(copy.deepcopy(value))
    def __call__(self, *argv):
        self.calls.append(argv)
        kind = "runtime" if argv[0] == "ip" else "persistent"
        action = argv[3] if kind == "runtime" else argv[-2][0]
        event = kind + ":" + action
        if self.fail == event and not self.fail_after:
            raise m.Stop("Injected command failure")
        metric = int(argv[argv.index("metric") + 1]) if kind == "runtime" else int(argv[-1].split()[2])
        if kind == "runtime":
            if action == "add":
                self.state["kernel"].append({"dst": m.DEST, "gateway": m.VIA, "dev": m.DEV, "protocol": "static", "metric": metric})
            else:
                self.state["kernel"] = [r for r in self.state["kernel"] if r.get("dst") != m.DEST]
        elif action == "+":
            self.state["persistent"].append({"ip": m.DEST, "nh": m.VIA, "mt": str(metric), "table": "254"})
        else:
            self.state["persistent"] = [r for r in self.state["persistent"] if r.get("ip") != m.DEST]
        if self.drift and event == "persistent:+":
            self.state["properties"]["ipv4.dns"] = "10.1.93.53"
        if self.fail == event:
            raise m.Stop("Injected timeout after side effect")
        return ""
    def probe(self):
        return [{"gateway": "10.1.93.254" if self.probe_bad else m.VIA, "dev": m.DEV, "prefsrc": "10.1.93.50"}]
    def apply(self):
        return m.apply(self, self.snapshot, self.write, self.probe)

class TransactionTests(unittest.TestCase):
    def test_success_preserves_unrelated_state_and_records_preimage(self):
        machine = Machine()
        before = machine.snapshot()
        result = machine.apply()
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(machine.records[0]["before"], before)
        self.assertEqual(m.without_target(machine.state), m.without_target(before))
        self.assertEqual(result["reboot_persistence"], "NOT_TESTED")
        self.assertFalse(result["whole_ac_pass"])
        self.assertEqual([x[0] for x in machine.calls], ["ip", "nmcli"])
        self.assertNotIn("reapply", str(machine.calls))
        self.assertEqual(machine.state["properties"]["ipv4.route-table"], "0")
        self.assertTrue(machine.calls[1][-1].endswith("table=254"))
        self.assertEqual([x for x in machine.state["persistent"] if x["ip"] == m.DEST][0]["table"], "254")
    def test_already_correct_does_not_mutate(self):
        machine = Machine()
        machine.apply()
        machine.calls.clear()
        self.assertEqual(machine.apply()["status"], "ALREADY_CORRECT")
        self.assertEqual(machine.calls, [])
    def test_persistent_failure_only_removes_own_runtime(self):
        machine = Machine()
        before = machine.snapshot()
        machine.fail = "persistent:+"
        self.assertEqual(machine.apply()["status"], "FAILED_RESTORED")
        self.assertEqual(machine.state, before)
        self.assertEqual(machine.calls[-1][3], "del")
    def test_failure_preserves_preexisting_runtime(self):
        machine = Machine()
        machine.state["kernel"].append({"dst": m.DEST, "gateway": m.VIA, "dev": m.DEV, "protocol": "static", "metric": 50})
        before = machine.snapshot()
        machine.fail = "persistent:+"
        self.assertEqual(machine.apply()["status"], "FAILED_RESTORED")
        self.assertEqual(machine.state, before)
        self.assertFalse(any(x[0] == "ip" for x in machine.calls))
    def test_probe_failure_rolls_back_only_added_persistent(self):
        machine = Machine()
        machine.state["kernel"].append({"dst": m.DEST, "gateway": m.VIA, "dev": m.DEV, "protocol": "static", "metric": 50})
        before = machine.snapshot()
        machine.probe_bad = True
        self.assertEqual(machine.apply()["status"], "FAILED_RESTORED")
        self.assertEqual(machine.state, before)
        self.assertFalse(any(x[0] == "ip" for x in machine.calls))
    def test_probe_failure_removes_persistent_before_runtime(self):
        machine = Machine()
        machine.probe_bad = True
        self.assertEqual(machine.apply()["status"], "FAILED_RESTORED")
        self.assertEqual([x[0] for x in machine.calls], ["ip", "nmcli", "nmcli", "ip"])
    def test_timeout_after_side_effect_does_not_claim_ownership(self):
        machine = Machine()
        machine.fail, machine.fail_after = "persistent:+", True
        result = machine.apply()
        self.assertEqual(result["status"], "PARTIAL_REVIEW_REQUIRED")
        self.assertFalse(result["recovery_complete"])
        self.assertEqual(len(machine.calls), 2)
    def test_drift_refuses_rollback(self):
        machine = Machine()
        machine.drift = True
        result = machine.apply()
        self.assertEqual(result["status"], "PARTIAL_REVIEW_REQUIRED")
        self.assertEqual(len(machine.calls), 2)
    def test_rollback_denied_is_not_recovery_pass(self):
        machine = Machine()
        machine.probe_bad, machine.fail = True, "persistent:-"
        result = machine.apply()
        self.assertEqual(result["status"], "PARTIAL_REVIEW_REQUIRED")
        self.assertFalse(result["recovery_complete"])
    def test_preimage_write_failure_prevents_mutation(self):
        machine = Machine()
        def broken(record):
            raise OSError("disk full")
        with self.assertRaises(OSError):
            m.apply(machine, machine.snapshot, broken, machine.probe)
        self.assertEqual(machine.calls, [])
    def test_drift_before_first_change_prevents_mutation(self):
        machine = Machine()
        count = 0
        def snapshot():
            nonlocal count
            count += 1
            if count > 1:
                machine.state["properties"]["ipv4.gateway"] = "10.1.93.59"
            return machine.snapshot()
        result = m.apply(machine, snapshot, machine.write, machine.probe)
        self.assertEqual(result["status"], "PARTIAL_REVIEW_REQUIRED")
        self.assertEqual(machine.calls, [])

class ValidationTests(unittest.TestCase):
    def test_conflicting_route_is_not_replaced(self):
        state = fixture()
        state["kernel"].append({"dst": m.DEST, "gateway": "10.1.93.254", "dev": m.DEV})
        with self.assertRaises(m.Stop):
            m.plan(state)
    def test_duplicate_exact_route_is_rejected(self):
        state = fixture()
        state["persistent"] += [{"ip": m.DEST, "nh": m.VIA, "mt": "100"}] * 2
        with self.assertRaises(m.Stop):
            m.plan(state)
    def test_more_specific_route_is_rejected(self):
        state = fixture()
        state["kernel"].append({"dst": "10.250.10.30/32", "gateway": m.VIA, "dev": m.DEV})
        with self.assertRaises(m.Stop):
            m.plan(state)
    def test_nm_parser_preserves_unrelated_attributes(self):
        raw = "192.0.2.0/24 10.1.93.59 80 table=200, 10.250.10.0/24 10.1.93.58 100 table=254"
        self.assertEqual(len(m.parse_routes(raw)), 2)
        self.assertTrue(any(x.get("table") == "200" for x in m.parse_routes(raw)))
        self.assertTrue(any(x.get("table") == "254" for x in m.parse_routes(raw)))
    def test_existing_implicit_profile_zero_route_requires_review(self):
        state = fixture()
        state["persistent"].append({"ip": m.DEST, "nh": m.VIA, "mt": "100"})
        with self.assertRaisesRegex(m.Stop, "implicit persistent table"):
            m.plan(state)
    def test_owned_persistent_table_change_refuses_compensation(self):
        before = fixture()
        after = copy.deepcopy(before)
        after["persistent"].append({"ip": m.DEST, "nh": m.VIA, "mt": "100", "table": "200"})
        with self.assertRaises(m.Stop):
            m.expected_state(before, after, {"runtime": False, "persistent": True}, 100)
    def test_unrecognized_nm_output_is_rejected(self):
        for raw in ("10.250.10.0/24 10.1.93.58 nonsense", "{ip = x}", "{ ip = 10.250.10.0/24, nh = 10.1.93.58, mt = 100 table=254 }", "10.0.0.0/8 table=254 table=200", "10.0.0.0/8 unknown=5"):
            with self.subTest(raw=raw), self.assertRaises((m.Stop, ValueError)):
                m.parse_routes(raw)
    def test_owned_runtime_source_drift_is_rejected(self):
        before = fixture()
        after = copy.deepcopy(before)
        after["kernel"].append({"dst": m.DEST, "gateway": m.VIA, "dev": m.DEV, "protocol": "static", "metric": 100, "prefsrc": "10.1.93.99"})
        with self.assertRaises(m.Stop):
            m.expected_state(before, after, {"runtime": True, "persistent": False}, 100)
    def test_command_timeout_and_stderr_not_exposed(self):
        with patch.object(m.subprocess, "run", side_effect=subprocess.TimeoutExpired("nmcli", 20)):
            with self.assertRaisesRegex(m.Stop, "completion unknown"):
                m.Commands()("nmcli", "-g", "ipv4.routes")
        with patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "SECRET")):
            with self.assertRaises(m.Stop) as cm:
                m.Commands()("ip", "-j", "route")
            self.assertNotIn("SECRET", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
