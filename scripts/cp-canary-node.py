#!/usr/bin/env python3
"""Remote CP diagnostics and CP1-only firewall transaction; standard library only."""
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import tempfile

NODES = {"k8s-control01": "10.1.93.102", "k8s-control02": "10.1.93.103", "k8s-control03": "10.1.93.104"}
ROOT = Path("/var/lib/fds/icr-d-net-01-01/cp1-canary")
PLAN = "2c1e6101724d078a0a8fd58497d4d84ae5453e938529496c3045b96ea0fef7d7"
RULES = [f'rule family="ipv4" source address="{ip}/32" port port="{port}" protocol="tcp" accept'
         for ip in NODES.values() for port in ("2379-2380", "10250")]


def need(value, message):
    if not value:
        raise ValueError(message)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def fingerprint(value):
    return sha(encoded(value))


def redact(value):
    text = str(value)
    text = re.sub(r"-----BEGIN [^-\r\n]+-----.*?(?:-----END [^-\r\n]+-----|\Z)", "[REDACTED_PEM]", text, flags=re.S)
    text = re.sub(r"(?<![A-Za-z0-9])[a-z0-9]{6}\.[a-z0-9]{16}(?![A-Za-z0-9])", "[REDACTED_TOKEN]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED_JWT]", text)
    text = re.sub(r"(?i)(\bbearer\s+)[A-Za-z0-9_./+=-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:--)?(?:certificate-key|token|password|client-key-data|client-certificate-data)\s*[=:]\s*)([^\s,;]+)", r"\1[REDACTED]", text)
    return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)


def command(argv, timeout=15):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C",
                                   "LC_ALL": "C", "KUBECTL_KUBERC": "false"})
        result = {"rc": proc.returncode, "stdout": redact(proc.stdout), "stderr": redact(proc.stderr)}
    except subprocess.TimeoutExpired:
        result = {"rc": 124, "stdout": "", "stderr": "COMMAND_TIMEOUT; outcome may be unknown"}
    except OSError as error:
        result = {"rc": 127, "stdout": "", "stderr": redact(error)}
    for key in ("stdout", "stderr"):
        if len(result[key].encode()) > 256 * 1024:
            result[key] = result[key].encode()[:256 * 1024].decode("utf-8", "ignore")
            result[key + "_truncated"] = True
    return result


def checked(argv):
    result = command(argv)
    need(result["rc"] == 0 and not result.get("stdout_truncated"),
         "query failed: " + shlex.join(argv) + "; rc=" + str(result["rc"]) + "; " + result["stderr"][:2000])
    return result["stdout"].strip()


def canonical_rule(rule):
    return shlex.join(shlex.split(rule))


def parse_zone(text):
    lines = text.splitlines()
    need(lines and lines[0].split()[0] == "public", "unexpected zone")
    fields, rules, in_rules = {}, [], False
    for line in lines[1:]:
        line = line.strip()
        if line == "rich rules:":
            in_rules = True
        elif in_rules and line:
            need(line.startswith("rule "), "invalid rich rule")
            rules.append(canonical_rule(line))
        elif ":" in line:
            key, val = line.split(":", 1)
            need(key not in fields, "duplicate zone field")
            fields[key] = val.strip()
    need(in_rules and "ssh" in fields.get("services", "").split(), "SSH service missing")
    need(fields.get("target") == "default" and not fields.get("sources"), "zone routing differs")
    need(not fields.get("ports") and not fields.get("protocols"), "unexpected broad allowance")
    lb = canonical_rule('rule family="ipv4" source address="10.1.93.109/32" port port="6443" protocol="tcp" accept')
    need(lb in rules, "existing LB rule missing")
    return {"fields": fields, "rules": sorted(rules)}


