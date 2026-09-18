"""Bounded, configuration-read-only CP2/CP3 observations; no readiness verdict."""
import datetime as dt
import hashlib
import ipaddress
import itertools
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
from urllib.parse import urlsplit

EXPECTED = {"k8s-control02": "10.1.93.103", "k8s-control03": "10.1.93.104"}
PATH_FLAGS = {"config", "kubeconfig", "pod-manifest-path", "data-dir", "cert-dir"}
IP_FLAGS = {"advertise-address", "bind-address", "node-ip"}
PORT_FLAGS = {"secure-port", "healthz-port", "read-only-port"}
MANIFESTS = Path("/etc/kubernetes/manifests")
MAX_FILE = 262144
PATH_RX = re.compile(r"/[A-Za-z0-9_./+-]+\Z")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def run(argv, timeout=10):
    try:
        p = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout,
                           env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin", "LC_ALL": "C"})
        if len(p.stdout.encode()) > 2 * 1024 * 1024:
            return {"rc": None, "stdout": "", "error": "OUTPUT_LIMIT_EXCEEDED",
                    "command_rc": p.returncode}
        return {"rc": p.returncode, "stdout": p.stdout,
                "stderr_sha256": sha(p.stderr.encode()), "stderr_bytes": len(p.stderr.encode())}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"rc": None, "stdout": "", "error": type(exc).__name__}


def public_result(result):
    return {key: value for key, value in result.items() if key != "stdout"}


def safe_path(value):
    return bool(PATH_RX.fullmatch(value)) and ".." not in Path(value).parts and len(value) < 512


def safe_value(flag, value):
    if flag in PATH_FLAGS:
        return value if safe_path(value) else None
    if flag in IP_FLAGS:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            return None
    if flag in PORT_FLAGS:
        return int(value) if value.isdigit() and 0 <= int(value) <= 65535 else None
    if flag == "etcd-servers":
        servers = []
        for entry in value.split(","):
            try:
                u = urlsplit(entry)
                if (u.scheme not in ("http", "https") or not u.hostname or
                        u.username or u.password or u.query or u.fragment or u.path not in ("", "/")):
                    return None
                host = str(ipaddress.ip_address(u.hostname))
                if u.port is None:
                    return None
                servers.append({"scheme": u.scheme, "ip": host, "port": u.port})
            except ValueError:
                return None
        return servers
    return None


def safe_flags(text):
    observed = []
    # Textual extraction only; this does not claim YAML/schema validation.
    for match in re.finditer(r"--([a-z][a-z0-9-]*)[=\s]+([^\s\"'\]};]+)", text):
        flag, raw = match.groups()
        if flag in PATH_FLAGS | IP_FLAGS | PORT_FLAGS | {"etcd-servers"}:
            value = safe_value(flag, raw)
            observed.append({"flag": flag, "value": value,
                             "parse": "ALLOWLISTED" if value is not None else "UNPARSED_VALUE_WITHHELD"})
    return observed


def metadata(path):
    try:
        s = path.lstat()
        return {"path": str(path), "exists": True, "mode": oct(stat.S_IMODE(s.st_mode)),
                "uid": s.st_uid, "gid": s.st_gid, "size": s.st_size,
                "mtime_ns": s.st_mtime_ns,
                "kind": "symlink" if stat.S_ISLNK(s.st_mode) else
                        "file" if stat.S_ISREG(s.st_mode) else
                        "directory" if stat.S_ISDIR(s.st_mode) else "other"}
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    except OSError as exc:
        return {"path": str(path), "exists": None, "error": type(exc).__name__}


