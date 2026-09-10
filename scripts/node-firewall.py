#!/usr/bin/env python3
"""Fixed-scope, one-node firewall transactions for unjoined Workers and CP2/CP3."""
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

CP = {"k8s-control01": "10.1.93.102", "k8s-control02": "10.1.93.103", "k8s-control03": "10.1.93.104"}
WORKERS = {"k8s-worker01": "10.1.93.105", "k8s-worker02": "10.1.93.107", "k8s-worker03": "10.1.93.108"}
NODES = dict(CP, **WORKERS)
TARGETS = {k: v for k, v in NODES.items() if k != "k8s-control01"}
ROOT = Path("/var/lib/fds/icr-d-net-01-01/node-firewall-v1")
MAX_BYTES = 256 * 1024
LB_RULE = 'rule family="ipv4" source address="10.1.93.109/32" port port="6443" protocol="tcp" accept'


def plan_for(name):
    need(name in TARGETS, "target outside plan")
    ports = ("10250",) if name in WORKERS else ("2379-2380", "10250")
    rows = [{"kind": "rich-rule", "value": f'rule family="ipv4" source address="{ip}/32" port port="{port}" protocol="tcp" accept', "desired": True}
            for ip in CP.values() for port in ports]
    if name in WORKERS:
        rows.append({"kind": "service", "value": "cockpit", "desired": False})
    return [dict(row, plane=plane) for plane in ("permanent", "runtime") for row in rows]


def plan_hash(name):
    return fingerprint({"schema": "fds.node-firewall-plan.v1", "target": name, "zone": "public", "operations": plan_for(name)})


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
        proc = subprocess.run([binary(Path(argv[0]).name)] + argv[1:], capture_output=True, text=True, timeout=timeout,
                              env={"PATH": "/usr/sbin:/usr/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/root", "KUBECTL_KUBERC": "false"})
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
    fields["services"] = " ".join(sorted(fields["services"].split()))
    return {"fields": fields, "rules": sorted(rules)}


class BaseBackend:
    def __init__(self, node):
        self.node = node
        self.cache = tempfile.TemporaryDirectory(prefix="fds-kubectl-")
        self.snapshot_queries = {}

    def read_snapshot(self, argv):
        result = command(argv)
        self.snapshot_queries[shlex.join(argv)] = result
        need(result["rc"] == 0 and not result.get("stdout_truncated"),
             "query failed: " + shlex.join(argv) + "; rc=" + str(result["rc"]) + "; " + result["stderr"][:2000])
        return result["stdout"].strip()

    def policy_snapshot(self):
        # --get-active-policies is formatted detail, not a policy-name list.
        # ANY/HOST in its zone bindings must never become --info-policy names.
        active_output = self.read_snapshot(["firewall-cmd", "--get-active-policies"])
        configurations = {}
        for plane in ("runtime", "permanent"):
            prefix = ["firewall-cmd"] + (["--permanent"] if plane == "permanent" else [])
            names = self.read_snapshot(prefix + ["--get-policies"]).split()
            need(len(names) == len(set(names)) and all(re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names),
                 "invalid policy-name list")
            configurations[plane] = {name: self.read_snapshot(prefix + ["--info-policy=" + name]) for name in sorted(names)}
        return {"active_output": active_output, "configurations": configurations}

    def identity(self):
        need(os.geteuid() == 0, "sudo/root required")
        need(socket.gethostname().split(".")[0] == self.node and self.node in NODES, "hostname mismatch")
        addresses = json.loads(self.read_snapshot(["ip", "-j", "-4", "addr", "show", "dev", "ens160"]))
        need(any(a.get("local") == NODES[self.node] for item in addresses for a in item.get("addr_info", [])), "ens160 IP mismatch")

    def snapshot(self):
        self.snapshot_queries = {}
        read = self.read_snapshot
        self.identity()
        need(read(["getenforce"]) == "Enforcing", "SELinux is not Enforcing")
        need(read(["firewall-cmd", "--state"]) == "running", "firewalld not running")
        default = read(["firewall-cmd", "--get-default-zone"])
        active = read(["firewall-cmd", "--get-zone-of-interface=ens160"])
        need(default == active == "public", "default/interface zone mismatch")
        connection = read(["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", "ens160"])
        need(re.fullmatch(r"[0-9a-fA-F-]{36}", connection), "NM active connection UUID unavailable")
        zone = read(["nmcli", "-g", "connection.zone", "connection", "show", "uuid", connection])
        need(zone in ("", "public"), "NM zone does not resolve to public")
        planes = {}
        for plane in ("permanent", "runtime"):
            prefix = ["firewall-cmd"] + (["--permanent"] if plane == "permanent" else [])
            planes[plane] = parse_zone(read(prefix + ["--zone=public", "--list-all"]))
        need(planes["runtime"]["fields"].get("interfaces") == "ens160", "runtime interface changed")
        need(planes["permanent"]["fields"].get("interfaces") in ("", "ens160"), "permanent interface mismatch")
        policy_data = self.policy_snapshot()
        return {"hostname": self.node, "ip": NODES[self.node], "selinux": "Enforcing", "default_zone": default,
                "active_zone": active, "nm_uuid": connection, "nm_zone": zone, "planes": planes,
                "active_policies": policy_data,
                "direct_runtime": read(["firewall-cmd", "--direct", "--get-all-rules"]),
                "direct_permanent": read(["firewall-cmd", "--permanent", "--direct", "--get-all-rules"])}

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
        need(stat.S_ISREG(os.fstat(self.lock).st_mode) and os.fstat(self.lock).st_uid == 0 and not os.fstat(self.lock).st_mode & 0o077, "unsafe lock")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if new:
                for previous in ROOT.glob("*.json"):
                    private_path(previous)
                    data = json.loads(previous.read_bytes())
                    need(data.get("status") == "ROLLED_BACK_REVIEW_REQUIRED", "existing transaction requires review: " + previous.name)
                need(not self.path.exists() and not self.path.is_symlink(), "run id already exists")
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
        except BaseException:
            os.close(self.lock)
            raise


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


