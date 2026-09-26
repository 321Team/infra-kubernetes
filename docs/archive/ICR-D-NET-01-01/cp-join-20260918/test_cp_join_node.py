import hashlib
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("cp_join_node", pathlib.Path(__file__).with_name("cp_join_node.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class NodeTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.manifests = self.root / "etc/kubernetes/manifests"
        self.manifests.mkdir(parents=True, mode=0o755)
        self.content = {name: ("test manifest " + name).encode() for name in m.MANIFESTS}
        self.hashes = {name: hashlib.sha256(value).hexdigest() for name, value in self.content.items()}
        self.manifest_patch = mock.patch.object(m, "MANIFESTS", self.hashes)
        self.manifest_patch.start()
        self.host_patch = mock.patch.object(m.Node, "host_checks", return_value={"kubeadm": "v1.36.4"})
        self.host_patch.start()
        self.commands = []

    def tearDown(self):
        self.host_patch.stop()
        self.manifest_patch.stop()
        self.tmp.cleanup()

    def node(self, name="k8s-control02", mode="prepare", runner=None):
        req = {"node": name, "mode": mode, "job_id": "a123456789abcdef"}
        return m.Node(req, root=str(self.root), command=runner or self.fake_run)

    def seed(self):
        for name, content in self.content.items():
            path = self.manifests / name
            path.write_bytes(content)
            path.chmod(0o600)
        pki = self.root / "etc/kubernetes/pki"
        pki.mkdir(mode=0o700)
        (pki / "ca.key").write_text("TEST PRIVATE MATERIAL DO NOT SHARE")
        (pki / "ca.key").chmod(0o600)

    def fake_run(self, argv, timeout):
        self.commands.append(argv)
        if argv[0] == "firewall-cmd":
            value = "running" if argv[1] == "--state" else "public" if argv[1] == "--get-zone-of-interface=ens160" else "yes"
            return {"rc": 0, "stdout": value + "\n", "stderr": ""}
        if argv[:3] == ["systemctl", "is-active", "kubelet"]:
            return {"rc": 3, "stdout": "inactive\n", "stderr": ""}
        return {"rc": 0, "stdout": "", "stderr": ""}

    def config(self, name="k8s-control02"):
        return {"apiVersion": "kubeadm.k8s.io/v1beta4", "kind": "JoinConfiguration",
                "nodeRegistration": {"name": name, "criSocket": m.ENDPOINT},
                "discovery": {"bootstrapToken": {"apiServerEndpoint": "10.1.93.109:6443",
                   "token": "abcdef.0123456789abcdef", "caCertHashes": ["sha256:" + "a" * 64]}},
                "controlPlane": {"localAPIEndpoint": {"advertiseAddress": m.TARGETS[name], "bindPort": 6443},
                                 "certificateKey": "b" * 64}}

    def test_hash_mismatch_causes_no_service_or_configuration_change(self):
        self.seed()
        path = self.manifests / "kube-apiserver.yaml"
        path.write_text("changed after observation")
        node = self.node()
        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            node.dispatch()
        self.assertEqual(len(list(self.manifests.iterdir())), 3)
        self.assertFalse(node.work.exists())
        self.assertFalse(any(c[:2] == ["systemctl", "stop"] for c in self.commands))

    def test_verified_private_backup_precedes_stop_and_quarantine(self):
        self.seed()
        node = self.node()
        actual = self.fake_run
        def command(argv, timeout):
            if argv[:3] == ["systemctl", "stop", "kubelet"]:
                self.assertEqual(node.read_state()["state"], "CLEANUP_STARTED")
                for name in self.hashes:
                    self.assertEqual(m.sha(node.work / "private-backup/etc-kubernetes/manifests" / name), self.hashes[name])
                self.assertEqual(len(list(self.manifests.iterdir())), 3)
            return actual(argv, timeout)
        node.command = command
        result = node.dispatch()
        self.assertEqual(result["state"], "PREPARED")
        self.assertEqual(list(self.manifests.iterdir()), [])
        self.assertEqual(len(list((node.work / "quarantined-manifests").iterdir())), 3)
        self.assertTrue((self.root / "etc/kubernetes/pki/ca.key").exists())
        self.assertNotIn("TEST PRIVATE", json.dumps(result))
        self.assertEqual(node.work.stat().st_mode & 0o777, 0o700)

    def test_cp3_empty_has_no_stop_or_quarantine(self):
        node = self.node("k8s-control03")
        result = node.dispatch()
        self.assertEqual(result["cleanup"], "NOT_NEEDED_CP3_EMPTY")
        self.assertFalse(result["configuration_mutation"])
        self.assertFalse((node.work / "quarantined-manifests").exists())
        self.assertFalse(any("stop" in command for command in self.commands))

    def test_backup_failure_does_not_stop_or_move(self):
        self.seed()
        node = self.node()
        with mock.patch.object(m.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                node.dispatch()
        self.assertEqual(len(list(self.manifests.iterdir())), 3)
        self.assertFalse(any("stop" in command for command in self.commands))
        self.assertEqual(node.read_state()["state"], "BACKUP_IN_PROGRESS")

    def test_symlink_is_rejected_before_mutation(self):
        self.seed()
        (self.root / "etc/kubernetes/unsafe").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            self.node().dispatch()
        self.assertEqual(len(list(self.manifests.iterdir())), 3)
        self.assertFalse(any("stop" in command for command in self.commands))

    def test_join_secrets_not_in_argv_logs_or_remaining_config(self):
        self.seed()
        node = self.node()
        node.dispatch()
        node.req.update(mode="join", join_config=self.config())
        def command(argv, timeout):
            self.commands.append(argv)
            if argv[:2] == ["kubeadm", "join"]:
                self.assertEqual(timeout, 600)
                path = pathlib.Path(argv[-1])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(path.read_text()), node.req["join_config"])
                self.assertNotIn("abcdef.0123456789abcdef", repr(argv))
                return {"rc": 0, "stdout": "abcdef.0123456789abcdef key=" + "b" * 64,
                        "stderr": "", "timed_out": False}
            return self.fake_run(argv, timeout)
        node.command = command
        result = node.dispatch()
        self.assertEqual(result["state"], "JOIN_COMMAND_SUCCEEDED_CLUSTER_VERIFICATION_REQUIRED")
        text = json.dumps(result) + node.journal.read_text()
        self.assertNotIn("abcdef.0123456789abcdef", text)
        self.assertNotIn("b" * 64, text)
        self.assertFalse((self.root / "run/fds-cp-join" / node.job / "JOIN.json").exists())
        with self.assertRaisesRegex(RuntimeError, "never repeat"):
            node.dispatch()

    def test_timeout_records_unknown_state_without_reset(self):
        self.seed()
        node = self.node()
        node.dispatch()
        node.req.update(mode="join", join_config=self.config())
        actual = self.fake_run
        def command(argv, timeout):
            if argv[:2] == ["kubeadm", "join"]:
                self.commands.append(argv)
                return {"rc": -9, "stdout": "partial execution", "stderr": "", "timed_out": True}
            return actual(argv, timeout)
        node.command = command
        result = node.dispatch()
        self.assertEqual(result["state"], "FAILED_RECOVERY_REQUIRED")
        self.assertFalse(result["retry_allowed"])
        self.assertFalse(any("reset" in command for command in self.commands))
        with self.assertRaisesRegex(RuntimeError, "never repeat"):
            node.dispatch()

    def test_preflight_bypass_fields_rejected(self):
        node = self.node()
        cfg = self.config()
        cfg["skipPhases"] = ["preflight"]
        node.req["join_config"] = cfg
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            node.validate_join_config()

    def test_missing_existing_runtime_rule_stops_before_backup_or_cleanup(self):
        self.seed()
        node = self.node()
        actual = self.fake_run
        def command(argv, timeout):
            if argv[0] == "firewall-cmd" and any("10.1.93.109/32" in x for x in argv):
                self.commands.append(argv)
                return {"rc": 1, "stdout": "no\n", "stderr": ""}
            return actual(argv, timeout)
        node.command = command
        with self.assertRaisesRegex(RuntimeError, "command failed"):
            node.dispatch()
        self.assertFalse(node.work.exists())
        self.assertEqual(len(list(self.manifests.iterdir())), 3)
        self.assertFalse(any("stop" in command for command in self.commands))
        self.assertFalse(any(any("--add" in argument or "--remove" in argument for argument in command) for command in self.commands))

    def test_arbitrary_top_level_request_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported request"):
            m.Node({"mode": "prepare", "node": "k8s-control02", "job_id": "a123456789abcdef", "command": "rm -rf /"})

    def unit_values(self, extra=""):
        return {
            "ExecStart": "{ path=/usr/bin/kubelet ; argv[]=/usr/bin/kubelet $KUBELET_KUBECONFIG_ARGS $KUBELET_CONFIG_ARGS $KUBELET_KUBEADM_ARGS $KUBELET_EXTRA_ARGS " + extra + " ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }\n",
            "Environment": '"KUBELET_KUBECONFIG_ARGS=--bootstrap-kubeconfig=/etc/kubernetes/bootstrap-kubelet.conf --kubeconfig=/etc/kubernetes/kubelet.conf" KUBELET_CONFIG_ARGS=--config=/var/lib/kubelet/config.yaml\n',
            "EnvironmentFiles": "/var/lib/kubelet/kubeadm-flags.env (ignore_errors=yes) /etc/sysconfig/kubelet (ignore_errors=yes)\n"}

    def unit_node(self, values):
        def command(argv, timeout):
            self.commands.append(argv)
            prop = next(value.split("=", 1)[1] for value in argv if value.startswith("--property="))
            return {"rc": 0, "stdout": values[prop], "stderr": ""}
        return self.node(runner=command)

    def test_realistic_systemctl_execstart_ignores_nonpath_metadata(self):
        values = self.unit_values()
        self.unit_node(values).configuration_checks()
        # A running service has actual timestamp text; this is also metadata.
        values["ExecStart"] = values["ExecStart"].replace("start_time=[n/a]", "start_time=[Fri 2026-09-18 11:22:25 KST]").replace("status=0/0", "status=1/FAILURE")
        self.unit_node(values).configuration_checks()

    def test_custom_execstart_flag_rejected_even_when_paths_standard(self):
        with self.assertRaisesRegex(RuntimeError, "unit flag"):
            self.unit_node(self.unit_values("--anonymous-auth=true")).configuration_checks()

    def test_custom_execstart_path_or_environment_file_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "unit path"):
            self.unit_node(self.unit_values("--config=/tmp/custom.yaml")).configuration_checks()
        values = self.unit_values()
        values["EnvironmentFiles"] += " /tmp/override.env (ignore_errors=yes)"
        with self.assertRaisesRegex(RuntimeError, "environment file"):
            self.unit_node(values).configuration_checks()


if __name__ == "__main__":
    unittest.main()