def read_owned_text(path):
    # Refuse symlinked or writable parents and refuse device/large/unowned inputs.
    for parent in list(path.parents)[:-1]:
        s = parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError("untrusted parent")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        first = os.fstat(fd)
        if (not stat.S_ISREG(first.st_mode) or first.st_uid != 0 or first.st_mode & 0o022 or
                first.st_size > MAX_FILE):
            raise ValueError("untrusted file")
        raw = b""
        while len(raw) <= MAX_FILE:
            chunk = os.read(fd, min(65536, MAX_FILE + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        last = os.fstat(fd)
        if len(raw) > MAX_FILE or (first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns) != (
                last.st_ino, last.st_size, last.st_mtime_ns, last.st_ctime_ns):
            raise ValueError("changed during read")
        return raw.decode("utf-8"), sha(raw)
    finally:
        os.close(fd)


def trusted_directory(path):
    # lstat each ancestor: O_NOFOLLOW on a final file alone does not protect
    # against a symlink earlier in a path during directory enumeration.
    for p in reversed([path] + list(path.parents)):
        s = p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError("untrusted directory path")


def directory_inventory(path, parse=False):
    out = {"directory": metadata(path), "files": [], "text_parse": "ALLOWLISTED_TEXT_ONLY"}
    if not out["directory"].get("exists"):
        return out
    try:
        if out["directory"]["kind"] != "directory":
            raise ValueError("directory symlink or other")
        trusted_directory(path)
        items = sorted(itertools.islice(path.iterdir(), 65))
        if len(items) > 64:
            out["truncated"] = True
        for p in items[:64]:
            item = metadata(p)
            item["dotfile"] = p.name.startswith(".")
            if parse and item.get("kind") == "file":
                try:
                    txt, digest = read_owned_text(p)
                    item["sha256"] = digest
                    item["flags"] = safe_flags(txt)
                    item["path_scalars"] = sorted(set(m.group(1) for m in re.finditer(
                        r"(?m)^\s*(?:path|staticPodPath):\s*[\"']?(/[A-Za-z0-9_./+-]+)[\"']?\s*$", txt)
                        if safe_path(m.group(1))))
                except (OSError, ValueError, UnicodeError) as exc:
                    item["content_read"] = "WITHHELD_" + type(exc).__name__
            out["files"].append(item)
    except (OSError, ValueError) as exc:
        out["error"] = type(exc).__name__
    return out


def scalar_version(argv):
    r = run(argv)
    # Never copy arbitrary stderr/command output into shared evidence.
    versions = re.findall(r"(?<![\w.])v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", r["stdout"])
    return {**public_result(r), "versions": versions[:4],
            "parse": "VERSION_OBSERVED" if r["rc"] == 0 and versions else "INCOMPLETE"}


def package_provenance(root=None):
    root = root if root is not None else Path("/opt/fds/P1-K8S-01-v4")
    out = {"root": metadata(root), "files": [], "markers_are_execution_history": False,
           "relationship_to_static_manifests": "NOT_ESTABLISHED",
           "scan_limits": {"depth": 4, "files": 128, "total_bytes": 4194304}, "limitations": []}
    if not out["root"].get("exists"):
        return out
    markers = {"kubeadm_join": r"kubeadm[\s\"',\[\]]{1,30}join",
               "kubeadm_init": r"kubeadm[\s\"',\[\]]{1,30}init",
               "kubeadm_reset": r"kubeadm[\s\"',\[\]]{1,30}reset",
               "control_plane_prepare": r"control-plane-prepare",
               "static_manifest_directory": r"/etc/kubernetes/manifests",
               "static_pod_path_setting": r"staticPodPath|pod-manifest-path",
               "control_plane_manifest_name": r"kube-(?:apiserver|controller-manager|scheduler)\.yaml"}
    queue, visited, read_bytes, budgeted_bytes = [(root, 0)], 0, 0, 0
    while queue and len(out["files"]) < 128 and visited < 32:
        directory, depth = queue.pop(0)
        visited += 1
        try:
            trusted_directory(directory)
            with os.scandir(directory) as scan:
                entries = []
                for entry in scan:
                    entries.append(entry.name)
                    if len(entries) == 129:
                        out["limitations"].append("DIRECTORY_ENTRY_LIMIT:" + str(directory))
                        break
            for entry in sorted(entries[:128]):
                path = directory / entry
                m = metadata(path)
                if m.get("kind") == "directory":
                    if depth < 4:
                        queue.append((path, depth + 1))
                    else:
                        out["limitations"].append("DEPTH_LIMIT:" + str(path))
                    continue
                if m.get("kind") == "symlink":
                    out["limitations"].append("SYMLINK_SKIPPED:" + str(path))
                    continue
                if path.suffix.lower() not in {".py", ".sh", ".yaml", ".yml", ".json", ".md"}:
                    continue
                if len(out["files"]) >= 128:
                    break
                m["role"] = "SCRIPT_TEXT" if path.suffix.lower() in {".py", ".sh"} else "CONFIG_OR_DOCUMENT_TEXT"
                try:
                    file_budget = m.get("size", MAX_FILE + 1)
                    if budgeted_bytes + file_budget > 4194304:
                        raise ValueError("total byte limit")
                    budgeted_bytes += file_budget
                    text, file_sha = read_owned_text(path)
                    read_bytes += len(text.encode())
                    m["sha256"] = file_sha
                    m["text_markers"] = {key: ("FOUND_IN_TEXT" if re.search(pattern, text) else
                        "NOT_FOUND_IN_SCANNED_TEXT") for key, pattern in markers.items()}
                except (OSError, ValueError, UnicodeError) as exc:
                    m["content_read"] = "WITHHELD_" + type(exc).__name__
                out["files"].append(m)
        except (OSError, ValueError) as exc:
            out["limitations"].append(type(exc).__name__ + ":" + str(directory))
    if queue or len(out["files"]) >= 128:
        out["limitations"].append("SCAN_LIMIT_REACHED")
    out["bytes_read"] = read_bytes
    out["bytes_budgeted"] = budgeted_bytes
    out["negative_marker_result_proves_no_relationship"] = False
    return out


def collect(name, ip):
    if EXPECTED.get(name) != ip or os.geteuid() != 0:
        raise ValueError("invalid target or effective user")
    actual = socket.gethostname().split(".", 1)[0]
    if actual != name:
        raise ValueError("target hostname mismatch")
    addresses = run(["ip", "-j", "-4", "address", "show"])
    if addresses["rc"] != 0:
        raise ValueError("target address read failed")
    owned = [a["local"] for iface in json.loads(addresses["stdout"])
             for a in iface.get("addr_info", []) if a.get("family") == "inet"]
    if ip not in owned:
        raise ValueError("target IP mismatch")
    result = {"schema": "fds.cp-prejoin-observation.v1", "hostname": name, "ip": ip,
              "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "configuration_mutation": False, "join_readiness": "NOT_ASSESSED"}
    result["versions"] = {"kubeadm": scalar_version(["kubeadm", "version", "-o", "short"]),
                          "kubelet": scalar_version(["kubelet", "--version"]),
                          "containerd": scalar_version(["containerd", "--version"])}
    result["services"] = {}
    service_flags = []
    for unit in ("kubelet", "containerd"):
        r = run(["systemctl", "show", unit, "--property=LoadState,ActiveState,SubState,ExecStart"])
        values = dict(line.split("=", 1) for line in r["stdout"].splitlines() if "=" in line)
        item = {**public_result(r), **{k: v for k, v in values.items()
                if k in ("LoadState", "ActiveState", "SubState") and re.fullmatch(r"[a-z-]+", v)}}
        item["exec_allowlisted_flags"] = safe_flags(values.get("ExecStart", ""))
        result["services"][unit] = item
        if unit == "kubelet":
            service_flags.extend(item["exec_allowlisted_flags"])
    residual_paths = ["/var/lib/kubelet/config.yaml", "/var/lib/kubelet/kubeadm-flags.env",
                      "/etc/kubernetes/kubelet.conf", "/etc/kubernetes/bootstrap-kubelet.conf",
                      "/var/lib/etcd", "/var/lib/etcd/member", "/etc/kubernetes/manifests/etcd.yaml"]
    result["known_paths"] = [metadata(Path(p)) for p in residual_paths]
    result["manifests"] = directory_inventory(MANIFESTS, parse=(name == "k8s-control02"))
    result["kubelet_config_paths"] = []
    configs = {"/var/lib/kubelet/config.yaml"}
    candidate_manifest_dirs = set()
    for f in service_flags:
        if f["flag"] == "config" and isinstance(f["value"], str):
            configs.add(f["value"])
        if f["flag"] == "pod-manifest-path" and isinstance(f["value"], str):
            candidate_manifest_dirs.add(f["value"])
    # Environment files are read only for allowlisted flags, never emitted whole.
    for file in ("/var/lib/kubelet/kubeadm-flags.env", "/etc/sysconfig/kubelet"):
        item = metadata(Path(file))
        if item.get("exists"):
            try:
                text, digest = read_owned_text(Path(file))
                item.update(sha256=digest, flags=safe_flags(text))
                for f in item["flags"]:
                    if f["flag"] == "config" and isinstance(f["value"], str):
                        configs.add(f["value"])
                    if f["flag"] == "pod-manifest-path" and isinstance(f["value"], str):
                        candidate_manifest_dirs.add(f["value"])
            except (OSError, ValueError, UnicodeError) as exc:
                item["content_read"] = "WITHHELD_" + type(exc).__name__
        result.setdefault("kubelet_flag_files", []).append(item)
    for file in sorted(configs):
        item = metadata(Path(file))
        if item.get("exists"):
            try:
                text, digest = read_owned_text(Path(file))
                item["sha256"] = digest
                matches = re.findall(r"(?m)^\s*staticPodPath:\s*[\"']?(/[A-Za-z0-9_./+-]+)[\"']?\s*$", text)
                item["staticPodPath"] = [p for p in matches if safe_path(p)]
                candidate_manifest_dirs.update(item["staticPodPath"])
                item["parse"] = "ALLOWLISTED_TEXT_ONLY"
            except (OSError, ValueError, UnicodeError) as exc:
                item["content_read"] = "WITHHELD_" + type(exc).__name__
        result["kubelet_config_paths"].append(item)
    result["candidate_manifest_directories"] = [
        directory_inventory(Path(p), parse=(name == "k8s-control02"))
        for p in sorted(candidate_manifest_dirs) if Path(p) != MANIFESTS]
    result["effective_kubelet_configuration"] = "NOT_FULLY_RESOLVED_SYSTEMD_ENVIRONMENT_MAY_OVERRIDE"
    ports = run(["ss", "-H", "-lnt"])
    result["listeners"] = {**public_result(ports), "selected": []}
    for line in ports["stdout"].splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[0] == "LISTEN":
            address = fields[3]
            if address.rsplit(":", 1)[-1] in {"6443", "2379", "2380", "10250", "10257", "10259"}:
                result["listeners"]["selected"].append(address)
    cri = run(["crictl", "--runtime-endpoint", "unix:///run/containerd/containerd.sock",
               "--timeout", "8", "ps", "-a", "-o", "json"], timeout=12)
    result["containers"] = {**public_result(cri), "endpoint": "unix:///run/containerd/containerd.sock",
                            "selected": [], "other_runtime_endpoints": "NOT_INSPECTED",
                            "parse": "INCOMPLETE"}
    if cri["rc"] == 0:
        try:
            parsed = json.loads(cri["stdout"])
            if not isinstance(parsed, dict) or not isinstance(parsed.get("containers"), list):
                raise ValueError("container collection absent")
            if len(parsed["containers"]) > 4096:
                raise ValueError("container collection exceeds limit")
            selected = []
            for c in parsed["containers"]:
                if not isinstance(c, dict) or not isinstance(c.get("metadata"), dict):
                    raise ValueError("container record malformed")
                cn = c.get("metadata", {}).get("name", "")
                if cn in {"kube-apiserver", "kube-controller-manager", "kube-scheduler", "etcd", "kube-proxy"}:
                    container_id, state, created_at = c.get("id"), c.get("state"), c.get("createdAt")
                    if (not isinstance(container_id, str) or not re.fullmatch(r"[0-9a-f]{12,128}", container_id) or
                            state not in {"CONTAINER_CREATED", "CONTAINER_RUNNING", "CONTAINER_EXITED", "CONTAINER_UNKNOWN"} or
                            not (type(created_at) is int or isinstance(created_at, str) and created_at.isdecimal())):
                        raise ValueError("container fields malformed")
                    selected.append({"name": cn, "state": state,
                        "container_id": container_id, "created_at": created_at})
            result["containers"].update(selected=selected, parse="CONTAINER_LIST_OBSERVED")
        except (ValueError, TypeError, AttributeError):
            result["containers"]["parse_error"] = True
    try:
        with socket.create_connection(("10.1.93.109", 6443), timeout=3,
                                      source_address=(ip, 0)):
            result["lb_tcp"] = {"source_ip": ip, "target": "10.1.93.109:6443", "connect": "PASS",
                                "api_tls_auth_backend_health": "NOT_TESTED"}
    except OSError as exc:
        result["lb_tcp"] = {"source_ip": ip, "target": "10.1.93.109:6443", "connect": "FAIL",
                            "error": type(exc).__name__, "errno": exc.errno}
    result["v4_package_provenance"] = package_provenance()
    result["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(collect(sys.argv[1], sys.argv[2]), sort_keys=True, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "COLLECTION_FAILED", "error": type(exc).__name__,
                          "configuration_mutation": False, "join_readiness": "NOT_ASSESSED"}))
        sys.exit(2)