def present_in(snapshot, row):
    zone = snapshot["planes"][row["plane"]]
    return (canonical_rule(row["value"]) in zone["rules"] if row["kind"] == "rich-rule"
            else row["value"] in zone["fields"]["services"].split())


def set_presence(snapshot, row, present):
    zone = snapshot["planes"][row["plane"]]
    values = set(zone["rules"] if row["kind"] == "rich-rule" else zone["fields"]["services"].split())
    value = canonical_rule(row["value"]) if row["kind"] == "rich-rule" else row["value"]
    values.add(value) if present else values.discard(value)
    if row["kind"] == "rich-rule":
        zone["rules"] = sorted(values)
    else:
        zone["fields"]["services"] = " ".join(sorted(values))


def evolved(before, operations):
    result = copy.deepcopy(before)
    for op in operations:
        if op["status"] == "changed":
            set_presence(result, op, op["desired"])
    return result


class Backend(BaseBackend):
    def snapshot(self):
        result = super().snapshot()
        read = self.read_snapshot
        active = read(["firewall-cmd", "--get-active-zones"])
        need(active.split() == ["public", "interfaces:", "ens160"], "additional active zone or source binding")
        result["active_zones"] = active
        result["other_zones"] = {}
        result["services"] = {}
        result["passthrough"] = {}
        allowed_rules = {canonical_rule(row["value"]) for row in plan_for(self.node) if row["kind"] == "rich-rule"}
        if self.node in CP:
            allowed_rules.add(canonical_rule(LB_RULE))
        for plane in ("permanent", "runtime"):
            prefix = ["firewall-cmd"] + (["--permanent"] if plane == "permanent" else [])
            zone = result["planes"][plane]
            fields = zone["fields"]
            allowed_services = {"dhcpv6-client", "ssh", "cockpit"} if self.node in WORKERS else {"dhcpv6-client", "ssh"}
            need(set(fields["services"].split()) <= allowed_services, "unexpected active service")
            need(set(zone["rules"]) <= allowed_rules, "unreviewed rich rule")
            if self.node in CP:
                need(canonical_rule(LB_RULE) in zone["rules"], "existing LB allowance missing")
            need(not any(fields.get(x) for x in ("forward-ports", "source-ports")), "unreviewed port allowance")
            names = read(prefix + ["--get-zones"]).split()
            need(len(names) == len(set(names)) and "public" in names and
                 all(re.fullmatch(r"[A-Za-z0-9_-]+", n) for n in names), "invalid zones")
            result["other_zones"][plane] = {n: read(prefix + ["--zone=" + n, "--list-all"])
                                             for n in sorted(names) if n != "public"}
            result["services"][plane] = {n: read(prefix + ["--info-service=" + n]) for n in sorted(allowed_services)}
            result["passthrough"][plane] = read(prefix + ["--direct", "--get-all-passthroughs"])
            need(not result["passthrough"][plane] and not result["direct_" + plane], "direct firewall overrides require review")
        if self.node in WORKERS:
            result["cockpit_units"] = {}
            for unit in ("cockpit.socket", "cockpit.service"):
                raw = read(["systemctl", "show", unit, "-p", "LoadState", "-p", "UnitFileState", "-p", "ActiveState", "-p", "SubState"])
                props = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
                need(props == {"LoadState": "masked", "UnitFileState": "masked", "ActiveState": "inactive", "SubState": "dead"},
                     "Cockpit masking/state changed")
                result["cockpit_units"][unit] = props
            need(not read(["ss", "-H", "-lnt", "sport = :9090"]), "Cockpit TCP 9090 listener appeared")
            result["cockpit_listener_absent"] = True
        return result

    def query(self, row):
        result = command(["firewall-cmd"] + (["--permanent"] if row["plane"] == "permanent" else []) +
                         ["--zone=public", "--query-" + row["kind"] + "=" + row["value"]])
        need(not result.get("stdout_truncated") and not result.get("stderr_truncated") and
             (result["rc"], result["stdout"].strip()) in ((0, "yes"), (1, "no")), "presence query failed")
        return result["rc"] == 0

    def edit(self, row, desired):
        fixed = {k: row[k] for k in ("plane", "kind", "value", "desired")}
        need(fixed in plan_for(self.node) and type(desired) is bool, "mutation outside fixed scope")
        action = "add" if desired else "remove"
        return command(["firewall-cmd"] + (["--permanent"] if row["plane"] == "permanent" else []) +
                       ["--zone=public", "--" + action + "-" + row["kind"] + "=" + row["value"]])


