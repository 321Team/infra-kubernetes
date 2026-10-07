"""Synthetic unit checks. No containerd or host connection is used."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "tools" / "oid02-cache-metadata-readonly.py"
spec = importlib.util.spec_from_file_location("oid02_metadata", MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def packed(obj):
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return raw, "sha256:" + hashlib.sha256(raw).hexdigest()


def fixture(index=True, config_change=None, manifest_change=None, index_change=None):
    cfg = {"architecture": "amd64", "os": "linux",
           "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64]},
           "config": {"Env": ["TOKEN=DO_NOT_EMIT"], "Labels": {"private": "DO_NOT_EMIT"}}}
    if config_change:
        config_change(cfg)
    config_raw, config_hash = packed(cfg)
    manifest = {"schemaVersion": 2, "mediaType": next(iter(sorted(m.MANIFEST_TYPES))),
                "config": {"mediaType": next(iter(sorted(m.CONFIG_TYPES))),
                           "digest": config_hash, "size": len(config_raw)},
                "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                            "digest": "sha256:" + "b" * 64, "size": 100}]}
    if manifest_change:
        manifest_change(manifest)
    manifest_raw, manifest_hash = packed(manifest)
    docs = {config_hash: config_raw, manifest_hash: manifest_raw}
    if not index:
        return manifest_hash, docs
    idx = {"schemaVersion": 2, "mediaType": next(iter(sorted(m.INDEX_TYPES))),
           "manifests": [{"mediaType": manifest["mediaType"], "digest": manifest_hash,
                          "size": len(manifest_raw),
                          "platform": {"architecture": "amd64", "os": "linux"}}]}
    if index_change:
        index_change(idx)
    index_raw, index_hash = packed(idx)
    docs[index_hash] = index_raw
    return index_hash, docs


class MetadataTests(unittest.TestCase):
    def inspect(self, **changes):
        digest, docs = fixture(**changes)
        return m.inspect_image(docs.__getitem__, "fixture", "invalid.example/fixture", digest)

    def test_index_platform_config_and_descriptor_sizes(self):
        result = self.inspect()
        self.assertIsNotNone(result["index_digest"])
        self.assertNotEqual(result["index_digest"], result["linux_amd64_manifest_digest"])
        self.assertFalse(result["layer_bytes_verified"])
        self.assertFalse(result["original_supply_provenance_verified"])
        self.assertIsNone(result["archive_size_bytes"])

    def test_direct_manifest_platform_is_verified_from_config(self):
        result = self.inspect(index=False)
        self.assertIsNone(result["index_digest"])
        self.assertEqual(result["approved_top_digest"], result["linux_amd64_manifest_digest"])

    def test_config_env_labels_never_appear_in_output(self):
        self.assertNotIn("DO_NOT_EMIT", json.dumps(self.inspect()))

    def test_layer_bytes_are_never_read(self):
        digest, docs = fixture()
        calls = []
        def read(key):
            calls.append(key)
            return docs[key]
        result = m.inspect_image(read, "fixture", "fixture", digest)
        self.assertEqual(len(calls), 3)
        self.assertNotIn(result["layers"][0]["digest"], calls)

    def test_top_content_digest_mismatch(self):
        digest, docs = fixture()
        docs[digest] += b" "
        with self.assertRaisesRegex(m.CollectionError, "DIGEST_MISMATCH"):
            m.inspect_image(docs.__getitem__, "fixture", "fixture", digest)

    def test_child_content_digest_mismatch(self):
        digest, docs = fixture()
        child = json.loads(docs[digest])["manifests"][0]["digest"]
        docs[child] += b" "
        with self.assertRaisesRegex(m.CollectionError, "DIGEST_MISMATCH"):
            m.inspect_image(docs.__getitem__, "fixture", "fixture", digest)

    def test_child_size_mismatch(self):
        with self.assertRaisesRegex(m.CollectionError, "SIZE_MISMATCH"):
            self.inspect(index_change=lambda x: x["manifests"][0].update(size=1))

    def test_missing_platform(self):
        with self.assertRaisesRegex(m.CollectionError, "PLATFORM_MISSING"):
            self.inspect(index_change=lambda x: x["manifests"][0]["platform"].update(architecture="arm64"))

    def test_ambiguous_platform(self):
        with self.assertRaisesRegex(m.CollectionError, "AMBIGUOUS"):
            self.inspect(index_change=lambda x: x["manifests"].append(copy.deepcopy(x["manifests"][0])))

    def test_config_platform_mismatch(self):
        with self.assertRaisesRegex(m.CollectionError, "CONFIG_PLATFORM_MISMATCH"):
            self.inspect(config_change=lambda x: x.update(os="windows"))

    def test_cpu_variant_not_assumed_compatible(self):
        with self.assertRaisesRegex(m.CollectionError, "PLATFORM_MISSING"):
            self.inspect(index_change=lambda x: x["manifests"][0]["platform"].update(variant="v3"))

    def test_descriptor_boolean_size_rejected(self):
        with self.assertRaisesRegex(m.CollectionError, "DESCRIPTOR_SIZE"):
            self.inspect(manifest_change=lambda x: x["layers"][0].update(size=True))

    def test_conflicting_duplicate_layer_sizes(self):
        def change(x):
            dup = copy.deepcopy(x["layers"][0])
            dup["size"] = 101
            x["layers"].append(dup)
        with self.assertRaisesRegex(m.CollectionError, "CONFLICTING_DESCRIPTOR_SIZE"):
            self.inspect(manifest_change=change)

    def test_rootfs_layer_count_not_inferred(self):
        with self.assertRaisesRegex(m.CollectionError, "ROOTFS_RELATION"):
            self.inspect(config_change=lambda x: x["rootfs"].update(diff_ids=[]))

    def test_foreign_layer_not_treated_as_standard_layer(self):
        with self.assertRaisesRegex(m.CollectionError, "MEDIA_TYPE"):
            self.inspect(manifest_change=lambda x: x["layers"][0].update(mediaType="application/vnd.docker.image.rootfs.foreign.diff.tar.gzip"))

    def test_duplicate_json_keys_rejected(self):
        raw = b'{"schemaVersion":2,"schemaVersion":2}'
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(m.CollectionError, "DUPLICATE_JSON_KEY"):
            m.document(raw, digest)

    def test_nonfinite_json_rejected(self):
        raw = b'{"schemaVersion":2,"size":NaN}'
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(m.CollectionError, "NONFINITE_JSON"):
            m.document(raw, digest)

    def test_schema_boolean_rejected(self):
        raw, digest = packed({"schemaVersion": True})
        with self.assertRaisesRegex(m.CollectionError, "UNSUPPORTED_SCHEMA"):
            m.document(raw, digest)

    def test_oversized_document_rejected(self):
        with self.assertRaisesRegex(m.CollectionError, "SIZE_LIMIT"):
            m.document(b"x" * (m.MAX_DOCUMENT + 1), "sha256:" + "1" * 64)

    def test_windows_cli_failure_is_incomplete_not_runtime_pass(self):
        with mock.patch.object(m.platform, "system", return_value="Windows"):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                rc = m.main(["--source-commit", "c" * 40])
        obj = json.loads(stream.getvalue())
        self.assertEqual(rc, 2)
        self.assertEqual(obj["status"], "INCOMPLETE")
        self.assertFalse(obj["runtime_changed"])
        self.assertFalse(obj["plan_ready"])
        self.assertEqual(obj["commands"], [])

    def test_wrong_source_host_rejected_before_command(self):
        with mock.patch.object(m.platform, "system", return_value="Linux"), \
             mock.patch.object(m.os, "geteuid", return_value=0, create=True), \
             mock.patch.object(m.socket, "gethostname", return_value="worker01"), \
             mock.patch.object(m.subprocess, "run") as run:
            with self.assertRaisesRegex(m.CollectionError, "CONTROL01_SOURCE_ONLY"):
                m.host_gate()
            run.assert_not_called()

    def test_command_nonzero_is_not_success_and_stderr_is_not_emitted(self):
        fake = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"SECRET_DO_NOT_EMIT")
        with mock.patch.object(m.subprocess, "run", return_value=fake) as run:
            reader = m.CacheReader("/usr/bin/ctr")
            with self.assertRaisesRegex(m.CollectionError, "READ_FAILED"):
                reader("sha256:" + "c" * 64)
        argv = run.call_args.args[0]
        self.assertEqual(argv[-3:-1], ["content", "get"])
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(reader.commands[0]["returncode"], 1)
        self.assertNotIn("SECRET", json.dumps(reader.commands))

    def test_command_timeout_is_not_success(self):
        with mock.patch.object(m.subprocess, "run", side_effect=subprocess.TimeoutExpired(["ctr"], 10)):
            reader = m.CacheReader("/usr/bin/ctr")
            with self.assertRaisesRegex(m.CollectionError, "TIMEOUT"):
                reader("sha256:" + "c" * 64)
        self.assertIsNone(reader.commands[0]["returncode"])
        self.assertEqual(reader.commands[0]["error"], "TIMEOUT")

    def test_version_is_sanitized_and_command_is_read_only(self):
        fake = subprocess.CompletedProcess([], 0, stdout=b"ctr github.com/containerd/containerd v2.3.5 SECRET", stderr=b"")
        with mock.patch.object(m.subprocess, "run", return_value=fake):
            reader = m.CacheReader("/usr/bin/ctr")
            self.assertEqual(reader.version(), "2.3.5")
        self.assertEqual(reader.commands[0]["argv"], ["/usr/bin/ctr", "--version"])
        self.assertNotIn("SECRET", json.dumps(reader.commands))

    def test_time_budget_stops_without_extra_command(self):
        reader = m.CacheReader("/usr/bin/ctr")
        reader.deadline = 0
        with mock.patch.object(m.subprocess, "run") as run:
            with self.assertRaisesRegex(m.CollectionError, "TIME_BUDGET"):
                reader("sha256:" + "c" * 64)
            run.assert_not_called()

    def test_unverified_host_has_no_observed_address(self):
        with mock.patch.object(m.platform, "system", return_value="Windows"):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                rc = m.main(["--source-commit", "c" * 40])
        result = json.loads(stream.getvalue())
        self.assertIsNone(result["address"])
        self.assertIsNone(result["effective_uid"])
        self.assertIsNone(result["ctr_version"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
