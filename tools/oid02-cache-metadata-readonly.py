#!/usr/bin/env python3
"""Read pinned OID02 manifest metadata from the existing control01 cache."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import threading
import time

INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
CONFIG_TYPES = {
    "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.container.image.v1+json",
}
LAYER_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd",
    "application/vnd.docker.image.rootfs.diff.tar.gzip",
}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
MAX_DOCUMENT = 4 * 1024 * 1024
MAX_STDERR = 64 * 1024
CONTAINERD_SOCKET = "/run/containerd/containerd.sock"
TARGETS = (
    ("kube-controllers", "quay.io/calico/kube-controllers",
     "sha256:df00967cbd6d88e1ff3123e1598895845622e2987928b4ebd9d8ac49aefe00c3"),
    ("coredns", "registry.k8s.io/coredns/coredns:v1.14.2",
     "sha256:e7e6440cfd1e919280958f5b5a6ab2b184d385bba774c12ad2a9e1e4183f90d9"),
)


class CollectionError(Exception):
    """A categorical error; never include config content or command stderr."""


def pairs_unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CollectionError("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def document(raw, digest, expected_size=None, schema=True):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DOCUMENT:
        raise CollectionError("DOCUMENT_SIZE_LIMIT")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise CollectionError("INVALID_DIGEST")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
        raise CollectionError("CONTENT_DIGEST_MISMATCH")
    if expected_size is not None and len(raw) != expected_size:
        raise CollectionError("DESCRIPTOR_SIZE_MISMATCH")
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(
                             CollectionError("NONFINITE_JSON_VALUE")))
    except (UnicodeError, ValueError, RecursionError):
        raise CollectionError("INVALID_JSON") from None
    if not isinstance(obj, dict):
        raise CollectionError("JSON_OBJECT_REQUIRED")
    if schema and (type(obj.get("schemaVersion")) is not int
                   or obj["schemaVersion"] != 2):
        raise CollectionError("UNSUPPORTED_SCHEMA")
    return obj


def descriptor(obj, allowed_types):
    if not isinstance(obj, dict):
        raise CollectionError("INVALID_DESCRIPTOR")
    digest, size, media = obj.get("digest"), obj.get("size"), obj.get("mediaType")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise CollectionError("INVALID_DESCRIPTOR_DIGEST")
    if type(size) is not int or not 0 < size < 2**63:
        raise CollectionError("INVALID_DESCRIPTOR_SIZE")
    if media not in allowed_types:
        raise CollectionError("UNSUPPORTED_DESCRIPTOR_MEDIA_TYPE")
    # This preparation never follows registry URLs or reads embedded data.
    return {"digest": digest, "size": size, "mediaType": media}


def platform_matches(obj):
    return (isinstance(obj, dict) and obj.get("os") == "linux"
            and obj.get("architecture") == "amd64"
            and obj.get("variant") in (None, "", "v1")
            and not obj.get("os.features") and not obj.get("features"))


def inspect_image(reader, name, ref, top_digest):
    top_raw = reader(top_digest)
    top = document(top_raw, top_digest)
    top_type = top.get("mediaType")
    content_sizes = {top_digest: len(top_raw)}

    def remember(desc):
        previous = content_sizes.get(desc["digest"])
        if previous is not None and previous != desc["size"]:
            raise CollectionError("CONFLICTING_DESCRIPTOR_SIZE")
        content_sizes[desc["digest"]] = desc["size"]

    index_digest = None
    if top_type in INDEX_TYPES:
        items = top.get("manifests")
        if not isinstance(items, list) or not 0 < len(items) <= 128:
            raise CollectionError("INVALID_INDEX_MANIFESTS")
        matches = [x for x in items if isinstance(x, dict)
                   and platform_matches(x.get("platform"))]
        if len(matches) != 1:
            raise CollectionError("PLATFORM_MISSING_OR_AMBIGUOUS")
        child = descriptor(matches[0], MANIFEST_TYPES)
        remember(child)
        manifest_raw = reader(child["digest"])
        manifest = document(manifest_raw, child["digest"], child["size"])
        if manifest.get("mediaType") != child["mediaType"]:
            raise CollectionError("MANIFEST_MEDIA_TYPE_MISMATCH")
        index_digest = top_digest
        manifest_digest = child["digest"]
    elif top_type in MANIFEST_TYPES:
        manifest, manifest_digest = top, top_digest
    else:
        raise CollectionError("UNSUPPORTED_TOP_MEDIA_TYPE")

    config = descriptor(manifest.get("config"), CONFIG_TYPES)
    remember(config)
    config_raw = reader(config["digest"])
    config_doc = document(config_raw, config["digest"], config["size"], schema=False)
    if not platform_matches(config_doc):
        raise CollectionError("CONFIG_PLATFORM_MISMATCH")
    # Only os/architecture are emitted. Env, labels and history stay in memory.
    layers_raw = manifest.get("layers")
    if not isinstance(layers_raw, list) or not 1 <= len(layers_raw) <= 512:
        raise CollectionError("INVALID_LAYERS")
    layers = [descriptor(item, LAYER_TYPES) for item in layers_raw]
    for layer in layers:
        remember(layer)
    rootfs = config_doc.get("rootfs")
    if (not isinstance(rootfs, dict) or rootfs.get("type") != "layers"
            or not isinstance(rootfs.get("diff_ids"), list)
            or len(rootfs["diff_ids"]) != len(layers)
            or not all(isinstance(d, str) and DIGEST.fullmatch(d)
                       for d in rootfs["diff_ids"])):
        raise CollectionError("CONFIG_ROOTFS_RELATION_MISMATCH")

    return {
        "name": name, "reference": ref, "approved_top_digest": top_digest,
        "top_media_type": top_type, "index_digest": index_digest,
        "linux_amd64_manifest_digest": manifest_digest,
        "platform_from_verified_config": {"os": "linux", "architecture": "amd64"},
        "config": config, "layers": layers,
        "linux_amd64_referenced_content_bytes_unique": sum(content_sizes.values()),
        "size_basis": "DESCRIPTORS_PLUS_READ_DOCUMENT_BYTES_NOT_ARCHIVE_SIZE",
        "archive_size_bytes": None,
        "layer_bytes_verified": False, "original_supply_provenance_verified": False,
        "export_import_executed": False,
    }


def stderr_category(raw, returncode, error):
    if error:
        return error
    if not raw:
        return "EMPTY"
    if returncode == 0:
        return "STDERR_PRESENT_ON_SUCCESS"
    lower = raw.lower()
    if b"permission denied" in lower or b"operation not permitted" in lower:
        return "PERMISSION"
    if b"not found" in lower or b"no such file" in lower:
        return "NOT_FOUND"
    if b"connection refused" in lower or b"dial unix" in lower:
        return "SOCKET_CONNECTION"
    return "COMMAND_FAILED_UNCLASSIFIED"


def capture_command(argv, timeout_seconds):
    """Bound both in-memory streams; never write subprocess output to disk."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    wake = threading.Event()
    streams = {
        "stdout": {"raw": bytearray(), "complete": False, "error": None},
        "stderr": {"raw": bytearray(), "complete": False, "error": None},
    }

    def drain(name, pipe, limit):
        state = streams[name]
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    state["complete"] = True
                    break
                room = limit - len(state["raw"])
                state["raw"].extend(chunk[:room])
                if len(chunk) > room:
                    state["error"] = name.upper() + "_SIZE_LIMIT"
                    wake.set()
                    break
        except OSError:
            state["error"] = "STREAM_READ_FAILED"
            wake.set()
        finally:
            pipe.close()

    threads = []
    for name, pipe, limit in (("stdout", process.stdout, MAX_DOCUMENT),
                               ("stderr", process.stderr, MAX_STDERR)):
        thread = threading.Thread(target=drain, args=(name, pipe, limit), daemon=True)
        thread.start()
        threads.append(thread)
    deadline = time.monotonic() + timeout_seconds
    error = None
    while True:
        error = next((s["error"] for s in streams.values() if s["error"]), None)
        if error or process.poll() is not None:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            error = "READ_TIMEOUT"
            break
        wake.wait(min(0.02, remaining))
    if error:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    cleanup_deadline = time.monotonic() + 2
    try:
        process.wait(timeout=max(0.01, cleanup_deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        error = error or "PROCESS_REAP_TIMEOUT"
    for thread in threads:
        thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
    error = error or next((s["error"] for s in streams.values() if s["error"]), None)
    if not all(s["complete"] for s in streams.values()):
        error = error or "STREAM_CAPTURE_INCOMPLETE"
    return {"stdout": bytes(streams["stdout"]["raw"]),
            "stderr": bytes(streams["stderr"]["raw"]),
            "stdout_complete": streams["stdout"]["complete"],
            "stderr_complete": streams["stderr"]["complete"],
            "returncode": process.returncode, "error": error}


class CacheReader:
    def __init__(self, ctr_path):
        self.ctr = ctr_path
        self.deadline = time.monotonic() + 45
        self.commands = []
        self.executables = {}

    def resolve_binary(self, name):
        found = shutil.which(name)
        if not found:
            raise CollectionError("EXISTING_" + name.upper() + "_REQUIRED")
        try:
            path = Path(found).resolve(strict=True)
            if not stat.S_ISREG(path.stat().st_mode):
                raise CollectionError("EXECUTABLE_REGULAR_FILE_REQUIRED")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                while True:
                    if time.monotonic() >= self.deadline:
                        raise CollectionError("COLLECTION_TIME_BUDGET_EXCEEDED")
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
            self.executables[name] = {"resolved_path": str(path),
                                      "sha256": digest.hexdigest(), "size_bytes": size}
            return str(path)
        except OSError:
            raise CollectionError("EXECUTABLE_IDENTITY_QUERY_FAILED") from None

    def run(self, argv, timeout_seconds=10):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CollectionError("COLLECTION_TIME_BUDGET_EXCEEDED")
        record = {"argv": argv, "returncode": None,
                  "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        self.commands.append(record)
        start = time.monotonic()
        try:
            result = capture_command(argv, min(timeout_seconds, remaining))
        except OSError:
            record["error"] = "EXEC_ERROR"
            raise CollectionError("CONTENT_EXEC_ERROR") from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - start) * 1000)
            record["finished_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        record["returncode"] = result["returncode"]
        for name in ("stdout", "stderr"):
            raw = result[name]
            record[name + "_size_bytes"] = len(raw)
            record[name + "_sha256"] = hashlib.sha256(raw).hexdigest()
            record[name + "_capture_complete"] = result[name + "_complete"] and not result["error"]
        record["stderr_error_category"] = stderr_category(
            result["stderr"], result["returncode"], result["error"])
        if result["error"]:
            record["error"] = result["error"]
            raise CollectionError("CONTENT_" + result["error"])
        if result["returncode"] != 0:
            raise CollectionError("CONTENT_READ_FAILED")
        if not 0 < len(result["stdout"]) <= MAX_DOCUMENT:
            raise CollectionError("DOCUMENT_SIZE_LIMIT")
        return result["stdout"]

    def __call__(self, digest):
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise CollectionError("INVALID_DIGEST")
        return self.run([self.ctr, "--address", CONTAINERD_SOCKET,
                         "--namespace", "k8s.io", "content", "get", digest])

    def version(self):
        raw = self.run([self.ctr, "--version"])
        if len(raw) > 4096:
            raise CollectionError("VERSION_OUTPUT_SIZE_LIMIT")
        match = re.search(rb"\bv?([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)", raw)
        if not match:
            raise CollectionError("CTR_VERSION_NOT_IDENTIFIED")
        return match.group(1).decode("ascii")


def host_gate(reader=None):
    if platform.system() != "Linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise CollectionError("LINUX_EXISTING_ROOT_CONTEXT_REQUIRED")
    hostname = socket.gethostname()
    if hostname.split(".", 1)[0] not in {"control01", "k8s-control01"}:
        raise CollectionError("CONTROL01_SOURCE_ONLY")
    reader = reader if reader is not None else CacheReader(None)
    try:
        if not stat.S_ISSOCK(os.stat(CONTAINERD_SOCKET).st_mode):
            raise CollectionError("EXISTING_CONTAINERD_SOCKET_REQUIRED")
    except FileNotFoundError:
        raise CollectionError("EXISTING_CONTAINERD_SOCKET_MISSING") from None
    except OSError:
        raise CollectionError("CONTAINERD_SOCKET_QUERY_FAILED") from None
    try:
        ip_path = reader.resolve_binary("ip")
        raw = reader.run([ip_path, "-j", "-4", "address", "show"], timeout_seconds=5)
        addresses = json.loads(raw)
        found = {a.get("local") for item in addresses for a in item.get("addr_info", [])
                 if a.get("family") == "inet"}
        if "10.1.93.102" not in found:
            raise CollectionError("CONTROL01_ADDRESS_MISMATCH")
    except (ValueError, TypeError, AttributeError):
        raise CollectionError("HOST_IDENTITY_QUERY_FAILED") from None
    return hostname, reader.resolve_binary("ctr")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True,
                        help="Reviewed checkout commit declaration, 40 lowercase hex")
    args = parser.parse_args(argv)
    if not COMMIT.fullmatch(args.source_commit):
        parser.error("source-commit must be a full lowercase 40-character SHA")
    now = lambda: dt.datetime.now(dt.timezone.utc).isoformat()
    result = {
        "schema": "OID02-cache-metadata-readonly-2", "started_at_utc": now(),
        "source_commit_declared": args.source_commit,
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "host": None, "address": None, "expected_source_address": "10.1.93.102",
        "effective_uid": None, "ctr_version": None,
        "containerd_socket": CONTAINERD_SOCKET,
        "namespace": "k8s.io", "status": "INCOMPLETE", "images": [], "commands": [],
        "runtime_changed": False, "plan_ready": False, "oid02_ac_accepted": False,
    }
    reader = CacheReader(None)
    try:
        hostname, ctr = host_gate(reader)
        result["host"] = hostname
        result["address"] = "10.1.93.102"
        result["effective_uid"] = os.geteuid()
        reader.ctr = ctr
        result["ctr_version"] = reader.version()
        for name, ref, digest in TARGETS:
            result["images"].append(inspect_image(reader, name, ref, digest))
        result["status"] = "MANIFEST_METADATA_VERIFIED_REVIEW_REQUIRED"
    except CollectionError as error:
        result["error"] = str(error)
    except Exception:
        result["error"] = "UNEXPECTED_COLLECTION_ERROR"
    finally:
        if reader:
            result["commands"] = reader.commands
            result["executables"] = reader.executables
        result["finished_at_utc"] = now()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "MANIFEST_METADATA_VERIFIED_REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
