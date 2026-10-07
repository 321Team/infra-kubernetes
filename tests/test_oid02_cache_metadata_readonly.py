"""Synthetic unit checks. No containerd or host connection is used."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "tools" / "oid02-cache-metadata-readonly.py"
spec = importlib.util.spec_from_file_location("oid02_metadata", MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def packed(obj):
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return raw, "sha256:" + hashlib.sha256(raw).hexdigest()


def captured(stdout=b"", stderr=b"", returncode=0, error=None):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode,
            "error": error, "stdout_complete": True, "stderr_complete": True}


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
        fake = captured(stderr=b"permission denied SECRET_DO_NOT_EMIT", returncode=1)
        with mock.patch.object(m, "capture_command", return_value=fake) as run:
            reader = m.CacheReader("/usr/bin/ctr")
            with self.assertRaisesRegex(m.CollectionError, "READ_FAILED"):
                reader("sha256:" + "c" * 64)
        argv = run.call_args.args[0]
        self.assertEqual(argv[-3:-1], ["content", "get"])
        self.assertEqual(reader.commands[0]["returncode"], 1)
        self.assertEqual(reader.commands[0]["stderr_size_bytes"], len(fake["stderr"]))
        self.assertEqual(reader.commands[0]["stderr_sha256"], hashlib.sha256(fake["stderr"]).hexdigest())
        self.assertEqual(reader.commands[0]["stderr_error_category"], "PERMISSION")
        self.assertNotIn("SECRET", json.dumps(reader.commands))

    def test_command_timeout_is_not_success(self):
        with mock.patch.object(m, "capture_command",
                               return_value=captured(b"partial", b"SECRET", -9, "READ_TIMEOUT")):
            reader = m.CacheReader("/usr/bin/ctr")
            with self.assertRaisesRegex(m.CollectionError, "TIMEOUT"):
                reader("sha256:" + "c" * 64)
        self.assertEqual(reader.commands[0]["returncode"], -9)
        self.assertEqual(reader.commands[0]["error"], "READ_TIMEOUT")
        self.assertFalse(reader.commands[0]["stderr_capture_complete"])
        self.assertFalse(reader.commands[0]["stdout_capture_complete"])
        self.assertNotIn("SECRET", json.dumps(reader.commands))

    def test_version_is_sanitized_and_command_is_read_only(self):
        fake = captured(b"ctr github.com/containerd/containerd v2.3.5 SECRET")
        with mock.patch.object(m, "capture_command", return_value=fake):
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


class FollowupTests(unittest.TestCase):
    def test_missing_manifest_media_type_is_intentionally_incomplete(self):
        top, docs = fixture(index=False, manifest_change=lambda x: x.pop("mediaType"))
        with self.assertRaisesRegex(m.CollectionError, "UNSUPPORTED_TOP_MEDIA_TYPE"):
            m.inspect_image(docs.__getitem__, "fixture", "fixture", top)

    def test_empty_layers_for_pinned_service_images_is_incomplete(self):
        top, docs = fixture(index=False,
                            config_change=lambda x: x["rootfs"].update(diff_ids=[]),
                            manifest_change=lambda x: x.update(layers=[]))
        with self.assertRaisesRegex(m.CollectionError, "INVALID_LAYERS"):
            m.inspect_image(docs.__getitem__, "fixture", "fixture", top)

    def test_success_stderr_warning_has_only_metadata(self):
        with mock.patch.object(m, "capture_command",
                               return_value=captured(b"document", b"SECRET_WARNING")):
            reader = m.CacheReader("ctr")
            self.assertEqual(reader.run(["ctr"]), b"document")
        c = reader.commands[0]
        self.assertTrue(c["stderr_capture_complete"])
        self.assertEqual(c["stderr_error_category"], "STDERR_PRESENT_ON_SUCCESS")
        self.assertNotIn("SECRET_WARNING", json.dumps(c))
        self.assertIn("started_at_utc", c)
        self.assertIn("finished_at_utc", c)

    def test_exec_failure_exposes_no_exception_text(self):
        with mock.patch.object(m, "capture_command", side_effect=OSError("SECRET")):
            reader = m.CacheReader("ctr")
            with self.assertRaisesRegex(m.CollectionError, "EXEC_ERROR"):
                reader.run(["ctr"])
        self.assertNotIn("SECRET", json.dumps(reader.commands))
        self.assertIsNone(reader.commands[0]["returncode"])

    def test_socket_missing_has_its_own_error_and_no_command(self):
        with mock.patch.object(m.platform, "system", return_value="Linux"), \
             mock.patch.object(m.os, "geteuid", return_value=0, create=True), \
             mock.patch.object(m.socket, "gethostname", return_value="control01"), \
             mock.patch.object(m.os, "stat", side_effect=FileNotFoundError("SECRET")), \
             mock.patch.object(m, "capture_command") as run:
            with self.assertRaisesRegex(m.CollectionError, "SOCKET_MISSING"):
                m.host_gate()
            run.assert_not_called()

    def test_resolved_binary_identity_matches_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "ctr"
            binary.write_bytes(b"synthetic_binary_identity")
            with mock.patch.object(m.shutil, "which", return_value=str(binary)):
                reader = m.CacheReader(None)
                selected = reader.resolve_binary("ctr")
            self.assertEqual(selected, str(binary.resolve()))
            self.assertEqual(reader.executables["ctr"]["sha256"],
                             hashlib.sha256(binary.read_bytes()).hexdigest())

    def test_missing_binary_does_not_execute(self):
        with mock.patch.object(m.shutil, "which", return_value=None):
            with self.assertRaisesRegex(m.CollectionError, "EXISTING_IP_REQUIRED"):
                m.CacheReader(None).resolve_binary("ip")

    def test_nonregular_binary_is_rejected_before_open(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(m.shutil, "which", return_value=directory):
            with self.assertRaisesRegex(m.CollectionError, "REGULAR_FILE_REQUIRED"):
                m.CacheReader(None).resolve_binary("ctr")

    def test_real_local_subprocess_captures_separate_bounded_streams(self):
        result = m.capture_command([sys.executable, "-c",
            "import sys;sys.stdout.write('ok');sys.stderr.write('private');sys.exit(3)"], 2)
        self.assertEqual(result["returncode"], 3)
        self.assertEqual(result["stdout"], b"ok")
        self.assertEqual(result["stderr"], b"private")
        self.assertIsNone(result["error"])
        self.assertTrue(result["stdout_complete"])
        self.assertTrue(result["stderr_complete"])

    def test_real_local_subprocess_timeout_preserves_partial_stderr(self):
        result = m.capture_command([sys.executable, "-c",
            "import sys,time;sys.stderr.write('private');sys.stderr.flush();time.sleep(5)"], 0.5)
        self.assertEqual(result["error"], "READ_TIMEOUT")
        self.assertEqual(result["stderr"], b"private")
        self.assertIsNotNone(result["returncode"])

    def test_real_local_subprocess_stderr_limit_stops_and_bounds_memory(self):
        with mock.patch.object(m, "MAX_STDERR", 8192):
            result = m.capture_command([sys.executable, "-c",
                "import sys;sys.stderr.buffer.write(b'x'*100000);sys.stderr.flush()"], 2)
        self.assertEqual(result["error"], "STDERR_SIZE_LIMIT")
        self.assertLessEqual(len(result["stderr"]), 8192)
        self.assertFalse(result["stderr_complete"])

    def test_real_local_subprocess_stdout_limit_stops_and_bounds_memory(self):
        with mock.patch.object(m, "MAX_DOCUMENT", 8192):
            result = m.capture_command([sys.executable, "-c",
                "import sys;sys.stdout.buffer.write(b'x'*100000);sys.stdout.flush()"], 2)
        self.assertEqual(result["error"], "STDOUT_SIZE_LIMIT")
        self.assertLessEqual(len(result["stdout"]), 8192)
        self.assertFalse(result["stdout_complete"])

    def test_ip_command_record_is_kept_when_address_gate_fails(self):
        reader = m.CacheReader(None)
        fake = captured(b'[{"addr_info":[{"family":"inet","local":"10.1.93.105"}]}]')
        with mock.patch.object(m.platform, "system", return_value="Linux"), \
             mock.patch.object(m.os, "geteuid", return_value=0, create=True), \
             mock.patch.object(m.socket, "gethostname", return_value="control01"), \
             mock.patch.object(m.os, "stat", return_value=mock.Mock(st_mode=m.stat.S_IFSOCK)), \
             mock.patch.object(reader, "resolve_binary", return_value="/usr/sbin/ip"), \
             mock.patch.object(m, "capture_command", return_value=fake):
            with self.assertRaisesRegex(m.CollectionError, "ADDRESS_MISMATCH"):
                m.host_gate(reader)
        self.assertEqual(reader.commands[0]["argv"], ["/usr/sbin/ip", "-j", "-4", "address", "show"])
        self.assertEqual(reader.commands[0]["returncode"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