def outcome(result, actual, desired, before):
    warning = any(x in (result["stdout"] + result["stderr"]) for x in ("ALREADY_ENABLED", "NOT_ENABLED"))
    if result.get("stdout_truncated") or result.get("stderr_truncated") or warning:
        return "uncertain"
    if result["rc"] == 0 and actual == desired:
        return "changed"
    if result["rc"] not in (0, 124) and actual == before:
        return "not_changed"
    return "uncertain"


def apply_transaction(backend, journal, request):
    before = backend.snapshot()
    need(fingerprint(before) == request["before_sha256"], "live firewall/NM/mask state differs from inspection")
    receipt = {"schema": "fds.node-firewall-receipt.v1", "hostname": backend.node, "run_id": request["run_id"],
               "source_commit": request["source_commit"], "source_sha256": request["source_sha256"],
               "plan_sha256": plan_hash(backend.node), "topology_sha256": request["topology_sha256"],
               "authorization_sha256": request["authorization_sha256"], "before": before,
               "status": "APPLYING", "operations": [], "started_at": now()}
    journal.save(receipt)
    try:
        for row in plan_for(backend.node):
            need(backend.snapshot() == evolved(before, receipt["operations"]), "concurrent configuration change")
            present = backend.query(row)
            need(present == present_in(before, row), "presence changed after snapshot")
            op = dict(row, present_before=present, status="preserved" if present == row["desired"] else "pending")
            receipt["operations"].append(op)
            journal.save(receipt)  # Intent is durable before each individual change.
            if op["status"] == "preserved":
                continue
            result = backend.edit(row, row["desired"])
            op["result"] = result
            op["status"] = outcome(result, backend.query(row), row["desired"], present)
            journal.save(receipt)
            need(op["status"] == "changed", "change failed or ownership uncertain")
        receipt["after"] = backend.snapshot()
        need(receipt["after"] == evolved(before, receipt["operations"]), "post-apply state differs")
        receipt["status"] = "APPLIED_REVIEW_REQUIRED"
    except Exception as error:
        receipt["status"] = "PARTIAL_APPLY_REVIEW_REQUIRED"
        receipt["error"] = redact(error)
    receipt["ended_at"] = now()
    journal.save(receipt)
    return receipt


def validate_receipt(receipt, request):
    need(receipt["schema"] == "fds.node-firewall-receipt.v1" and receipt["hostname"] == request["node"] and
         receipt["run_id"] == request["run_id"], "receipt identity differs")
    for key in ("source_commit", "source_sha256", "plan_sha256", "topology_sha256"):
        need(receipt[key] == request[key], "receipt/input differs: " + key)
    need(receipt["status"] in ("APPLIED_REVIEW_REQUIRED", "PARTIAL_APPLY_REVIEW_REQUIRED"), "receipt not eligible for rollback")
    rows = plan_for(request["node"])
    ops = receipt["operations"]
    need(len(ops) <= len(rows), "too many receipt operations")
    for op, row in zip(ops, rows):
        need(all(op.get(k) == v for k, v in row.items()), "receipt is not an ordered plan prefix")
        need(type(op.get("present_before")) is bool and op["present_before"] == present_in(receipt["before"], op), "receipt origin differs")
        need(op["status"] in ("changed", "preserved", "not_changed"), "ambiguous operation requires manual review")
        need((op["status"] == "preserved") == (op["present_before"] == op["desired"]), "receipt ownership differs")


