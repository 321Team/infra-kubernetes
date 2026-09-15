import json
import unittest
from unittest.mock import patch

from test_node_firewall import n


class StructuredRedactionTests(unittest.TestCase):
    def test_review_regression_and_existing_gaps(self):
        keys = ("accessToken", "refreshToken", "dbPassword", "clientSecret", "client-key", "tls.key",
                "ACCESS_TOKEN", "refresh-token", "db.password", "client_secret", "apiKey", "credentials")
        value = {key: "synthetic-sensitive-" + str(i) for i, key in enumerate(keys)}
        parsed = json.loads(n.redact(json.dumps(value)))
        self.assertEqual(parsed, dict.fromkeys(keys, "[REDACTED]"))

    def test_parent_credential_context_survives_nested_objects_and_lists(self):
        value = {"credentials": {"users": [{"name": "synthetic-user", "value": "synthetic-secret"}]},
                 "accessToken": ["synthetic-token", {"raw": "synthetic-raw"}]}
        parsed = json.loads(n.redact(json.dumps(value)))
        self.assertEqual(parsed["credentials"]["users"][0], {"name": "[REDACTED]", "value": "[REDACTED]"})
        self.assertEqual(parsed["accessToken"], ["[REDACTED]", {"raw": "[REDACTED]"}])

    def test_env_literal_is_masked_but_reference_and_regular_env_survive(self):
        reference = {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {
            "name": "db-auth", "key": "password", "optional": False}}}
        normal = {"name": "FDS_RESPONSE", "value": "trial/a"}
        env = [{"name": "ACCESS_TOKEN", "value": "synthetic-env-secret"}, reference, normal]
        parsed = json.loads(n.redact(json.dumps({"spec": {"containers": [{"env": env}]}})))
        got = parsed["spec"]["containers"][0]["env"]
        self.assertEqual(got[0], {"name": "ACCESS_TOKEN", "value": "[REDACTED]"})
        self.assertEqual(got[1:], [reference, normal])

    def test_secret_payload_arbitrary_names_are_masked(self):
        value = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "test-only"},
                 "data": {"arbitrary": "c3ludGhldGlj"}, "stringData": {"arbitrary": "synthetic"}}
        parsed = json.loads(n.redact(json.dumps(value)))
        self.assertEqual(parsed["data"], {"arbitrary": "[REDACTED]"})
        self.assertEqual(parsed["stringData"], {"arbitrary": "[REDACTED]"})
        self.assertEqual(parsed["metadata"], value["metadata"])

    def test_same_named_credential_object_outside_projected_path_is_masked(self):
        value = {"serviceAccountToken": {"path": "synthetic-token"}}
        self.assertEqual(json.loads(n.redact(json.dumps(value))),
                         {"serviceAccountToken": {"path": "[REDACTED]"}})

    def test_kubernetes_reference_and_selector_fields_survive(self):
        value = {"secretKeyRef": {"name": "db-auth", "key": "password", "optional": False},
                 "imagePullSecrets": [{"name": "registry-auth"}],
                 "matchExpressions": [{"key": "role", "operator": "In", "values": ["control"]}],
                 "automountServiceAccountToken": False}
        parsed = json.loads(n.redact(json.dumps(value)))
        self.assertEqual(parsed["secretKeyRef"], value["secretKeyRef"])
        self.assertEqual(parsed["imagePullSecrets"], value["imagePullSecrets"])
        self.assertEqual(parsed["matchExpressions"], value["matchExpressions"])
        self.assertIs(parsed["automountServiceAccountToken"], False)

    def test_plaintext_and_bearer_protection_remains(self):
        text = n.redact('password=synthetic-password token=synthetic-token Bearer synthetic-bearer')
        self.assertNotIn('synthetic-', text)
        parsed = json.loads(n.redact(json.dumps({"Authorization": "Bearer synthetic-bearer"})))
        self.assertEqual(parsed["Authorization"], "Bearer [REDACTED]")

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
