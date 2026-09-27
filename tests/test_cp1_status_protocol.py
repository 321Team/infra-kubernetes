"""Exercise the real consumer/helper request protocol without SSH or mutation."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cp1_status_protocol_controller", ROOT / "scripts/run-node-firewall-cni.py")
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)
helper = controller.node
consumer = controller.cni
HOST = "k8s-control01"
JOB = "c564ae97530dfe80"


class CP1StatusProtocolTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.requests = []
        self.policy = helper.execution_policy()
        self.frozen_status = {"status": "CP_SESSION_ACTIVE_REVIEW_PENDING", "job_id": JOB}
        self.frozen_run = mock.Mock(return_value=SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps(self.frozen_status)))
        self.api = SimpleNamespace(
            remote_request=controller.bind_policy_reader(self.policy, helper.policy_hash()),
            require_result=controller.require_result,
            cp1_state_gate=controller.cp1_state_gate,
            re=controller.re,
            run=self.frozen_run,
            ssh_argv=controller.ssh_argv,
        )

    def dispatch(self, name, request):
        """Replace only the SSH boundary; execute the actual remote dispatcher."""
        self.requests.append((name, copy.deepcopy(request)))
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            helper.main(request)
        return {"transport": "PASS", "identity": {"hostname": name},
                "stderr": "", "result": json.loads(stream.getvalue())}

    @contextlib.contextmanager
    def protocol(self):
        events = self.events

        class ReadOnlyBackend:
            def __init__(self, name):
                self.node = name
                self.cache = SimpleNamespace(cleanup=lambda: events.append("cleanup"))

            def identity(self):
                events.append("identity")

            def cp1_state(self):
                events.append("cp1-state")
                stable = {"firewall": {"verified": True}, "protection": {"verified": True}}
                return {"stable": stable, "sha256": helper.fingerprint(stable)}

        with mock.patch.object(helper, "BaseBackend", ReadOnlyBackend), \
                mock.patch.object(helper, "Backend", side_effect=AssertionError("mutation backend used")), \
                mock.patch.object(controller, "remote_request", side_effect=self.dispatch):
            yield

    def test_original_two_field_request_fails_before_backend_or_commands(self):
        with self.protocol():
            wrapper = self.dispatch(HOST, {"mode": "cp1-state", "node": HOST})
        self.assertEqual(wrapper["result"]["status"], "STOP")
        self.assertEqual(wrapper["result"]["error"], "'policy'")
        self.assertEqual(wrapper["result"]["command_metrics"]["count"], 0)
        self.assertEqual(self.events, [])

    def test_bound_reader_drives_real_consumer_and_helper_to_frozen_status(self):
        with self.protocol():
            result = consumer.remote_status(self.api, {"job_id": JOB})
        self.assertEqual(result, self.frozen_status)
        self.assertEqual(self.events, ["identity", "cp1-state", "cleanup"])
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0][1]
        self.assertEqual(request["policy"], self.policy)
        self.assertEqual(request["policy_sha256"], helper.policy_hash())
        self.frozen_run.assert_called_once()

    def test_wrong_bound_digest_stops_before_frozen_read(self):
        self.api.remote_request = controller.bind_policy_reader(self.policy, "0" * 64)
        with self.protocol(), self.assertRaisesRegex(Exception, "remote result did not satisfy"):
            consumer.remote_status(self.api, {"job_id": JOB})
        self.assertEqual(self.events, [])
        self.frozen_run.assert_not_called()

    def test_caller_cannot_replace_policy_and_caller_input_is_unchanged(self):
        request = {"mode": "cp1-state", "node": HOST,
                   "policy": {"permanent_promotion": "ALLOW"}, "policy_sha256": "0" * 64}
        original = copy.deepcopy(request)
        with self.protocol():
            wrapper = self.api.remote_request(HOST, request)
        self.assertEqual(wrapper["result"]["status"], "CP1_STATE_REVIEW_REQUIRED")
        self.assertEqual(self.requests[0][1]["policy"], self.policy)
        self.assertEqual(request, original)

    def test_mutation_or_other_host_request_is_rejected_before_transport(self):
        for name, request in (
                (HOST, {"mode": "apply", "node": HOST}),
                ("k8s-control02", {"mode": "cp1-state", "node": "k8s-control02"}),
                (HOST, {"mode": "cp1-state", "node": "k8s-control02"})):
            with self.subTest(name=name, request=request), self.protocol(), \
                    self.assertRaisesRegex(Exception, "CP1 read-only request required"):
                self.api.remote_request(name, request)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.events, [])
        self.frozen_run.assert_not_called()

    def test_transport_failure_prevents_frozen_status_read(self):
        with mock.patch.object(controller, "remote_request", return_value={"transport": "ERROR"}), \
                self.assertRaisesRegex(Exception, "remote result did not satisfy"):
            consumer.remote_status(self.api, {"job_id": JOB})
        self.frozen_run.assert_not_called()

    def test_frozen_status_process_failure_remains_a_stop(self):
        self.frozen_run.return_value = SimpleNamespace(returncode=2, stderr="failure", stdout="{}")
        with self.protocol(), self.assertRaisesRegex(Exception, "CP1 live status unavailable"):
            consumer.remote_status(self.api, {"job_id": JOB})
        self.frozen_run.assert_called_once()

    def test_protection_drift_stops_before_second_read(self):
        stable = {"firewall": {"verified": True}, "protection": {"name": "changed"}}
        state = {"stable": stable, "sha256": helper.fingerprint(stable)}
        record = {"job_id": JOB, "after": {"firewall": stable["firewall"],
                                           "protection": {"name": "original"}}}
        with self.protocol(), self.assertRaisesRegex(Exception, "CP1 stable state differs"):
            consumer.before_or_after(self.api, state, state["sha256"], record)
        self.assertEqual(self.requests, [])
        self.frozen_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