class Backend:
    def __init__(self, node):
        self.node = node
        self.cache = tempfile.TemporaryDirectory(prefix="fds-kubectl-")

    def identity(self):
        need(os.geteuid() == 0, "sudo/root required")
        need(socket.gethostname().split(".")[0] == self.node and self.node in NODES, "hostname mismatch")
        addresses = json.loads(checked(["ip", "-j", "-4", "addr", "show", "dev", "ens160"]))
        need(any(a.get("local") == NODES[self.node] for item in addresses for a in item.get("addr_info", [])), "ens160 IP mismatch")

    def snapshot(self):
        self.identity()
        need(checked(["getenforce"]) == "Enforcing", "SELinux is not Enforcing")
        need(checked(["firewall-cmd", "--state"]) == "running", "firewalld not running")
        default = checked(["firewall-cmd", "--get-default-zone"])
        active = checked(["firewall-cmd", "--get-zone-of-interface=ens160"])
        need(default == active == "public", "default/interface zone mismatch")
        connection = checked(["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", "ens160"])
        need(re.fullmatch(r"[0-9a-fA-F-]{36}", connection), "NM active connection UUID unavailable")
        zone = checked(["nmcli", "-g", "connection.zone", "connection", "show", "uuid", connection])
        need(zone in ("", "public"), "NM zone does not resolve to public")
        planes = {}
        for plane in ("permanent", "runtime"):
            prefix = ["firewall-cmd"] + (["--permanent"] if plane == "permanent" else [])
            planes[plane] = parse_zone(checked(prefix + ["--zone=public", "--list-all"]))
        need(planes["runtime"]["fields"].get("interfaces") == "ens160", "runtime interface changed")
        need(planes["permanent"]["fields"].get("interfaces") in ("", "ens160"), "permanent interface mismatch")
        policies = checked(["firewall-cmd", "--get-active-policies"]).split()
        policy_data = {name: checked(["firewall-cmd", "--info-policy=" + name]) for name in sorted(policies)}
        return {"hostname": self.node, "ip": NODES[self.node], "selinux": "Enforcing", "default_zone": default,
                "active_zone": active, "nm_uuid": connection, "nm_zone": zone, "planes": planes,
                "active_policies": policy_data,
                "direct_runtime": checked(["firewall-cmd", "--direct", "--get-all-rules"]),
                "direct_permanent": checked(["firewall-cmd", "--permanent", "--direct", "--get-all-rules"])}

    def kubectl(self, args):
        return command(["/usr/bin/kubectl", "--kubeconfig=/etc/kubernetes/admin.conf",
                        "--cache-dir=" + self.cache.name, "--request-timeout=8s"] + args, timeout=18)

    def topology(self):
        need(self.node == "k8s-control01", "topology lookup is CP1 only")
        records = {}
        self.topology_queries = records
        records["api_readyz"] = self.kubectl(["get", "--raw=/readyz?verbose"])
        records["nodes"] = self.kubectl(["get", "nodes", "-o", "json"])
        pod = self.kubectl(["-n", "kube-system", "get", "pod", "etcd-k8s-control01", "-o", "json"])
        records["etcd_pod"] = pod
        need(pod["rc"] == 0 and not pod.get("stdout_truncated"), "etcd Pod lookup failed")
        meta = json.loads(pod["stdout"])
        need(meta["spec"]["nodeName"] == "k8s-control01" and meta["spec"].get("hostNetwork") is True, "unexpected etcd Pod placement")
        container = next(c for c in meta["spec"]["containers"] if c["name"] == "etcd")
        path = "/etc/kubernetes/pki/etcd"
        mount = next(m for m in container["volumeMounts"] if m["mountPath"] == path)
        need(any(v["name"] == mount["name"] and v.get("hostPath", {}).get("path") == path for v in meta["spec"]["volumes"]), "unexpected etcd certificate mount")
        etcd = ["-n", "kube-system", "exec", "etcd-k8s-control01", "-c", "etcd", "--", "etcdctl",
                "--endpoints=https://127.0.0.1:2379", "--cacert=" + path + "/ca.crt",
                "--cert=" + path + "/healthcheck-client.crt", "--key=" + path + "/healthcheck-client.key",
                "--dial-timeout=3s", "--command-timeout=5s", "--write-out=json"]
        records["etcd_members"] = self.kubectl(etcd + ["member", "list"])
        records["etcd_status"] = self.kubectl(etcd + ["endpoint", "status"])
        for key, result in records.items():
            need(result["rc"] == 0 and not result.get("stdout_truncated"), "topology query failed: " + key)
        need("readyz check passed" in records["api_readyz"]["stdout"], "API readiness did not pass")
        nodes = json.loads(records["nodes"]["stdout"])["items"]
        members = json.loads(records["etcd_members"]["stdout"])["members"]
        need(members, "empty etcd member list")
        stable = {"nodes": sorted((n["metadata"]["name"], n["metadata"]["uid"]) for n in nodes),
                  "members": sorted([{"id": str(m["ID"]), "name": m.get("name", ""),
                                      "peerURLs": sorted(m.get("peerURLs", [])), "clientURLs": sorted(m.get("clientURLs", [])),
                                      "isLearner": bool(m.get("isLearner", False))} for m in members], key=lambda m: m["id"])}
        return {"stable": stable, "sha256": fingerprint(stable), "queries": records}

    def query(self, plane, rule):
        result = command(["firewall-cmd"] + (["--permanent"] if plane == "permanent" else []) +
                         ["--zone=public", "--query-rich-rule=" + rule])
        need((result["rc"], result["stdout"].strip()) in ((0, "yes"), (1, "no")), "rich-rule query failed")
        return result["rc"] == 0

    def edit(self, plane, rule, action):
        need(plane in ("runtime", "permanent") and rule in RULES and action in ("add", "remove"), "invalid mutation")
        return command(["firewall-cmd"] + (["--permanent"] if plane == "permanent" else []) +
                       ["--zone=public", "--" + action + "-rich-rule=" + rule])


