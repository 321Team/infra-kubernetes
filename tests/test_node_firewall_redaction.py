import json
import unittest
from unittest.mock import patch

from test_node_firewall import n


class StructuredRedactionTests(unittest.TestCase):
    def test_projected_token_configuration_stays_valid_and_unchanged(self):
        value = {"projected": {"sources": [{"serviceAccountToken": {
            "expirationSeconds": 3607, "path": "token"}}]}}
        self.assertEqual(json.loads(n.redact(json.dumps(value, indent=4))), value)

    def test_nested_credentials_are_redacted_without_losing_json_types(self):
        value = {"items": [{"token": "secret-value", "PASSWORD": 123,
                            "client-key-data": "private-value", "ready": True,
                            "count": 2, "optional": None,
                            "url": "https://person:password@example.invalid/"}]}
        text = n.redact(json.dumps(value))
        parsed = json.loads(text)["items"][0]
        for key in ("token", "PASSWORD", "client-key-data"):
            self.assertEqual(parsed[key], "[REDACTED]")
        self.assertNotIn("secret-value", text)
        self.assertNotIn("private-value", text)
        self.assertNotIn("person:password", text)
        self.assertIs(parsed["ready"], True)
        self.assertEqual(parsed["count"], 2)
        self.assertIsNone(parsed["optional"])

    def test_cp1_protection_path_parses_redacted_projected_pod_json(self):
        pod = {"metadata": {"name": "calico-node-test", "uid": "test-uid"},
               "spec": {"nodeName": "k8s-control01", "volumes": [{"projected": {
                   "sources": [{"serviceAccountToken": {"path": "token", "expirationSeconds": 3607}}]}}]},
               "status": {"phase": "Running", "containerStatuses": [
                   {"name": "calico-node", "ready": True, "restartCount": 0, "imageID": "test-image"}]}}
        replies = [{"rc": 0, "stderr": "", "stdout": n.redact(json.dumps({"items": items}, indent=4))}
                   for items in ([pod], [], [], [], [])]
        backend = n.BaseBackend("k8s-control01")
        try:
            with patch.object(backend, "snapshot", return_value={}), \
                    patch.object(backend, "kubectl", side_effect=replies):
                state = backend.cp1_state()
            self.assertEqual(state["sha256"], n.fingerprint(state["stable"]))
            self.assertEqual(state["stable"]["protection"]["calico_nodes"][0]["name"], "calico-node-test")
        finally:
            backend.cache.cleanup()


if __name__ == "__main__":
    unittest.main()
