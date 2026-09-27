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
                self.state["kernel"].append({"dst": m.DEST, "gateway": m.VIA, "dev": argv[argv.index("dev") + 1], "protocol": "static", "metric": metric})
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
        return [{"gateway": "10.1.93.254" if self.probe_bad else m.VIA, "dev": self.state["device"], "prefsrc": self.state["ip"]}]
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
        machine.state["kernel"].append({"dst": m.DEST, "gateway": m.VIA, "dev": "ens160", "protocol": "static", "metric": 50})
        before = machine.snapshot()
        machine.fail = "persistent:+"
        self.assertEqual(machine.apply()["status"], "FAILED_RESTORED")
        self.assertEqual(machine.state, before)
        self.assertFalse(any(x[0] == "ip" for x in machine.calls))
    def test_probe_failure_rolls_back_only_added_persistent(self):
        machine = Machine()
        machine.state["kernel"].append({"dst": m.DEST, "gateway": m.VIA, "dev": "ens160", "protocol": "static", "metric": 50})
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
        state["kernel"].append({"dst": m.DEST, "gateway": "10.1.93.254", "dev": "ens160"})
        with self.assertRaises(m.Stop):
            m.plan(state)
    def test_duplicate_exact_route_is_rejected(self):
        state = fixture()
        state["persistent"] += [{"ip": m.DEST, "nh": m.VIA, "mt": "100"}] * 2
        with self.assertRaises(m.Stop):
            m.plan(state)
    def test_more_specific_route_is_rejected(self):
        state = fixture()
        state["kernel"].append({"dst": "10.250.10.30/32", "gateway": m.VIA, "dev": "ens160"})
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
        after["kernel"].append({"dst": m.DEST, "gateway": m.VIA, "dev": "ens160", "protocol": "static", "metric": 100, "prefsrc": "10.1.93.99"})
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


STORAGE_UUID = "22345678-1234-1234-1234-123456789abc"

def db_fixture(runtime=True, persistent=True):
    state = fixture()
    state.update(host="db01", ip="10.1.93.55", device="ens192", addresses=[("10.1.93.55", 24)],
                 bindings={UUID: "ens192", STORAGE_UUID: "ens160"})
    state["kernel"] = [
        {"dst": "default", "gateway": "10.1.93.254", "dev": "ens192", "protocol": "static", "metric": 101},
        {"dst": "10.1.93.0/24", "dev": "ens192", "protocol": "kernel", "scope": "link", "prefsrc": "10.1.93.55", "metric": 101},
        {"dst": "10.250.40.0/24", "dev": "ens160", "protocol": "kernel", "scope": "link", "prefsrc": "10.250.40.20", "metric": 100},
    ]
    if runtime:
        state["kernel"].append({"dst": m.DEST, "gateway": m.VIA, "dev": "ens192", "protocol": "static", "metric": 101})
    if persistent:
        state["persistent"].append({"ip": m.DEST, "nh": m.VIA, "mt": "101", "table": "254"})
    return state

class DbReadOnlyCommands:
    """Two active NICs with distinct profiles; reject every unmodelled command."""
    def __init__(self):
        self.calls = []
        self.state = db_fixture()
        self.addresses = [dict(ifname="ens192", flags=["UP"],
                               addr_info=[dict(local="10.1.93.55", prefixlen=24)])]
        self.props = {p: "--" for p in m.PROPERTIES}
        self.props.update({"connection.uuid": UUID, "connection.type": "802-3-ethernet",
                           "connection.interface-name": "ens192", "connection.autoconnect": "yes",
                           "ipv4.method": "manual", "ipv4.addresses": "10.1.93.55/24",
                           "ipv4.gateway": "10.1.93.254", "ipv4.route-table": "254"})
    def __call__(self, *argv):
        self.calls.append(argv)
        if argv == ("ip", "-j", "-4", "address", "show", "dev", "ens192"):
            return json.dumps(self.addresses)
        if argv == ("ip", "-j", "-4", "route", "show", "table", "all"):
            return json.dumps(self.state["kernel"])
        if argv == ("ip", "-j", "-4", "rule", "show"):
            return json.dumps([dict(priority=p, src="all", table=t)
                               for p,t in [(0,"local"),(32766,"main"),(32767,"default")]])
        if argv == ("nmcli", "-g", "GENERAL.NM-MANAGED", "device", "show", "ens192"):
            return "yes"
        if argv == ("nmcli", "-g", "GENERAL.STATE", "device", "show", "ens192"):
            return "100 (connected)"
        if argv == ("nmcli", "-t", "-f", "UUID,DEVICE", "connection", "show", "--active"):
            return STORAGE_UUID + ":ens160\n" + UUID + ":ens192"
        if argv == ("nmcli", "-g", "UUID", "connection", "show"):
            return STORAGE_UUID + "\n" + UUID
        if len(argv) == 7 and argv[:2] == ("nmcli", "-g") and argv[3:6] == ("connection", "show", "uuid"):
            prop, identity = argv[2], argv[6]
            if identity == STORAGE_UUID:
                return {"connection.type": "802-3-ethernet", "connection.interface-name": "ens160"}[prop]
            if identity == UUID:
                if prop == "ipv4.routes":
                    return "10.250.10.0/24 10.1.93.58 101 table=254"
                return self.props[prop]
        raise AssertionError("Unexpected command: " + str(argv))

