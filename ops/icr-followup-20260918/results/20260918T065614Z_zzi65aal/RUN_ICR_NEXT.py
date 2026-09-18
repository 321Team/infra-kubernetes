#!/usr/bin/env python3
"""One collection of current CP/Worker readiness; default is a local plan."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

NODES = {"k8s-control01": "10.1.93.102", "k8s-worker01": "10.1.93.105",
         "k8s-worker02": "10.1.93.107", "k8s-worker03": "10.1.93.108"}
EXPECTED = {"k8s-control01": "10.1.93.102", "k8s-control02": "10.1.93.103",
            "k8s-control03": "10.1.93.104", **{k: v for k, v in NODES.items() if "worker" in k}}
KEY = "/home/server01_ansible/.ssh/p1_ansible_ed25519"
KNOWN_HOSTS = "/home/server01_ansible/.ssh/known_hosts"
SOURCE_IP = "10.250.10.10"

# The payload emits only selected public status fields, never command stderr,
# kubeconfig contents, tokens, certificates, full Pod specs, or environment values.
REMOTE = r'''
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
       "LANG": "C", "LC_ALL": "C", "HOME": "/root"}
queries = {}

def run(label, argv, parse=False):
    record = {"available": False, "argv": argv, "started_at": now()}
    queries[label] = record
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20, env=ENV)
        record["rc"] = p.returncode
        if p.returncode:
            record["error_type"] = "NONZERO_EXIT"
            return None
        value = json.loads(p.stdout) if parse else p.stdout.strip()
        record["available"] = True
        return value
    except subprocess.TimeoutExpired:
        record["error_type"] = "TIMEOUT"
    except FileNotFoundError:
        record["error_type"] = "COMMAND_NOT_FOUND"
    except (ValueError, UnicodeError):
        record["error_type"] = "INVALID_RESPONSE"
    except OSError:
        record["error_type"] = "OS_ERROR"
    finally:
        record["ended_at"] = now()
    return None

def metadata(path):
    try:
        s = Path(path).lstat()
        return {"available": True, "exists": True, "type": "symlink" if stat.S_ISLNK(s.st_mode)
                else "directory" if stat.S_ISDIR(s.st_mode) else "file" if stat.S_ISREG(s.st_mode) else "other",
                "uid": s.st_uid, "mode": oct(stat.S_IMODE(s.st_mode)), "bytes": s.st_size}
    except FileNotFoundError:
        return {"available": True, "exists": False}
    except OSError:
        return {"available": False, "error_type": "OS_ERROR"}

def names(path):
    m = metadata(path)
    if not m.get("exists") or m.get("type") != "directory":
        return {"metadata": m, "names": None}
    try:
        items = sorted(p.name for p in Path(path).iterdir())
        return {"metadata": m, "names": items[:200], "truncated": len(items) > 200}
    except OSError:
        return {"metadata": m, "names": None, "error_type": "OS_ERROR"}

def node_summary(data):
    return [{"name": n["metadata"]["name"], "uid": n["metadata"]["uid"],
             "internal_ips": sorted(a["address"] for a in n.get("status", {}).get("addresses", []) if a["type"] == "InternalIP"),
             "ready": next((c.get("status") for c in n.get("status", {}).get("conditions", []) if c.get("type") == "Ready"), "Unknown"),
             "control_plane": "node-role.kubernetes.io/control-plane" in n["metadata"].get("labels", {}),
             "kubelet_version": n.get("status", {}).get("nodeInfo", {}).get("kubeletVersion")}
            for n in data["items"]]

def cluster(out):
    kube = shutil.which("kubectl", path=ENV["PATH"])
    if not kube:
        out["cluster"] = {"available": False, "error_type": "KUBECTL_NOT_FOUND"}
        return
    base = [kube, "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=10s", "--cache-dir=/dev/null"]
    cfg = run("kubeconfig_safety", base + ["config", "view", "--minify", "-o", "json"], True)
    if cfg is None:
        out["cluster"] = {"available": False, "error_type": "KUBECONFIG_UNAVAILABLE"}
        return
    try:
        c = cfg["clusters"][0]["cluster"]
        users = cfg.get("users", [])
        safety = {"proxy_url_present": bool(c.get("proxy-url")),
                  "insecure_skip_tls_verify": c.get("insecure-skip-tls-verify", False),
                  "tls_server_name_override": bool(c.get("tls-server-name")),
                  "external_auth_plugin": any(u.get("user", {}).get("exec") or u.get("user", {}).get("auth-provider") for u in users),
                  "ca_configured": bool(c.get("certificate-authority-data") or c.get("certificate-authority")),
                  "proxy_environment_removed": True}
        out["kubeconfig_checks"] = safety
        if (safety["proxy_url_present"] or safety["insecure_skip_tls_verify"] is not False
                or safety["tls_server_name_override"] or safety["external_auth_plugin"] or not safety["ca_configured"]):
            out["cluster"] = {"available": False, "error_type": "KUBECONFIG_UNSAFE_FOR_DIRECT_VERIFICATION"}
            return
    except (KeyError, IndexError, TypeError):
        out["cluster"] = {"available": False, "error_type": "KUBECONFIG_STRUCTURE"}
        return
    if not out.get("route_to_lb", {}).get("expected_source"):
        out["cluster"] = {"available": False, "error_type": "LB_SOURCE_UNVERIFIED"}
        return
    lb = base + ["--server=https://10.1.93.109:6443", "--insecure-skip-tls-verify=false"]
    who = run("lb_authenticated_identity", lb + ["auth", "whoami", "-o", "json"], True)
    identity = None
    if who is not None:
        info = who.get("status", {}).get("userInfo", {})
        username = info.get("username")
        groups = info.get("groups", [])
        identity = {"username": username, "groups": groups,
                    "authenticated": isinstance(username, str) and bool(username) and username != "system:anonymous"
                    and isinstance(groups, list) and "system:authenticated" in groups and "system:unauthenticated" not in groups}
    data = run("lb_get_nodes", lb + ["get", "--raw=/api/v1/nodes"], True)
    result = {"available": data is not None, "identity": identity,
              "tls_verification_enforced": True, "node_get_succeeded": data is not None,
              "authenticated_node_access": data is not None and identity is not None and identity["authenticated"],
              "lb_backend_3_of_3": "NOT_TESTED", "lb_failover": "NOT_TESTED"}
    if data is not None:
        try:
            result["nodes"] = node_summary(data)
        except (KeyError, TypeError):
            result.update(available=False, node_get_succeeded=False, authenticated_node_access=False, error_type="NODE_RESPONSE_STRUCTURE")
    out["cluster"] = result
    out["calico_namespaces"] = {}
    ns_data = run("calico_namespace_inventory", lb + ["get", "--raw=/api/v1/namespaces"], True)
    ns_names = {n["metadata"]["name"] for n in ns_data.get("items", [])} if ns_data is not None else None
    for namespace in ("kube-system", "calico-system"):
        if ns_names is None:
            out["calico_namespaces"][namespace] = {"available": False, "status": "UNKNOWN", "pods": None}
            continue
        if namespace not in ns_names:
            out["calico_namespaces"][namespace] = {"available": True, "status": "ABSENT", "pods": []}
            continue
        pods = run("calico_pods_" + namespace, lb + ["get", "--raw=/api/v1/namespaces/" + namespace + "/pods"], True)
        selected = []
        if pods is not None:
            for p in pods.get("items", []):
                m, s = p.get("metadata", {}), p.get("status", {})
                if "calico" not in m.get("name", "") and "calico" not in " ".join(str(v) for v in m.get("labels", {}).values()):
                    continue
                selected.append({"name": m.get("name"), "uid": m.get("uid"), "node": p.get("spec", {}).get("nodeName"),
                                 "phase": s.get("phase"), "ready": next((c.get("status") for c in s.get("conditions", []) if c.get("type") == "Ready"), "Unknown"),
                                 "containers": [{"name": x.get("name"), "ready": x.get("ready"), "restart_count": x.get("restartCount"), "image_id": x.get("imageID")} for x in s.get("containerStatuses", [])]})
        out["calico_namespaces"][namespace] = {"available": pods is not None, "status": "PRESENT" if pods is not None else "UNKNOWN", "pods": selected if pods is not None else None}

def worker(out):
    route = run("route_to_monitor01", ["ip", "-j", "-4", "route", "get", "10.250.10.30"], True)
    out["route_to_monitor01"] = {"available": route is not None and bool(route)}
    if route:
        out["route_to_monitor01"].update(source=route[0].get("prefsrc", route[0].get("src")),
                                       device=route[0].get("dev"), gateway=route[0].get("gateway"),
                                       via_10_1_93_58=route[0].get("gateway") == "10.1.93.58")
    versions = {}
    for tool, args in (("kubeadm", ["version", "-o", "short"]), ("kubelet", ["--version"]), ("containerd", ["--version"])):
        value = run("version_" + tool, [tool] + args)
        match = re.search(r"\bv?(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)", value or "")
        versions[tool] = match.group(0) if match else None
    out["versions"] = versions
    out["services"] = {}
    for service in ("kubelet", "containerd"):
        value = run("service_" + service, ["systemctl", "show", service + ".service", "--property=LoadState,ActiveState,SubState"])
        out["services"][service] = dict(line.split("=", 1) for line in value.splitlines() if "=" in line) if value is not None else None
    out["files"] = {p: metadata(p) for p in ("/var/lib/kubelet/config.yaml", "/var/lib/kubelet/kubeadm-flags.env",
        "/etc/kubernetes/kubelet.conf", "/etc/kubernetes/bootstrap-kubelet.conf", "/etc/kubernetes/pki/ca.crt", "/etc/kubernetes/pki/apiserver.crt")}
    out["directories"] = {p: names(p) for p in ("/etc/kubernetes/manifests", "/etc/cni/net.d")}

def collect():
    out = {"schema": "fds.icr-next-node.v1", "hostname": socket.gethostname().split(".")[0], "expected_node": REQUEST["node"],
           "started_at": now(), "queries": queries, "status": "STARTING", "mutation_attempted": False}
    if os.geteuid() != 0 or out["hostname"] != REQUEST["node"]:
        out.update(status="IDENTITY_MISMATCH", error_type="HOST_OR_SUDO_IDENTITY")
        return out
    addresses = run("identity_ipv4", ["ip", "-j", "-4", "address", "show"], True)
    ips = [a.get("local") for link in addresses or [] for a in link.get("addr_info", []) if a.get("family") == "inet"]
    if REQUEST["ip"] not in ips:
        out.update(status="IDENTITY_MISMATCH", error_type="IP_IDENTITY")
        return out
    out["identity_verified"] = True
    route = run("route_to_lb", ["ip", "-j", "-4", "route", "get", "10.1.93.109"], True)
    if route:
        out["route_to_lb"] = {"source": route[0].get("prefsrc", route[0].get("src")), "device": route[0].get("dev")}
        out["route_to_lb"]["expected_source"] = out["route_to_lb"]["source"] == REQUEST["ip"]
    try:
        cluster(out) if REQUEST["node"] == "k8s-control01" else worker(out)
        complete = all(x["available"] for x in queries.values()) and out.get("route_to_lb", {}).get("expected_source") is True
        if REQUEST["node"] == "k8s-control01":
            complete = complete and out.get("cluster", {}).get("authenticated_node_access") is True
        out["status"] = "COLLECTED" if complete else "COLLECTED_WITH_UNAVAILABLE"
    except Exception:
        out.update(status="COLLECTED_WITH_UNAVAILABLE", error_type="RESPONSE_OR_COLLECTION_ERROR")
    out["ended_at"] = now()
    return out

try:
    result = collect()
except Exception:
    result = {"status": "UNAVAILABLE", "error_type": "COLLECTION_ERROR", "mutation_attempted": False}
print(json.dumps(result, sort_keys=True))
'''


def encoded(data):
    return (json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ssh_argv(ip):
    options = {"BatchMode": "yes", "PreferredAuthentications": "publickey", "PasswordAuthentication": "no",
               "KbdInteractiveAuthentication": "no", "StrictHostKeyChecking": "yes", "UserKnownHostsFile": KNOWN_HOSTS,
               "IdentitiesOnly": "yes", "IdentityAgent": "none", "ControlMaster": "no", "ControlPersist": "no",
               "ControlPath": "none", "ConnectTimeout": "10", "ConnectionAttempts": "1", "ServerAliveInterval": "10",
               "ServerAliveCountMax": "2", "UpdateHostKeys": "no", "PermitLocalCommand": "no",
               "ForwardAgent": "no", "ClearAllForwardings": "yes"}
    argv = ["ssh", "-F", "/dev/null", "-i", KEY, "-b", SOURCE_IP, "-T"]
    for key, value in options.items():
        argv += ["-o", key + "=" + value]
    return argv + ["svc_ansible@" + ip, "sudo -n /usr/bin/python3 -I -B -"]


def remote(name, ip):
    payload = "REQUEST = " + repr({"node": name, "ip": ip}) + "\n" + REMOTE
    env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
    try:
        p = subprocess.run(ssh_argv(ip), input=payload, capture_output=True, text=True, timeout=240, env=env)
        if p.returncode:
            return {"status": "UNAVAILABLE", "error_type": "SSH_OR_REMOTE_EXIT", "rc": p.returncode}
        data = json.loads(p.stdout)
        if not isinstance(data, dict) or data.get("expected_node") != name or data.get("hostname") != name:
            return {"status": "UNAVAILABLE", "error_type": "REMOTE_IDENTITY_OR_RESPONSE"}
        return data
    except subprocess.TimeoutExpired:
        return {"status": "UNAVAILABLE", "error_type": "SSH_TIMEOUT"}
    except (OSError, ValueError):
        return {"status": "UNAVAILABLE", "error_type": "TRANSPORT_OR_RESPONSE"}


def summarize(results):
    cp = results.get("k8s-control01", {})
    cluster = cp.get("cluster", {})
    valid = cp.get("identity_verified") is True and cluster.get("available") is True and cluster.get("authenticated_node_access") is True
    nodes = cluster.get("nodes", []) if valid else []
    by_name = {n["name"]: n for n in nodes}
    duplicate = len(by_name) != len(nodes)
    ready = [name for name, ip in EXPECTED.items() if name in by_name and by_name[name].get("ready") == "True"
             and by_name[name].get("internal_ips") == [ip]
             and by_name[name].get("control_plane") is name.startswith("k8s-control")]
    return {"schema": "fds.icr-next-summary.v1", "collection": {name: results.get(name, {}).get("status", "UNAVAILABLE") for name in NODES},
            "node_observation_available": valid, "expected_nodes": list(EXPECTED),
            "observed_nodes": sorted(by_name) if valid else None,
            "ready_expected_nodes": ready if valid else None,
            "three_cp_ready": all(n in ready for n in EXPECTED if "control" in n) if valid else None,
            "missing_workers": [n for n in EXPECTED if "worker" in n and n not in by_name] if valid else None,
            "six_of_six_ready": valid and not duplicate and set(by_name) == set(EXPECTED) and len(ready) == 6,
            "duplicate_node_names": duplicate if valid else None,
            "hep_runtime_approval": "NOT_GRANTED_BY_COLLECTION", "join_executed": False,
            "icr_full_acceptance": "NOT_DETERMINED", "lb_backend_3_of_3": "NOT_TESTED", "lb_failover": "NOT_TESTED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true", help="collect current status over existing SSH access")
    args = parser.parse_args()
    if not args.collect:
        print(json.dumps({"mode": "PLAN", "targets": NODES, "operation": "read current readiness; LB authenticated node GET",
                          "remote_mutation": False, "next": "python3 -I -B RUN_ICR_NEXT.py --collect"}, indent=2))
        return 0
    os.umask(0o077)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(tempfile.mkdtemp(prefix="ICR_Next_" + stamp + "_", dir=Path.cwd()))
    results = {}
    try:
        shutil.copyfile(__file__, out / "RUN_ICR_NEXT.py")
        for name, ip in NODES.items():
            print(name + "=COLLECTING", flush=True)
            results[name] = remote(name, ip)
            (out / (name + ".json")).write_bytes(encoded(results[name]))
            print(name + "=" + results[name]["status"], flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        for name in NODES:
            if name not in results:
                results[name] = {"status": "UNAVAILABLE", "error_type": "NOT_COLLECTED"}
                (out / (name + ".json")).write_bytes(encoded(results[name]))
        summary = summarize(results)
        summary["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        (out / "SUMMARY.json").write_bytes(encoded(summary))
        files = sorted(out.iterdir())
        (out / "SHA256SUMS").write_text("".join(sha(p) + "  " + p.name + "\n" for p in files))
        archive = Path(str(out) + ".tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(out, arcname=out.name)
        sidecar = Path(str(archive) + ".sha256")
        sidecar.write_text(sha(archive) + "  " + archive.name + "\n")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("ARCHIVE=" + str(archive))
        print("SIDECAR=" + str(sidecar))
    return 0 if all(x.get("status") == "COLLECTED" for x in results.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
