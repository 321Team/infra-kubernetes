#!/usr/bin/env python3
"""Fixed-scope read-only Worker firewall, six-node NTP and TCP evidence."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import time

CP = {"k8s-control01": "10.1.93.102", "k8s-control02": "10.1.93.103", "k8s-control03": "10.1.93.104"}
WORKERS = {"k8s-worker01": "10.1.93.105", "k8s-worker02": "10.1.93.107", "k8s-worker03": "10.1.93.108"}
NODES = dict(CP, **WORKERS)
CONFIG_DIRS = ("/etc/chrony.d", "/run/chrony-dhcp")
MAX_BYTES = 256 * 1024


def need(value, message):
    if not value:
        raise ValueError(message)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def redact(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    text = str(value)
    text = re.sub(r"-----BEGIN [^-\r\n]+-----.*?(?:-----END [^-\r\n]+-----|\Z)", "[REDACTED_PEM]", text, flags=re.S)
    text = re.sub(r"(?<![A-Za-z0-9])[a-z0-9]{6}\.[a-z0-9]{16}(?![A-Za-z0-9])", "[REDACTED_TOKEN]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED_JWT]", text)
    text = re.sub(r"(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9_./+=-]+", r"\1[REDACTED]", text)
    text = re.sub(r'''(?ix)((?:--)?(?:certificate-key|token|password|client-key-data|client-certificate-data)["']?\s*[=:]\s*["']?)([^\s,;"']+)''', r"\1[REDACTED]", text)
    return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)


def trusted(path, directory=False):
    path = Path(path)
    need(path.is_absolute(), "path is not absolute")
    for parent in reversed(path.parents):
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "untrusted parent: " + str(parent))
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    need(expected(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "untrusted file: " + str(path))
    return info


def binary(name):
    need(re.fullmatch(r"[a-z0-9-]+", name), "invalid binary name")
    for parent in ("/usr/sbin", "/usr/bin"):
        path = Path(parent) / name
        if path.exists():
            path = path.resolve(strict=True)
            info = trusted(path)
            need(info.st_mode & 0o111, "binary not executable")
            return str(path)
    raise FileNotFoundError("binary unavailable: " + name)


def command(argv, timeout=15):
    result = {"argv": argv, "started_at": now()}
    try:
        proc = subprocess.run([binary(argv[0])] + argv[1:], capture_output=True, text=True, timeout=timeout,
                              env={"PATH": "/usr/sbin:/usr/bin", "LANG": "C", "LC_ALL": "C"})
        result.update(rc=proc.returncode, stdout=redact(proc.stdout), stderr=redact(proc.stderr))
    except subprocess.TimeoutExpired as error:
        result.update(rc=124, stdout=redact(error.stdout or ""), stderr=redact(error.stderr or "") + "\nCOMMAND_TIMEOUT")
    except (OSError, ValueError) as error:
        result.update(rc=127, stdout="", stderr=redact(error))
    for key in ("stdout", "stderr"):
        raw = result[key].encode()
        if len(raw) > MAX_BYTES:
            result[key] = raw[:MAX_BYTES].decode("utf-8", "ignore")
            result[key + "_truncated"] = True
    result["ended_at"] = now()
    return result


def source_lines(text):
    """Store endpoint/reference directives, never whole config or authentication options."""
    lines = []
    for number, line in enumerate(text.splitlines(), 1):
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            # Record a parse gap without retaining a potentially sensitive line.
            lines.append({"line": number, "parse_error": True})
            continue
        if not words or words[0].startswith(("!", ";", "%")):
            continue
        directive = words[0].lower()
        if directive in ("server", "pool", "peer", "include", "confdir", "sourcedir"):
            lines.append({"line": number, "directive": directive,
                          "arguments": [redact(x) for x in (words[1:] if directive in ("include", "confdir", "sourcedir") else words[1:2])]})
        elif directive in ("local", "refclock"):
            lines.append({"line": number, "directive": directive, "present": True})
    return lines


def chrony_config():
    result = {"files": {}, "directories": {}, "gaps": [], "scope": ["/etc/chrony.conf"] + list(CONFIG_DIRS)}
    paths = [Path("/etc/chrony.conf")]
    for name in CONFIG_DIRS:
        directory = Path(name)
        try:
            trusted(directory, directory=True)
            candidates = sorted(p for p in directory.iterdir() if p.suffix in (".conf", ".sources"))
            need(len(candidates) <= 64, "too many chrony source files")
            paths.extend(candidates)
            result["directories"][name] = "enumerated"
        except FileNotFoundError:
            result["directories"][name] = "absent"
        except (OSError, ValueError) as error:
            result["directories"][name] = "unreadable"
            result["gaps"].append(redact(error))
    for path in paths:
        try:
            info = trusted(path)
            need(info.st_size <= 128 * 1024, "chrony configuration exceeds bound")
            # O_NOFOLLOW prevents final-component symlink swaps after validation.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                opened = os.fstat(stream.fileno())
                need((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), "chrony configuration changed during read")
                raw = stream.read(128 * 1024 + 1)
            need(len(raw) <= 128 * 1024, "chrony configuration grew beyond bound")
            entries = source_lines(raw.decode("utf-8", "replace"))
            result["files"][str(path)] = {"sha256": hashlib.sha256(raw).hexdigest(), "source_directives": entries}
            for entry in entries:
                if entry.get("parse_error"):
                    result["gaps"].append("configuration parse error: " + str(path) + ":" + str(entry["line"]))
                if entry.get("directive") in ("include", "confdir", "sourcedir"):
                    args = entry.get("arguments", [])
                    known = all(arg in CONFIG_DIRS for arg in args) and entry["directive"] != "include"
                    if not known:
                        result["gaps"].append("unresolved configuration reference: " + str(path) + ":" + str(entry["line"]))
        except (OSError, ValueError) as error:
            result["files"][str(path)] = {"error": redact(error)}
            result["gaps"].append("configuration unavailable: " + str(path))
    return result


def probe_targets(name):
    need(name in NODES, "unknown node")
    if name in CP:
        return [(worker, ip, 10250, "IF-12B") for worker, ip in WORKERS.items()]
    return [("k8s-lb01", "10.1.93.109", 6443, "IF-11A")]


def tcp_probe(target):
    name, ip, port, interface = target
    started = time.monotonic()
    result = {"destination": name, "ip": ip, "port": port, "interface": interface, "started_at": now()}
    try:
        with socket.create_connection((ip, port), timeout=3) as connection:
            result.update(connected=True, local_address=list(connection.getsockname()))
    except OSError as error:
        result.update(connected=False, errno=error.errno, error=redact(error))
    result.update(duration_ms=round((time.monotonic() - started) * 1000), ended_at=now())
    return result


def time_state_gaps(query):
    fields = dict(line.split("=", 1) for line in query.get("stdout", "").splitlines() if "=" in line)
    return ["time_state missing property: " + key for key in ("Timezone", "NTPSynchronized", "TimeUSec") if not fields.get(key)]


def kubelet_metadata():
    """Inspect only fixed path metadata; never open kubeconfig or bootstrap contents."""
    result = {"paths": {}, "gaps": []}
    for name in ("/var/lib/kubelet/config.yaml", "/var/lib/kubelet/kubeadm-flags.env",
                 "/etc/kubernetes/kubelet.conf", "/etc/kubernetes/bootstrap-kubelet.conf"):
        try:
            info = Path(name).lstat()
            result["paths"][name] = {"exists": True, "mode": oct(stat.S_IMODE(info.st_mode)),
                                     "uid": info.st_uid, "gid": info.st_gid, "size": info.st_size,
                                     "is_regular": stat.S_ISREG(info.st_mode), "is_symlink": stat.S_ISLNK(info.st_mode),
                                     "mtime_ns": info.st_mtime_ns}
        except FileNotFoundError:
            result["paths"][name] = {"exists": False}
        except OSError as error:
            result["paths"][name] = {"error": redact(error)}
            result["gaps"].append("metadata unavailable: " + name)
    return result


def collect(request):
    name = request.get("node")
    mode = request.get("mode")
    need(name in NODES and mode in ("inspect", "supplement"), "unsupported request")
    need(os.geteuid() == 0, "remote helper requires existing sudo access")
    need(socket.gethostname().split(".")[0] == name, "hostname differs")
    result = {"schema": "fds.worker-followup-node.v1", "hostname": name, "started_at": now(),
              "status": "STARTING", "mode": mode, "runtime_applied": "not_attempted", "queries": {}, "gaps": []}
    queries = result["queries"]
    queries["identity_ipv4"] = command(["ip", "-j", "-4", "address", "show", "dev", "ens160"])
    identity = queries["identity_ipv4"]
    if identity["rc"] != 0 or identity.get("stdout_truncated"):
        result.update(status="STOP", gaps=["interface identity query failed"], ended_at=now())
        return result
    try:
        ips = {entry["local"] for link in json.loads(identity["stdout"]) for entry in link.get("addr_info", []) if entry.get("family") == "inet"}
        need(NODES[name] in ips, "expected ens160 IPv4 missing")
    except (ValueError, KeyError, TypeError) as error:
        result.update(status="STOP", gaps=[redact(error)], ended_at=now())
        return result
    fixed = {
        "chrony_tracking": ["chronyc", "-n", "tracking"],
        "chrony_sources_numeric": ["chronyc", "-n", "sources", "-v"],
        "chrony_sources_original": ["chronyc", "-N", "sources", "-v"],
        "time_state": ["timedatectl", "show"],
        "chronyd_state": ["systemctl", "show", "chronyd.service", "--property=LoadState,ActiveState,SubState,NRestarts"],
    }
    if mode == "supplement":
        fixed = {"time_state": fixed["time_state"]}
        if name in WORKERS:
            fixed.update({
                "kubelet_state": ["systemctl", "show", "kubelet.service", "--property=Id,LoadState,ActiveState,SubState,UnitFileState,NRestarts"],
                "kubelet_journal": ["journalctl", "-u", "kubelet.service", "-n", "120", "--no-pager", "-o", "short-iso-precise"],
                "cockpit_units": ["systemctl", "show", "cockpit.socket", "cockpit.service", "--property=Id,LoadState,ActiveState,SubState,UnitFileState"],
                "listeners": ["ss", "-lntp"],
            })
    if name in WORKERS and mode == "inspect":
        fixed.update({
            "selinux": ["getenforce"],
            "routes": ["ip", "-4", "route", "show"],
            "listeners": ["ss", "-lntp"],
            "services": ["systemctl", "show", "kubelet.service", "containerd.service", "firewalld.service", "--property=Id,LoadState,ActiveState,SubState,NRestarts"],
            "nm_device": ["nmcli", "-t", "-f", "GENERAL.DEVICE,GENERAL.STATE,GENERAL.CONNECTION,GENERAL.CON-UUID", "device", "show", "ens160"],
            "nm_active": ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
            "firewall_state": ["firewall-cmd", "--state"],
            "firewall_active_zones": ["firewall-cmd", "--get-active-zones"],
            "firewall_default_zone": ["firewall-cmd", "--get-default-zone"],
            "firewall_interface_zone": ["firewall-cmd", "--get-zone-of-interface=ens160"],
            "firewall_active_policies": ["firewall-cmd", "--get-active-policies"],
            "nft_ruleset": ["nft", "-a", "list", "ruleset"],
        })
        for plane in ("runtime", "permanent"):
            base = ["firewall-cmd"] + (["--permanent"] if plane == "permanent" else [])
            for label, args in (("zones", ["--list-all-zones"]), ("policies", ["--list-all-policies"]),
                                ("direct_rules", ["--direct", "--get-all-rules"]),
                                ("direct_passthroughs", ["--direct", "--get-all-passthroughs"])):
                fixed["firewall_" + plane + "_" + label] = base + args
    for label, argv in fixed.items():
        queries[label] = command(argv)
    result["gaps"].extend(time_state_gaps(queries["time_state"]))
    if name in WORKERS and mode == "inspect" and queries["nm_device"]["rc"] == 0:
        match = re.search(r"^GENERAL\.CON-UUID:([0-9a-fA-F-]{36})$", queries["nm_device"]["stdout"], re.M)
        if match:
            queries["nm_connection_zone"] = command(["nmcli", "-g", "connection.id,connection.uuid,connection.interface-name,connection.zone", "connection", "show", "uuid", match.group(1)])
        else:
            result["gaps"].append("active NM connection UUID unavailable")
    if mode == "inspect":
        result["chrony_config"] = chrony_config()
        result["gaps"].extend(result["chrony_config"]["gaps"])
        result["tcp_probes"] = [tcp_probe(target) for target in probe_targets(name)]
    else:
        result["tcp_probes"] = []
        if name in WORKERS:
            result["kubelet_metadata"] = kubelet_metadata()
            result["gaps"].extend(result["kubelet_metadata"]["gaps"])
    result["failed_queries"] = [label for label, value in queries.items() if value["rc"] != 0 or value.get("stdout_truncated") or value.get("stderr_truncated")]
    result["status"] = "INCOMPLETE_REVIEW_REQUIRED" if result["gaps"] or result["failed_queries"] else "COLLECTED_REVIEW_REQUIRED"
    result["ended_at"] = now()
    return result


if __name__ == "__main__":
    try:
        output = collect(REQUEST)
    except Exception as error:
        output = {"status": "STOP", "error": redact(error), "runtime_applied": "not_attempted", "ended_at": now()}
    print(encoded(output).decode(), end="")