def private_path(path, create=False):
    if create:
        missing = []
        current = path
        while not current.exists() and not current.is_symlink():
            missing.append(current)
            current = current.parent
        private_path(current)
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
    for component in [path] + list(path.parents):
        info = component.lstat()
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "unsafe state path")


class Journal:
    def __init__(self, run_id, new):
        need(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", run_id), "invalid run id")
        private_path(ROOT, create=new)
        self.path = ROOT / (run_id + ".json")
        self.lock = os.open(ROOT / "transaction.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        need(os.fstat(self.lock).st_uid == 0 and not os.fstat(self.lock).st_mode & 0o077, "unsafe lock")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if new:
            need(not self.path.exists() and not self.path.is_symlink(), "run id already exists")
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)

    def save(self, value):
        private_path(self.path)
        fd, name = tempfile.mkstemp(prefix=".receipt-", dir=ROOT)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded(value))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            parent = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def load(self, expected):
        private_path(self.path)
        need(stat.S_ISREG(self.path.stat().st_mode) and not self.path.stat().st_mode & 0o077, "unsafe receipt")
        raw = self.path.read_bytes()
        need(sha(raw) == expected, "receipt checksum mismatch")
        return json.loads(raw)


def evolved(before, operations):
    expected = copy.deepcopy(before)
    for op in operations:
        if op["status"] == "added":
            rules = expected["planes"][op["plane"]]["rules"]
            rules.append(canonical_rule(op["rule"]))
            rules.sort()
    return expected


def apply_transaction(backend, journal, request):
    before = backend.snapshot()
    need(fingerprint(before) == request["before_sha256"], "live firewall/NM state differs from inspection")
    topology = backend.topology()
    need(topology["sha256"] == request["topology_sha256"], "cluster membership changed")
    stable = topology["stable"]
    need(len(stable["nodes"]) == 1 and stable["nodes"][0][0] == "k8s-control01", "initial canary requires only CP1 registered")
    need(len(stable["members"]) == 1 and stable["members"][0]["name"] == "k8s-control01" and
         stable["members"][0]["peerURLs"] == ["https://10.1.93.102:2380"] and not stable["members"][0]["isLearner"],
         "initial canary requires a single CP1 etcd member; review current topology")
    receipt = {"schema": "fds.cp1-canary-receipt.v1", "hostname": "k8s-control01", "run_id": request["run_id"],
               "started_at": now(), "source_sha256": request["source_sha256"], "source_commit": request["source_commit"],
               "plan_sha256": PLAN, "before": before, "topology_sha256": topology["sha256"],
               "status": "APPLYING", "operations": []}
    journal.save(receipt)
    try:
        for plane in ("permanent", "runtime"):
            for rule in RULES:
                need(backend.snapshot() == evolved(before, receipt["operations"]), "concurrent firewall change")
                present = backend.query(plane, rule)
                need(present == (canonical_rule(rule) in before["planes"][plane]["rules"]), "rule changed after snapshot")
                op = {"plane": plane, "rule": rule, "present_before": present,
                      "status": "preserved" if present else "add_pending", "started_at": now()}
                receipt["operations"].append(op)
                journal.save(receipt)  # Durable intent before any firewall mutation.
                if present:
                    continue
                result = backend.edit(plane, rule, "add")
                op["result"] = result
                exists = backend.query(plane, rule)
                if result["rc"] == 0 and exists and "ALREADY_ENABLED" not in (result["stderr"] + result["stdout"]):
                    op["status"] = "added"
                elif result["rc"] not in (0, 124) and not exists:
                    op["status"] = "not_added"
                else:
                    op["status"] = "add_uncertain"
                journal.save(receipt)
                need(op["status"] == "added", "add failed or ownership is uncertain")
        receipt["after"] = backend.snapshot()
        need(receipt["after"] == evolved(before, receipt["operations"]), "post-apply firewall mismatch")
        need(backend.topology()["sha256"] == receipt["topology_sha256"], "post-apply topology/readiness changed")
        receipt["status"] = "APPLIED_REVIEW_REQUIRED"
    except Exception as error:
        receipt["status"] = "PARTIAL_APPLY_REVIEW_REQUIRED"
        receipt["error"] = redact(error)
    receipt["ended_at"] = now()
    journal.save(receipt)
    return receipt


