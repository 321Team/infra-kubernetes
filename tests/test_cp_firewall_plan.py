import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cp_plan", ROOT / "scripts/build-cp-firewall-plan.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
POLICY = json.loads((ROOT / "policies/icr-d-net-01-01/cp-firewall-v1.json").read_text())
LB_RULE = 'rule family="ipv4" source address="10.1.93.109/32" port port="6443" protocol="tcp" accept'


def zone(plane, extra=""):
    return ("public (default)\n  target: default\n  interfaces: " +
            ("ens160" if plane == "runtime" else "") +
            "\n  sources: \n  services: dhcpv6-client ssh\n  ports: \n  protocols: \n" +
            "  rich rules:\n    " + LB_RULE + "\n" + extra)


def evidence():
    summary = {"status": "COLLECTED_REVIEW_REQUIRED", "ansible_returncode": 0,
               "collected_nodes": 3, "expected_nodes": 3, "kit_version": "1.1",
               "repo_head": module.INVENTORY, "ended_at": POLICY["evidence"]["ended_at"],
               "nodes": {n: {"status": "COLLECTED_REVIEW_REQUIRED"} for n in module.TARGETS}}
    files = {"COLLECTION_SUMMARY.json": json.dumps(summary).encode(),
             "controller_context.json": b"{}", "collection_parameters.json": b"{}",
             "ansible_collection.log": b"fixture", "ansible_syntax_check.log": b"fixture"}
    for name, ip in module.TARGETS.items():
        node = {"status": "COLLECTED_REVIEW_REQUIRED", "transport_rc": 0,
                "identity": {"hostname": name, "expected_ip": ip, "uid": 0},
                "preflight": {stage: {"rc": 0, "stdout": value} for stage, value in
                              [("ssh", "svc_ansible\n"), ("module", ""), ("become", "0")]},
                "queries": {"unit_state": {"rc": 0, "stdout": "Id=firewalld.service\nActiveState=active\nSubState=running\n"},
                            "firewall_active_zones": {"rc": 0, "stdout": "public (default)\n  interfaces: ens160\n"}}}
        for plane in ["permanent", "runtime"]:
            node["queries"]["firewall_public_" + plane] = {"rc": 0, "stdout": zone(plane)}
        files[name + ".json"] = json.dumps(node).encode()
    return files


def change_node(files, update):
    key = "k8s-control01.json"
    node = json.loads(files[key])
    update(node)
    files[key] = json.dumps(node).encode()


def archive_bytes(files, extra=None, corrupt=False, omit=None):
    checks = "".join(module.digest(data) + "  " + name + "\n" for name, data in files.items() if name != omit)
    payload = dict(files, SHA256SUMS=checks.encode())
    if corrupt:
        payload["ansible_collection.log"] += b"tampered"
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for name, data in payload.items():
            info = tarfile.TarInfo("bundle/" + name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if extra:
            info = tarfile.TarInfo(extra[0])
            if extra[1] == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
            archive.addfile(info, io.BytesIO(b""))
    return result.getvalue()


class PlanTests(unittest.TestCase):
    def test_exact_contract_and_preserve_existing_ssh_lb(self):
        plan = module.build_plan(POLICY, evidence())
        self.assertEqual(plan["candidate_add_count"], 36)
        self.assertFalse(plan["runtime_applied"])
        for node in plan["nodes"].values():
            for plane, ops in node["operations"].items():
                self.assertEqual(len(ops), 6)
                self.assertEqual(node["before"][plane]["rich_rules"], [LB_RULE])
                self.assertIn("ssh", node["before"][plane]["fields"]["services"])
                for op in ops:
                    self.assertNotIn("/24", op["rule"])
                    self.assertNotIn("6443", op["rule"])
                    self.assertNotIn("--timeout", " ".join(op["add_argv"]))

    def test_existing_rule_in_one_plane_is_preserved_and_never_removed(self):
        files = evidence()
        rule = module.wanted_rules()[0][1]
        change_node(files, lambda n: n["queries"]["firewall_public_permanent"].update(stdout=zone("permanent", rule + "\n")))
        plan = module.build_plan(POLICY, files)
        self.assertEqual(plan["candidate_add_count"], 35)
        op = plan["nodes"]["k8s-control01"]["operations"]["permanent"][0]
        self.assertEqual(op["action"], "preserve")
        self.assertIsNone(op["add_argv"])
        self.assertIsNone(op["rollback_argv"])

    def test_broad_source_or_extra_port_rejected(self):
        for field, value in [("sources", ["10.1.93.0/24"]), ("ports", module.PORTS + [{"port": "6443"}])]:
            policy = copy.deepcopy(POLICY)
            policy[field] = value
            with self.assertRaises(ValueError):
                module.build_plan(policy, evidence())

    def test_wrong_node_identity_rejected(self):
        files = evidence()
        change_node(files, lambda n: n["identity"].update(expected_ip="10.1.93.105"))
        with self.assertRaisesRegex(ValueError, "identity"):
            module.build_plan(POLICY, files)

    def test_partial_or_censored_transport_rejected(self):
        files = evidence()
        change_node(files, lambda n: n["preflight"]["ssh"].update(unreachable=True))
        with self.assertRaisesRegex(ValueError, "preflight"):
            module.build_plan(POLICY, files)

    def test_zone_or_truncated_output_rejected(self):
        for update in [lambda n: n["queries"]["firewall_active_zones"].update(stdout="trusted\n  interfaces: ens160\n"),
                       lambda n: n["queries"]["firewall_public_runtime"].update(stdout_truncated=True)]:
            files = evidence()
            change_node(files, update)
            with self.assertRaises(ValueError):
                module.build_plan(POLICY, files)

    def test_no_management_ssh_rejected(self):
        files = evidence()
        change_node(files, lambda n: n["queries"]["firewall_public_runtime"].update(stdout=zone("runtime").replace("dhcpv6-client ssh", "dhcpv6-client")))
        with self.assertRaisesRegex(ValueError, "SSH"):
            module.build_plan(POLICY, files)

    def read_fixture(self, raw, bad_sidecar=False):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "test.tar.gz"
            sidecar = Path(directory) / "test.tar.gz.sha256"
            archive.write_bytes(raw)
            sidecar.write_text(("0" * 64 if bad_sidecar else module.digest(raw)) + "  test.tar.gz\n")
            return module.read_archive(archive, sidecar, module.digest(raw))

    def test_archive_checksum_coverage(self):
        files = evidence()
        self.assertEqual(self.read_fixture(archive_bytes(files))["COLLECTION_SUMMARY.json"], files["COLLECTION_SUMMARY.json"])
        with self.assertRaisesRegex(ValueError, "incomplete checksum"):
            self.read_fixture(archive_bytes(files, omit="ansible_collection.log"))

    def test_tampering_and_bad_sidecar_rejected(self):
        with self.assertRaisesRegex(ValueError, "internal checksum"):
            self.read_fixture(archive_bytes(evidence(), corrupt=True))
        with self.assertRaisesRegex(ValueError, "sidecar"):
            self.read_fixture(archive_bytes(evidence()), bad_sidecar=True)

    def test_archive_traversal_and_symlink_rejected(self):
        for name, kind in [("../outside", "file"), ("bundle/link", "link")]:
            with self.assertRaises(ValueError):
                self.read_fixture(archive_bytes(evidence(), extra=(name, kind)))

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            module.parse_json('{"source":"10.1.93.102/32","source":"10.1.93.0/24"}')


if __name__ == "__main__":
    unittest.main()