def rollback_transaction(backend, journal, receipt, request):
    validate_receipt(receipt, request)
    need(backend.snapshot() == evolved(receipt["before"], receipt["operations"]), "state drift before rollback")
    receipt["status"] = "ROLLING_BACK"
    journal.save(receipt)
    try:
        for op in reversed(receipt["operations"]):
            if op["status"] != "changed":
                continue
            need(backend.snapshot() == evolved(receipt["before"], receipt["operations"]), "concurrent configuration change")
            need(backend.query(op) == op["desired"], "owned state changed")
            op["status"] = "undo_pending"
            journal.save(receipt)
            result = backend.edit(op, op["present_before"])
            op["undo_result"] = result
            state = outcome(result, backend.query(op), op["present_before"], op["desired"])
            op["status"] = "restored" if state == "changed" else "undo_uncertain"
            journal.save(receipt)
            need(op["status"] == "restored", "rollback result requires manual review")
        receipt["restored"] = backend.snapshot()
        need(receipt["restored"] == receipt["before"], "rollback did not restore original state")
        receipt["status"] = "ROLLED_BACK_REVIEW_REQUIRED"
    except Exception as error:
        receipt["status"] = "PARTIAL_ROLLBACK_REVIEW_REQUIRED"
        receipt["error"] = redact(error)
    receipt["ended_at"] = now()
    journal.save(receipt)
    return receipt


def fresh(created_at):
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(created_at)).total_seconds()
    need(0 <= age <= 900, "inspection older than 15 minutes or in future")


def inspect(backend):
    result = {"status": "INSPECTED_REVIEW_REQUIRED", "hostname": backend.node, "created_at": now(),
              "plan_sha256": plan_hash(backend.node)}
    try:
        result["snapshot"] = backend.snapshot()
        result["before_sha256"] = fingerprint(result["snapshot"])
        result["nft"] = command(["nft", "-j", "list", "ruleset"])
        need(result["nft"]["rc"] == 0 and not result["nft"].get("stdout_truncated"), "nft collection incomplete")
        paths = ["/var/lib/kubelet/config.yaml", "/var/lib/kubelet/kubeadm-flags.env", "/etc/kubernetes/kubelet.conf",
                 "/etc/kubernetes/bootstrap-kubelet.conf", "/var/lib/etcd", "/var/lib/etcd/member"]
        paths += ["/etc/kubernetes/manifests/" + name + ".yaml" for name in ("etcd", "kube-apiserver", "kube-controller-manager", "kube-scheduler")]
        result["residual_metadata"] = []
        for value in paths:
            try:
                info = Path(value).lstat()
                result["residual_metadata"].append({"path": value, "exists": True, "mode": oct(info.st_mode), "uid": info.st_uid,
                                                     "size": info.st_size, "mtime_ns": info.st_mtime_ns})
            except FileNotFoundError:
                result["residual_metadata"].append({"path": value, "exists": False})
    except Exception as error:
        result["status"] = "INCOMPLETE_REVIEW_REQUIRED"
        result["error"] = redact(error)
    result["queries"] = backend.snapshot_queries
    result["ended_at"] = now()
    return result


def main(request):
    backend = journal = None
    try:
        mode = request["mode"]
        need(mode in ("inspect", "topology", "apply", "rollback", "receipt"), "unsupported mode")
        if mode == "topology":
            need(request["node"] == "k8s-control01", "topology is CP1 only")
            backend = BaseBackend(request["node"])
            backend.identity()
            result = dict(backend.topology(), hostname=backend.node, created_at=now(), status="TOPOLOGY_REVIEW_REQUIRED")
        else:
            need(request["node"] in TARGETS, "CP1 mutation excluded; use PR2")
            backend = Backend(request["node"])
            backend.identity()
            if mode == "inspect":
                result = inspect(backend)
            else:
                for key, pattern in (("source_commit", r"[0-9a-f]{40}"), ("source_sha256", r"[0-9a-f]{64}")):
                    need(re.fullmatch(pattern, request[key]), "invalid " + key)
                need(request["plan_sha256"] == plan_hash(backend.node), "plan differs")
                if mode in ("apply", "rollback"):
                    fresh(request["inspection_created_at"])
                    need(re.fullmatch(r"[0-9a-f]{64}", request["authorization_sha256"]), "authorization digest absent")
                journal = Journal(request["run_id"], new=mode == "apply")
                if mode == "apply":
                    result = apply_transaction(backend, journal, request)
                elif mode == "rollback":
                    result = rollback_transaction(backend, journal, journal.load(request["receipt_sha256"]), request)
                else:
                    private_path(journal.path)
                    result = json.loads(journal.path.read_bytes())
                result = {"status": result["status"], "receipt": result, "receipt_path": str(journal.path),
                          "receipt_sha256": sha(journal.path.read_bytes())}
    except Exception as error:
        result = {"status": "STOP", "node": request.get("node"), "run_id": request.get("run_id"), "error": redact(error),
                  "note": "Do not retry blindly. Fetch durable receipt and review any partial/uncertain state."}
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