def rollback_transaction(backend, journal, receipt, request):
    need(receipt["hostname"] == "k8s-control01" and receipt["run_id"] == request["run_id"], "receipt identity mismatch")
    need(receipt["source_sha256"] == request["source_sha256"] and receipt["plan_sha256"] == PLAN, "rollback source differs")
    need(receipt["source_commit"] == request["source_commit"], "rollback commit differs")
    need(receipt["status"] in ("APPLIED_REVIEW_REQUIRED", "PARTIAL_APPLY_REVIEW_REQUIRED"), "receipt is not eligible for automatic rollback")
    operations = receipt["operations"]
    for op in operations:
        need(op["plane"] in ("permanent", "runtime") and op["rule"] in RULES, "receipt rule out of scope")
        need(op["status"] in ("added", "preserved", "not_added"), "ambiguous operation requires manual review")
        need(op["status"] != "added" or op["present_before"] is False, "refusing removal of a preexisting rule")
    need(backend.topology()["sha256"] == receipt["topology_sha256"], "membership changed; removing rules may disrupt joined nodes")
    need(backend.snapshot() == evolved(receipt["before"], operations), "firewall drift before rollback")
    receipt["status"] = "ROLLING_BACK"
    journal.save(receipt)
    try:
        for op in reversed(operations):
            if op["status"] != "added":
                continue
            need(backend.snapshot() == evolved(receipt["before"], operations), "concurrent firewall change")
            need(backend.query(op["plane"], op["rule"]), "owned rule unexpectedly absent")
            op["status"] = "remove_pending"
            journal.save(receipt)
            result = backend.edit(op["plane"], op["rule"], "remove")
            op["remove_result"] = result
            absent = not backend.query(op["plane"], op["rule"])
            op["status"] = "removed" if result["rc"] == 0 and absent else "remove_uncertain"
            journal.save(receipt)
            need(op["status"] == "removed", "remove failed or uncertain")
        receipt["rollback_after"] = backend.snapshot()
        need(receipt["rollback_after"] == receipt["before"], "rollback snapshot mismatch")
        need(backend.topology()["sha256"] == receipt["topology_sha256"], "post-rollback readiness/topology changed")
        receipt["status"] = "ROLLED_BACK_REVIEW_REQUIRED"
    except Exception as error:
        receipt["status"] = "PARTIAL_ROLLBACK_REVIEW_REQUIRED"
        receipt["error"] = redact(error)
    receipt["ended_at"] = now()
    journal.save(receipt)
    return receipt


def file_metadata(path):
    try:
        info = path.lstat()
        return {"path": str(path), "exists": True, "mode": oct(stat.S_IMODE(info.st_mode)),
                "uid": info.st_uid, "size": info.st_size, "mtime_ns": info.st_mtime_ns,
                "is_symlink": stat.S_ISLNK(info.st_mode), "is_regular": stat.S_ISREG(info.st_mode)}
    except FileNotFoundError:
        return {"path": str(path), "exists": False}


