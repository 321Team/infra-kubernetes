import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


node = load("prejoin_node", "prejoin_node_read.py")
controller = load("prejoin_controller", "controller_template.py")
adapter = load("prejoin_adapter", "cp1_reuse_adapter.py")


class ReadOnlyTests(unittest.TestCase):
    def test_secret_flags_and_userinfo_withheld(self):
        flags = node.safe_flags('--token=SECRET --certificate-key=PRIVATE --config=/var/lib/kubelet/config.yaml '
                                '--etcd-servers=https://user:SECRET@10.1.93.102:2379')
        encoded = json.dumps(flags)
        self.assertNotIn("SECRET", encoded)
        self.assertNotIn("PRIVATE", encoded)
        self.assertIn("UNPARSED_VALUE_WITHHELD", encoded)
        self.assertIn("/var/lib/kubelet/config.yaml", encoded)

    def test_leaf_and_parent_symlinks_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            path = Path(tmp)
            (path / "source").write_text("secret")
            (path / "link").symlink_to(path / "source")
            with self.assertRaises((ValueError, OSError)):
                node.read_owned_text(path / "link")
            (path / "dirlink").symlink_to(path, target_is_directory=True)
            result = node.directory_inventory(path / "dirlink")
            self.assertIn("error", result)
            self.assertEqual(result["files"], [])

    def test_invalid_identity_runs_nothing(self):
        with patch.object(node, "run") as run:
            with self.assertRaises(ValueError):
                node.collect("k8s-worker01", "10.1.93.105")
            run.assert_not_called()

    def test_missing_version_not_success(self):
        with patch.object(node, "run", return_value={"rc": 0, "stdout": "", "stderr_bytes": 0}):
            self.assertEqual(node.scalar_version(["kubelet", "--version"])["parse"], "INCOMPLETE")

    def test_package_markers_not_execution_proof_and_no_secret_output(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            path = Path(tmp)
            (path / "prepare.py").write_text('run(["kubeadm", "join", "--token", "DO_NOT_EXPORT"])\n')
            out = node.package_provenance(path)
            self.assertNotIn("DO_NOT_EXPORT", json.dumps(out))
            self.assertFalse(out["markers_are_execution_history"])
            self.assertEqual(out["relationship_to_static_manifests"], "NOT_ESTABLISHED")
            self.assertEqual(out["files"][0]["text_markers"]["kubeadm_join"], "FOUND_IN_TEXT")

    def test_ssh_fixed_identity_and_no_agent(self):
        with patch.object(controller.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            controller.ssh_read("k8s-control02", "pass", 12, ("k8s-control02", "10.1.93.103"))
        argv = run.call_args.args[0]
        self.assertIn("10.250.10.10", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("IdentityAgent=none", argv)
        self.assertIn("ForwardAgent=no", argv)
        self.assertEqual(argv[-1], "sudo -n /usr/bin/python3 -I -B - k8s-control02 10.1.93.103")
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_cp1_source_mismatch_never_executes_helper(self):
        calls = []
        def callback(payload, timeout):
            calls.append(payload)
            return types.SimpleNamespace(returncode=2, stdout="", stderr="secret")
        out = adapter.read_cp1(b"wrong", b"wrong", callback)
        self.assertEqual(len(calls), 1)  # independently attempted fixed status, not bad helper
        self.assertNotIn("secret", json.dumps(out))
        self.assertFalse(out["topology"]["ok"])

    def test_cp1_failure_still_collects_both_nodes_and_partial_archive(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp = Path(tmp)
            (tmp / "key").write_text("fixture-not-a-key")
            (tmp / "hosts").write_text("fixture-not-a-hostkey")
            output = tmp / "ICR_CP_Prejoin_fixture"
            output.mkdir()
            seen = []
            def ssh(name, payload, timeout, args):
                seen.append(name)
                body = {"schema": "fds.cp-prejoin-observation.v1", "hostname": name,
                        "ip": controller.TARGETS[name], "configuration_mutation": False}
                return types.SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
            fake_adapter = types.SimpleNamespace(read_cp1=lambda *args: {
                "topology": {"ok": False}, "status": {"ok": False, "status": "INCOMPLETE"}})
            record = list(controller.pwd.getpwuid(controller.os.getuid()))
            record[0] = "server01_ansible"
            fake_user = controller.pwd.struct_passwd(record)
            with patch.object(controller.sys, "argv", ["collector"]), \
                 patch.object(controller.pwd, "getpwuid", return_value=fake_user), \
                 patch.object(controller, "KEY", tmp / "key"), \
                 patch.object(controller, "KNOWN_HOSTS", tmp / "hosts"), \
                 patch.object(controller.tempfile, "mkdtemp", return_value=str(output)), \
                 patch.object(controller, "NODE_PAYLOAD_B64", base64.b64encode(b"# fixture").decode()), \
                 patch.object(controller, "APPROVED_HELPER_B64", ""), \
                 patch.object(controller, "APPROVED_POLICY_B64", ""), \
                 patch.object(controller, "load_adapter", return_value=fake_adapter), \
                 patch.object(controller, "ssh_read", side_effect=ssh), \
                 patch("builtins.print"):
                self.assertEqual(controller.main(), 2)
            self.assertEqual(seen, ["k8s-control02", "k8s-control03"])
            state = json.loads((output / "RUN_STATE.json").read_text())
            self.assertEqual(state["status"], "PARTIAL_OBSERVATIONS")
            self.assertEqual(state["join_readiness"], "NOT_ASSESSED")
            archive = output.with_suffix(".tar.gz")
            sidecar = Path(str(archive) + ".sha256").read_text().split()[0]
            self.assertEqual(sidecar, hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
