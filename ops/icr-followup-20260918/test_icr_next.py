import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("icr_next", HERE / "RUN_ICR_NEXT.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def remote_functions():
    tree = ast.parse(app.REMOTE)
    # Load the actual remote functions without executing the final collector.
    tree.body = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign))]
    ns = {}
    exec(compile(tree, "REMOTE", "exec"), ns)
    return ns


def good_results():
    nodes = [{"name": n, "uid": n + "-uid", "internal_ips": [ip], "ready": "True",
              "control_plane": n.startswith("k8s-control")} for n, ip in app.EXPECTED.items()]
    return {"k8s-control01": {"status": "COLLECTED", "identity_verified": True,
            "cluster": {"available": True, "authenticated_node_access": True, "nodes": nodes}}}


class ReadinessTests(unittest.TestCase):
    def test_only_exact_six_ready_passes(self):
        results = good_results()
        self.assertTrue(app.summarize(results)["six_of_six_ready"])
        for edit in (lambda n: n.pop(), lambda n: n.append(copy.deepcopy(n[0])),
                     lambda n: n[0].update(ready="False"), lambda n: n[0].update(internal_ips=["10.1.93.199"]),
                     lambda n: n[0].update(control_plane=False)):
            bad = copy.deepcopy(results)
            edit(bad["k8s-control01"]["cluster"]["nodes"])
            self.assertFalse(app.summarize(bad)["six_of_six_ready"])

    def test_three_cp_is_not_six(self):
        results = good_results()
        results["k8s-control01"]["cluster"]["nodes"] = results["k8s-control01"]["cluster"]["nodes"][:3]
        summary = app.summarize(results)
        self.assertTrue(summary["three_cp_ready"])
        self.assertFalse(summary["six_of_six_ready"])
        self.assertEqual(len(summary["missing_workers"]), 3)
        self.assertEqual(summary["hep_runtime_approval"], "NOT_GRANTED_BY_COLLECTION")

    def test_failed_identity_or_auth_is_unknown(self):
        for key in ("identity_verified", "authenticated_node_access"):
            results = good_results()
            obj = results["k8s-control01"] if key == "identity_verified" else results["k8s-control01"]["cluster"]
            obj[key] = False
            summary = app.summarize(results)
            self.assertFalse(summary["six_of_six_ready"])
            self.assertIsNone(summary["missing_workers"])

    def test_host_mismatch_stops_queries(self):
        ns = remote_functions()
        ns["REQUEST"] = {"node": "k8s-worker01", "ip": "10.1.93.105"}
        with patch.object(ns["socket"], "gethostname", return_value="different"), patch.object(ns["os"], "geteuid", return_value=0):
            self.assertEqual(ns["collect"]()["status"], "IDENTITY_MISMATCH")
        self.assertEqual(ns["queries"], {})

    def test_unsafe_kubeconfig_prevents_network_commands(self):
        for altered in ({"proxy-url": "https://proxy.invalid"}, {"insecure-skip-tls-verify": True}, {"tls-server-name": "other.invalid"}):
            ns = remote_functions()
            config = {"clusters": [{"cluster": {"certificate-authority": "ca.crt", **altered}}], "users": []}
            calls = []
            def fake(label, argv, parse=False):
                calls.append(label)
                return config
            ns["run"] = fake
            with patch.object(ns["shutil"], "which", return_value="/usr/bin/kubectl"):
                out = {"route_to_lb": {"expected_source": True}}
                ns["cluster"](out)
            self.assertEqual(calls, ["kubeconfig_safety"])
            self.assertFalse(out["cluster"]["available"])

    def test_direct_tls_and_anonymous_not_accepted(self):
        ns = remote_functions()
        calls = []
        def fake(label, argv, parse=False):
            calls.append((label, argv))
            if label == "kubeconfig_safety":
                return {"clusters": [{"cluster": {"certificate-authority": "ca.crt"}}], "users": []}
            if label == "lb_authenticated_identity":
                return {"status": {"userInfo": {"username": "system:anonymous", "groups": ["system:unauthenticated"]}}}
            return {"items": []}
        ns["run"] = fake
        with patch.object(ns["shutil"], "which", return_value="/usr/bin/kubectl"):
            out = {"route_to_lb": {"expected_source": True}}
            ns["cluster"](out)
        for label, argv in calls[1:]:
            self.assertIn("--server=https://10.1.93.109:6443", argv)
            self.assertIn("--insecure-skip-tls-verify=false", argv)
        self.assertTrue(out["cluster"]["node_get_succeeded"])
        self.assertFalse(out["cluster"]["authenticated_node_access"])
        self.assertFalse(any("proxy" in k.lower() for k in ns["ENV"]))

    def test_errors_do_not_disclose_output(self):
        ns = remote_functions()
        failed = subprocess.CompletedProcess(["kubectl"], 1, "SECRET TOKEN", "SECRET KEY")
        with patch.object(ns["subprocess"], "run", return_value=failed):
            self.assertIsNone(ns["run"]("test", ["kubectl"]))
        self.assertNotIn("SECRET", json.dumps(ns["queries"]))

    def test_absent_calico_namespace_does_not_issue_404_query(self):
        ns = remote_functions()
        calls = []
        def fake(label, argv, parse=False):
            calls.append(label)
            if label == "kubeconfig_safety":
                return {"clusters": [{"cluster": {"certificate-authority": "ca.crt"}}], "users": []}
            if label == "lb_authenticated_identity":
                return {"status": {"userInfo": {"username": "admin", "groups": ["system:authenticated"]}}}
            if label == "calico_namespace_inventory":
                return {"items": [{"metadata": {"name": "kube-system"}}]}
            return {"items": []}
        ns["run"] = fake
        with patch.object(ns["shutil"], "which", return_value="/usr/bin/kubectl"):
            out = {"route_to_lb": {"expected_source": True}}
            ns["cluster"](out)
        self.assertTrue(out["calico_namespaces"]["calico-system"]["available"])
        self.assertEqual(out["calico_namespaces"]["calico-system"]["status"], "ABSENT")
        self.assertNotIn("calico_pods_calico-system", calls)
        self.assertIn("calico_pods_kube-system", calls)

    def test_all_targets_and_checksums_survive_one_failure(self):
        calls = []
        def fake(name, ip):
            calls.append(name)
            return {"status": "UNAVAILABLE" if "worker01" in name else "COLLECTED", "expected_node": name}
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                os.chdir(tmp)
                with patch.object(app, "remote", side_effect=fake), patch("sys.argv", ["RUN_ICR_NEXT.py", "--collect"]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(app.main(), 2)
                self.assertEqual(calls, list(app.NODES))
                archive = next(Path(tmp).glob("*.tar.gz"))
                self.assertEqual(Path(str(archive) + ".sha256").read_text().split()[0], app.sha(archive))
                with tarfile.open(archive) as tar:
                    files = [m for m in tar.getmembers() if m.isfile()]
                    self.assertEqual(len(files), 7)
                    sums = tar.extractfile(next(m for m in files if m.name.endswith("/SHA256SUMS"))).read().decode().splitlines()
                    self.assertEqual(len(sums), 6)
                    for line in sums:
                        digest, name = line.split("  ")
                        raw = tar.extractfile(next(m for m in files if m.name.endswith("/" + name))).read()
                        self.assertEqual(app.hashlib.sha256(raw).hexdigest(), digest)
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