def inspect(backend):
    result = {"schema": "fds.cp-canary-inspection.v1", "hostname": backend.node, "ip": NODES[backend.node],
              "created_at": now(), "status": "INSPECTED_REVIEW_REQUIRED", "queries": {}, "gaps": []}
    try:
        result["snapshot"] = backend.snapshot()
        result["before_sha256"] = fingerprint(result["snapshot"])
    except Exception as error:
        result["gaps"].append("FIREWALL_PRECHECK: " + redact(error))
    queries = {"listeners": ["ss", "-H", "-lntp"],
               "units": ["systemctl", "show", "kubelet", "containerd", "firewalld", "-p", "Id", "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts"],
               "kubeadm_version": ["kubeadm", "version", "-o", "short"],
               "nft": ["nft", "-j", "list", "ruleset"],
               "active_zones": ["firewall-cmd", "--get-active-zones"],
               "public_runtime": ["firewall-cmd", "--zone=public", "--list-all"],
               "public_permanent": ["firewall-cmd", "--permanent", "--zone=public", "--list-all"],
               "nm_device": ["nmcli", "-g", "GENERAL.CONNECTION,GENERAL.CON-UUID", "device", "show", "ens160"]}
    if backend.node == "k8s-control02":
        queries["join_window_kubelet"] = ["journalctl", "--utc", "--no-pager", "-u", "kubelet", "--since", "2026-09-09 12:40:00 UTC", "--until", "2026-09-09 13:10:00 UTC", "-n", "800"]
        paths = ["/etc/kubernetes/manifests/" + name + ".yaml" for name in ("etcd", "kube-apiserver", "kube-controller-manager", "kube-scheduler")]
        paths += ["/var/lib/kubelet/config.yaml", "/var/lib/kubelet/kubeadm-flags.env", "/etc/kubernetes/kubelet.conf",
                  "/etc/kubernetes/bootstrap-kubelet.conf", "/var/lib/etcd", "/var/lib/etcd/member"]
        result["residual_files"] = [file_metadata(Path(path)) for path in paths]
    if backend.node == "k8s-control01":
        try:
            topology = backend.topology()
            result["topology"] = topology["stable"]
            result["topology_sha256"] = topology["sha256"]
            result["queries"].update(topology["queries"])
        except Exception as error:
            result["gaps"].append("TOPOLOGY: " + redact(error))
            result["queries"].update(getattr(backend, "topology_queries", {}))
        for pod, container in [("etcd-k8s-control01", "etcd"), ("kube-apiserver-k8s-control01", "kube-apiserver")]:
            result["queries"][container + "_log"] = backend.kubectl(["-n", "kube-system", "logs", pod, "-c", container,
                "--timestamps", "--tail=300", "--since-time=2026-09-09T12:40:00Z"])
    for key, argv in queries.items():
        result["queries"][key] = command(argv)
    result["tcp_to_cp1"] = []
    for port in (2379, 2380, 10250):
        probe = {"destination": "10.1.93.102", "port": port}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                sock.bind((NODES[backend.node], 0))
                sock.connect(("10.1.93.102", port))
                probe.update(connected=True, source=sock.getsockname()[0])
        except OSError as error:
            probe.update(connected=False, errno=error.errno, error=str(error))
        result["tcp_to_cp1"].append(probe)
    result["failed_queries"] = [key for key, query in result["queries"].items() if query["rc"] != 0 or query.get("stdout_truncated")]
    result["ended_at"] = now()
    result["canary_inputs_complete"] = backend.node == "k8s-control01" and not result["gaps"]
    result["note"] = "Read-only collection. Failed TCP probes do not make collection a transport failure. No kubeconfig/key contents or shell history collected."
    return result


def main(request):
    backend = None
    journal = None
    try:
        need(request["node"] in NODES, "unknown target")
        backend = Backend(request["node"])
        backend.identity()
        if request["mode"] == "inspect":
            result = inspect(backend)
        else:
            need(request["node"] == "k8s-control01", "mutations and receipts are CP1 only")
            need(re.fullmatch(r"[0-9a-f]{64}", request["source_sha256"]), "invalid source hash")
            need(re.fullmatch(r"[0-9a-f]{40}", request["source_commit"]), "invalid source commit")
            need(request["plan_sha256"] == PLAN, "plan mismatch")
            mode = request["mode"]
            need(mode in ("apply", "rollback", "receipt"), "unsupported mode")
            journal = Journal(request["run_id"], new=mode == "apply")
            if mode == "apply":
                age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(request["inspection_created_at"])).total_seconds()
                need(0 <= age <= 900, "inspection older than 15 minutes; inspect again")
                result = apply_transaction(backend, journal, request)
            elif mode == "rollback":
                receipt = journal.load(request["receipt_sha256"])
                result = rollback_transaction(backend, journal, receipt, request)
            else:
                private_path(journal.path)
                result = json.loads(journal.path.read_bytes())
            result = {"status": result["status"], "receipt": result,
                      "receipt_path": str(journal.path), "receipt_sha256": sha(journal.path.read_bytes())}
    except Exception as error:
        result = {"status": "STOP", "error": redact(error), "node": request.get("node"),
                  "run_id": request.get("run_id"), "note": "No automatic retry or blanket rollback. Inspect any existing durable receipt."}
        if journal and request.get("mode") == "apply" and journal.path.stat().st_size == 0:
            journal.save(result)
    finally:
        if journal:
            os.close(journal.lock)
        if backend:
            backend.cache.cleanup()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(REQUEST)