class DeviceBindingTests(unittest.TestCase):
    def test_collect_db_selects_ens192_profile_with_storage_ens160_present(self):
        commands = DbReadOnlyCommands()
        state = m.collect(commands, "db01")
        self.assertEqual(state["profile"], UUID)
        self.assertEqual(state["device"], "ens192")
        self.assertEqual(state["bindings"], {UUID: "ens192", STORAGE_UUID: "ens160"})
        self.assertEqual(state["addresses"], [("10.1.93.55", 24)])
        self.assertIn({"dst": "10.250.40.0/24", "dev": "ens160", "protocol": "kernel", "scope": "link", "prefsrc": "10.250.40.20", "metric": 100}, state["kernel"])
        self.assertFalse(m.plan(state)["add_runtime"])
        self.assertFalse(m.plan(state)["add_persistent"])
    def test_collect_db_wrong_nic_output_stops_before_mutation(self):
        commands = DbReadOnlyCommands()
        commands.addresses[0].update(ifname="ens160", addr_info=[dict(local="10.250.40.20", prefixlen=24)])
        with self.assertRaisesRegex(m.Stop, "Unexpected interface"):
            m.collect(commands, "db01")
        self.assertEqual(len(commands.calls), 1)
    def test_collect_db_wrong_address_stops_before_mutation(self):
        commands = DbReadOnlyCommands()
        commands.addresses[0]["addr_info"][0]["local"] = "10.250.40.20"
        with self.assertRaisesRegex(m.Stop, "Expected single host IPv4"):
            m.collect(commands, "db01")
        self.assertEqual(len(commands.calls), 1)
    def test_collect_db_profile_bound_to_storage_is_rejected(self):
        commands = DbReadOnlyCommands()
        commands.props["connection.interface-name"] = "ens160"
        with self.assertRaisesRegex(m.Stop, "explicitly bind ethernet ens192"):
            m.collect(commands, "db01")
        self.assertFalse(any("modify" in call for call in commands.calls))
    def test_db_existing_ens192_route_unchanged(self):
        machine = Machine(db_fixture())
        before = machine.snapshot()
        result = machine.apply()
        self.assertEqual(result["status"], "ALREADY_CORRECT")
        self.assertEqual(result["plan"]["device"], "ens192")
        self.assertEqual(machine.state, before)
        self.assertEqual(machine.calls, [])
    def test_db_existing_runtime_only_updates_selected_profile(self):
        machine = Machine(db_fixture(persistent=False))
        before = machine.snapshot()
        self.assertEqual(machine.apply()["status"], "APPLIED")
        self.assertEqual(machine.state["kernel"], before["kernel"])
        self.assertEqual(len(machine.calls), 1)
        self.assertEqual(machine.calls[0], m.profile_args("+", UUID, 101))
    def test_db_missing_runtime_uses_ens192_and_saved_metric(self):
        machine = Machine(db_fixture(runtime=False))
        before = machine.snapshot()
        self.assertEqual(machine.apply()["status"], "APPLIED")
        self.assertEqual(machine.calls, [("ip", "-4", "route", "add", "10.250.10.0/24", "via", "10.1.93.58", "dev", "ens192", "proto", "static", "metric", "101", "table", "main")])
        self.assertEqual(machine.state["persistent"], before["persistent"])
        self.assertEqual(m.without_target(machine.state), m.without_target(before))
    def test_db_compensation_removes_only_own_ens192_addition(self):
        machine = Machine(db_fixture(runtime=False, persistent=False))
        before = machine.snapshot()
        machine.fail = "persistent:+"
        self.assertEqual(machine.apply()["status"], "FAILED_RESTORED")
        self.assertEqual(machine.state, before)
        runtime_calls = [c for c in machine.calls if c[0] == "ip"]
        self.assertEqual([c[3] for c in runtime_calls], ["add", "del"])
        self.assertTrue(all(c[c.index("dev") + 1] == "ens192" for c in runtime_calls))
        self.assertTrue(all(c[c.index("metric") + 1] == "100" for c in runtime_calls))
    def test_db_probe_on_wrong_device_does_not_pass(self):
        machine = Machine(db_fixture(runtime=False, persistent=False))
        before = machine.snapshot()
        def wrong_probe():
            return [{"gateway": m.VIA, "dev": "ens160", "prefsrc": "10.1.93.55"}]
        result = m.apply(machine, machine.snapshot, machine.write, wrong_probe)
        self.assertEqual(result["status"], "FAILED_RESTORED")
        self.assertEqual(machine.state, before)
        self.assertEqual([c[0] for c in machine.calls], ["ip", "nmcli", "nmcli", "ip"])
    def test_db_unexpected_device_binding_stops_before_mutation(self):
        machine = Machine(db_fixture(runtime=False, persistent=False))
        machine.state["device"] = "ens160"
        with self.assertRaisesRegex(m.Stop, "Host/IP/device binding mismatch"):
            machine.apply()
        self.assertEqual(machine.calls, [])
        self.assertEqual(machine.records, [])
    def test_db_existing_implicit_table_remains_blocked(self):
        machine = Machine(db_fixture())
        machine.state["persistent"][-1].pop("table")
        with self.assertRaisesRegex(m.Stop, "implicit persistent table"):
            machine.apply()
        self.assertEqual(machine.calls, [])
    def test_edge_and_harbor_still_use_ens160(self):
        for host, ip in (("edge01", "10.1.93.50"), ("harbor01", "10.1.93.54")):
            with self.subTest(host=host):
                state = fixture()
                state.update(host=host, ip=ip, addresses=[(ip, 24)])
                machine = Machine(state)
                result = machine.apply()
                self.assertEqual(result["status"], "APPLIED")
                self.assertEqual(result["plan"]["device"], "ens160")
                self.assertEqual(machine.calls[0][machine.calls[0].index("dev") + 1], "ens160")

if __name__ == "__main__":
    unittest.main()
