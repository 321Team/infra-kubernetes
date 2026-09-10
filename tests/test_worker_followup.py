import importlib.util
import json
from pathlib import Path
import socket
import stat
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("controller", ROOT / "scripts/run-worker-followup.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
node = controller.node


def inventory():
    return {"k8s_control_plane": {"hosts": list(node.CP)}, "k8s_workers": {"hosts": list(node.WORKERS)},
            "_meta": {"hostvars": {name: {"ansible_host": ip, "ansible_user": "svc_ansible"} for name, ip in node.NODES.items()}}}


class ScopeTests(unittest.TestCase):
    def test_fixed_tcp_matrix(self):
        for name in node.CP:
            self.assertEqual([(ip, port) for _, ip, port, _ in node.probe_targets(name)], [(ip, 10250) for ip in node.WORKERS.values()])
        for name in node.WORKERS:
            self.assertEqual(node.probe_targets(name), [("k8s-lb01", "10.1.93.109", 6443, "IF-11A")])

    def test_unknown_node_or_mutating_mode_stops_before_queries(self):
        with patch.object(node, "command") as query:
            for request in ({"node": "other", "mode": "inspect"}, {"node": "k8s-control01", "mode": "apply"}):
                with self.assertRaises(ValueError):
                    node.collect(request)
            query.assert_not_called()

    def test_wrong_interface_address_preserves_identity_and_stops(self):
        reply = {"rc": 0, "stdout": json.dumps([{"addr_info": [{"family": "inet", "local": "192.0.2.1"}]}]), "stderr": ""}
        with patch.object(node.os, "geteuid", return_value=0), patch.object(node.socket, "gethostname", return_value="k8s-control01"), patch.object(node, "command", return_value=reply) as query, patch.object(node, "tcp_probe") as probe:
            result = node.collect({"node": "k8s-control01", "mode": "inspect"})
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(len(result["queries"]), 1)
        query.assert_called_once()
        probe.assert_not_called()

    def test_cp_has_only_time_queries_and_worker_probes(self):
        def query(argv):
            stdout = json.dumps([{"addr_info": [{"family": "inet", "local": "10.1.93.102"}]}]) if argv[0] == "ip" else ""
            return {"rc": 0, "stdout": stdout, "stderr": ""}
        with patch.object(node.os, "geteuid", return_value=0), patch.object(node.socket, "gethostname", return_value="k8s-control01"), patch.object(node, "command", side_effect=query), patch.object(node, "chrony_config", return_value={"gaps": []}), patch.object(node, "tcp_probe", return_value={"connected": False}):
            result = node.collect({"node": "k8s-control01", "mode": "inspect"})
        self.assertEqual(set(result["queries"]), {"identity_ipv4", "chrony_tracking", "chrony_sources_numeric", "chrony_sources_original", "time_state", "chronyd_state"})
        self.assertEqual(len(result["tcp_probes"]), 3)
        self.assertEqual(result["status"], "COLLECTED_REVIEW_REQUIRED")

    def test_inventory_rejects_wrong_group_and_address(self):
        controller.validate_inventory(inventory())
        for modification in ("group", "address", "user"):
            data = inventory()
            if modification == "group":
                data["k8s_workers"]["hosts"].pop()
            else:
                data["_meta"]["hostvars"]["k8s-worker01"]["ansible_host" if modification == "address" else "ansible_user"] = "unexpected"
            with self.assertRaises(ValueError):
                controller.validate_inventory(data)

    def test_ssh_pins_identity_source_and_host_keys(self):
        argv = controller.ssh_argv("k8s-worker01", ["sudo", "-n", "/usr/bin/python3", "-"])
        for value in ("StrictHostKeyChecking=yes", "IdentityAgent=none", "IdentitiesOnly=yes", "10.250.10.10", str(controller.KEY), "svc_ansible@10.1.93.105"):
            self.assertIn(value, argv)
        with self.assertRaises(ValueError):
            controller.ssh_argv("other", [])


class EvidenceTests(unittest.TestCase):
    def test_config_excludes_secrets_and_options(self):
        content = 'server ntp.example.invalid iburst key 123\nkeyfile /etc/chrony.keys\npassword secret\ninclude /unapproved/path\nsourcedir /run/chrony-dhcp\n'
        result = node.source_lines(content)
        rendered = json.dumps(result)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("chrony.keys", rendered)
        self.assertNotIn("123", rendered)
        self.assertEqual(result[0]["arguments"], ["ntp.example.invalid"])
        self.assertEqual(result[1]["arguments"], ["/unapproved/path"])

    def test_untrusted_file_and_symlink_rejected(self):
        for mode in (stat.S_IFREG | 0o666, stat.S_IFLNK | 0o777):
            def info(path):
                return SimpleNamespace(st_mode=mode if str(path) == "/etc/chrony.conf" else stat.S_IFDIR | 0o755, st_uid=0)
            with patch.object(Path, "lstat", info), self.assertRaises(ValueError):
                node.trusted(Path("/etc/chrony.conf"))

    def test_unresolved_include_recorded_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "chrony.conf"
            config.write_text("server ntp.example.invalid\ninclude /never-read/keys\n")
            real_path = Path
            def mapped(value):
                return config if value == "/etc/chrony.conf" else real_path(value)
            with patch.object(node, "CONFIG_DIRS", ()), patch.object(node, "Path", side_effect=mapped), patch.object(node, "trusted", return_value=config.stat()):
                result = node.chrony_config()
            self.assertEqual(list(result["files"]), [str(config)])
            self.assertTrue(any("unresolved configuration reference" in gap for gap in result["gaps"]))

    def test_timeout_retains_redacted_partial_output(self):
        error = subprocess.TimeoutExpired(["chronyc"], 15, output=b"partial token=abcdef.0123456789abcdef", stderr=b"waiting")
        with patch.object(node, "binary", return_value="/usr/bin/chronyc"), patch.object(node.subprocess, "run", side_effect=error):
            result = node.command(["chronyc", "-n", "tracking"])
        self.assertEqual(result["rc"], 124)
        self.assertIn("partial", result["stdout"])
        self.assertNotIn("abcdef.0123456789abcdef", result["stdout"])
        self.assertIn("waiting", result["stderr"])

    def test_truncated_query_is_visible(self):
        proc = SimpleNamespace(returncode=0, stdout="x" * (node.MAX_BYTES + 1), stderr="")
        with patch.object(node, "binary", return_value="/usr/sbin/nft"), patch.object(node.subprocess, "run", return_value=proc):
            result = node.command(["nft", "list", "ruleset"])
        self.assertTrue(result["stdout_truncated"])

    def test_tcp_failure_preserves_errno(self):
        with patch.object(socket, "create_connection", side_effect=ConnectionRefusedError(111, "Connection refused")):
            result = node.tcp_probe(node.probe_targets("k8s-control01")[0])
        self.assertFalse(result["connected"])
        self.assertEqual(result["errno"], 111)

    def test_collection_completeness_is_distinct_from_connectivity(self):
        targets = {name: {"transport": "PASS", "status": "COLLECTED_REVIEW_REQUIRED", "tcp_connected": 0} for name in node.NODES}
        self.assertEqual(controller.collection_status(targets), "COLLECTED_REVIEW_REQUIRED")
        targets["k8s-control01"]["failed_queries"] = ["chrony_tracking"]
        self.assertEqual(controller.collection_status(targets), "INCOMPLETE_REVIEW_REQUIRED")
        self.assertEqual(controller.collection_status({}), "INCOMPLETE_REVIEW_REQUIRED")

    def test_failed_preflight_still_emits_verifiable_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "evidence"
            out.mkdir()
            with patch.object(controller.tempfile, "mkdtemp", return_value=str(out)), patch.object(controller, "verify_source", side_effect=ValueError("source mismatch")), patch("sys.argv", ["run-worker-followup.py"]):
                with self.assertRaises(SystemExit) as stop:
                    controller.main()
            self.assertEqual(stop.exception.code, 2)
            archive = out.with_suffix(".tar.gz")
            self.assertEqual(Path(str(archive) + ".sha256").read_text().split()[0], controller.digest(archive))
            manifest = (out / "SHA256SUMS").read_text().splitlines()
            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest[0].split()[0], controller.digest(out / "RESULT.json"))
            with tarfile.open(archive) as tar:
                self.assertEqual({x.name for x in tar.getmembers() if x.isfile()}, {"evidence/RESULT.json", "evidence/SHA256SUMS"})

    def test_source_lock_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ["scripts/run-worker-followup.py", "scripts/worker-followup-node.py", "docs/runbooks/WORKER_NTP_FOLLOWUP.md"]
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original")
            lock = {"repository": "321Team/infra-kubernetes", "commit": "a" * 40, "files": {name: controller.digest(root / name) for name in names}}
            (root / "SOURCE_LOCK.json").write_text(json.dumps(lock))
            with patch.object(controller, "KIT", root):
                self.assertEqual(controller.verify_source(), "a" * 40)
                (root / names[0]).write_text("modified")
                with self.assertRaises(ValueError):
                    controller.verify_source()


if __name__ == "__main__":
    unittest.main()
