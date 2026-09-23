import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch

import collect


def pod():
    return {"metadata": {"name": collect.POD_NAME, "namespace": "monitoring-api", "uid": collect.POD_UID,
                         "annotations": {"not-for-output": "PRIVATE-MARKER"}},
            "spec": {"nodeName": "k8s-worker03", "serviceAccountName": "prometheus",
                     "containers": [{"env": [{"value": "PRIVATE-MARKER"}]}]},
            "status": {"podIP": collect.POD_IP, "hostIP": "10.1.93.108", "phase": "Running",
                       "conditions": [{"type": "Ready", "status": "True"}],
                       "containerStatuses": [{"name": "prometheus", "imageID": collect.IMAGE_ID}]}}


class SafetyTests(unittest.TestCase):
    def test_guard_minimal_fields(self):
        self.assertEqual(collect.sender_fields(pod())["uid"], collect.POD_UID)
        self.assertNotIn("PRIVATE-MARKER", json.dumps(collect.sender_fields(pod())))

    def test_changed_identity_rejected(self):
        for section, key, value in [("metadata", "uid", "recreated"), ("spec", "serviceAccountName", "other"),
                                    ("spec", "nodeName", "k8s-worker01"), ("status", "podIP", "10.244.1.1"),
                                    ("spec", "hostNetwork", True), ("metadata", "deletionTimestamp", "now")]:
            changed = copy.deepcopy(pod()); changed[section][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                collect.sender_fields(changed)

    def test_ens160_identity_required(self):
        with self.assertRaises(ValueError):
            collect.interfaces_for("k8s-worker01", [{"ifname": "ens160", "flags": ["UP"], "addr_info": [{"local": "10.1.93.108"}]}])

    def test_local_veth_required(self):
        addresses = [{"ifname": "ens160", "flags": ["UP"], "addr_info": [{"local": "10.1.93.108"}]}]
        for route in [[{"dev": "ens160"}], [{"dev": "cali123", "gateway": "10.1.93.1"}], []]:
            with self.subTest(route=route), self.assertRaises(ValueError):
                collect.interfaces_for("k8s-worker03", addresses, route)
        self.assertEqual(collect.interfaces_for("k8s-worker03", addresses, [{"dev": "cali123abc"}]), ["ens160", "cali123abc"])

    def test_bounded_passive_capture_timeout_is_observation(self):
        with patch.object(collect, "run", return_value={"rc": 124, "stdout": "", "stderr": ""}) as run:
            result = collect.capture("/sudo", "/timeout", "/tcpdump", "ens160")
        self.assertEqual(result["status"], "WINDOW_ENDED")
        argv = run.call_args.args[0]
        self.assertIn("35s", argv); self.assertIn("-p", argv)
        self.assertNotIn("-w", argv); self.assertNotIn("-A", argv); self.assertNotIn("-X", argv)
        self.assertNotIn("src host", argv[-1])

    def test_capture_denial_is_failure(self):
        with patch.object(collect, "run", return_value={"rc": 1}):
            self.assertEqual(collect.capture("s", "t", "d", "ens160")["status"], "FAILED")

    def test_guard_output_redacted_and_preserves_failure(self):
        for return_code in [0, 1]:
            output = io.StringIO()
            response = {"rc": return_code, "command": ["kubectl"], "stdout": json.dumps(pod()), "stderr": ""}
            with patch.object(collect.sys, "argv", ["collect.py", "guard-before"]), \
                 patch.object(collect.socket, "gethostname", return_value="k8s-control01"), \
                 patch.object(collect.getpass, "getuser", return_value="svc_ansible"), \
                 patch.object(collect, "tool", side_effect=lambda x: "/usr/bin/" + x), \
                 patch.object(collect, "run", return_value=response), contextlib.redirect_stdout(output):
                rc = collect.main()
            self.assertEqual(rc, 0 if return_code == 0 else 2)
            self.assertNotIn("PRIVATE-MARKER", output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["reads"][0]["rc"], return_code)


if __name__ == "__main__":
    unittest.main()
