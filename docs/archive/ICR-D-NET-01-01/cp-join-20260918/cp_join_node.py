#!/usr/bin/env python3
"""Candidate CP2/CP3 node step. stdin JSON; no reset or member removal.

The controller must verify live cluster membership and complete the firewall
ownership handoff before prepare. This module never claims cluster acceptance.
"""
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile

TARGETS = {"k8s-control02": "10.1.93.103", "k8s-control03": "10.1.93.104"}
MANIFESTS = {
    "kube-apiserver.yaml": "9c6c75d7ee5981c39386c7fde8039a8b9cb9bdcd88f6951c1e4e3815593a09c4",
    "kube-controller-manager.yaml": "8acf707b42c2b28f9ea7481082315b63174fb9abf09ba3ecde1b4c7d59978bd6",
    "kube-scheduler.yaml": "22a0cf186029c3d93743f6e9838a75b2becc6c937024727d42b2da995147f035",
}
ENDPOINT = "unix:///run/containerd/containerd.sock"
ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin", "LC_ALL": "C"}
SCHEMA = "fds.cp-join-node-candidate.v1"


def need(condition, message):
    if not condition:
        raise RuntimeError(message)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def redact(value, secrets=()):
    value = str(value)
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"\b[a-z0-9]{6}\.[a-z0-9]{16}\b", "[REDACTED_TOKEN]", value)
    value = re.sub(r"(?i)(certificate[-_ ]?key\s*[:=]\s*)[0-9a-f]{64}", r"\1[REDACTED_KEY]", value)
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", value, flags=re.S)
    return value


def run(argv, timeout=30):
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=ENV, start_new_session=True)
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(p.pid, signal.SIGKILL)
        out, err = p.communicate()
    return {"rc": p.returncode, "timed_out": timed_out,
            "stdout": out.decode("utf-8", "replace"), "stderr": err.decode("utf-8", "replace")}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class Node:
    def __init__(self, request, root="/", command=run):
        need(isinstance(request, dict), "request must be an object")
        need(set(request) <= {"mode", "node", "job_id", "join_config"}, "unsupported request fields")
        need("join_config" not in request or request.get("mode") == "join", "join_config only accepted in join mode")
        self.req, self.root, self.command = request, pathlib.Path(root), command
        self.name = request.get("node")
        self.job = request.get("job_id", "")
        need(self.name in TARGETS, "fixed CP2/CP3 target required")
        need(re.fullmatch(r"[0-9a-f]{16,32}", self.job), "invalid job_id")
        self.etc = self.path("/etc/kubernetes")
        self.manifests = self.etc / "manifests"
        self.base = self.path("/var/lib/fds/cp-join")
        self.work = self.base / self.job
        self.journal = self.work / "STATE.json"
        self.secrets = ()

    def path(self, value):
        return self.root / value.lstrip("/")

    def safe(self, path, directory=None):
        for ancestor in reversed(path.parents):
            if ancestor == self.root.parent:
                continue
            if self.root != pathlib.Path("/") and not (ancestor == self.root or self.root in ancestor.parents):
                continue
            if ancestor.exists() or ancestor.is_symlink():
                st = ancestor.lstat()
                need(stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode), "unsafe parent path")
                need(st.st_uid == 0 and not st.st_mode & 0o022, "untrusted parent permissions")
        st = path.lstat()
        need(not stat.S_ISLNK(st.st_mode), "symlink rejected")
        need(st.st_uid == 0 and not st.st_mode & 0o022, "untrusted path permissions")
        need(stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode), "unexpected path type")
        return st

    def private_dir(self, path):
        if not path.exists():
            path.mkdir(mode=0o700, parents=True)
        self.safe(path, True)
        need(stat.S_IMODE(path.stat().st_mode) == 0o700, "private directory must be mode 0700")

    def checked(self, argv, timeout=30):
        result = self.command(argv, timeout)
        need(result["rc"] == 0 and not result.get("timed_out"), "command failed: " + argv[0])
        return result["stdout"]

    def read_state(self):
        if not self.journal.exists():
            return None
        self.safe(self.journal)
        need(stat.S_IMODE(self.journal.stat().st_mode) == 0o600, "journal must be private")
        data = json.loads(self.journal.read_text())
        need(data.get("node") == self.name and data.get("job_id") == self.job, "journal identity mismatch")
        return data

    def save(self, state, **values):
        data = self.read_state() or {"schema": SCHEMA, "job_id": self.job, "node": self.name,
                                    "ip": TARGETS[self.name], "created_at": now()}
        data.update(values)
        data.update(state=state, updated_at=now())
        fd, temporary = tempfile.mkstemp(prefix=".state-", dir=str(self.work))
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.journal)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return data

    def inventory(self):
        self.safe(self.etc, True)
        need(not os.path.ismount(self.etc), "custom Kubernetes mount rejected")
        rows = []
        total = 0
        for directory, dirs, files in os.walk(self.etc, followlinks=False):
            for name in sorted(dirs + files):
                path = pathlib.Path(directory) / name
                isdir = name in dirs
                meta = self.safe(path, isdir)
                need(not os.path.ismount(path), "nested Kubernetes mount rejected")
                total += meta.st_size if not isdir else 0
                need(len(rows) < 1000 and total <= 64 * 1024 * 1024, "unexpected Kubernetes backup size")
                rows.append({"path": str(path.relative_to(self.etc)), "kind": "directory" if isdir else "file",
                             "mode": stat.S_IMODE(meta.st_mode), "uid": meta.st_uid, "gid": meta.st_gid,
                             "mtime_ns": meta.st_mtime_ns,
                             **({"sha256": sha(path), "bytes": meta.st_size} if not isdir else {})})
        return rows

    def configuration_checks(self):
        # Only the installed kubeadm layout is handled. Unit overrides require
        # explicit adaptation; no guessed custom kubelet or etcd directory.
        executables = {"/usr/bin/kubelet", "/usr/local/bin/kubelet", "/bin/kubelet"}
        config_paths = {"/etc/kubernetes/kubelet.conf", "/etc/kubernetes/bootstrap-kubelet.conf",
                        "/var/lib/kubelet/config.yaml"}
        environment_files = {"/var/lib/kubelet/kubeadm-flags.env", "/etc/sysconfig/kubelet", "/etc/default/kubelet"}
        for prop in ("ExecStart", "Environment", "EnvironmentFiles"):
            value = self.checked(["systemctl", "show", "kubelet", "--property=" + prop, "--value"])
            if prop == "ExecStart":
                # systemctl includes process metadata such as start_time=[n/a]
                # and status=0/0. Only executable and argument fields are paths.
                paths = re.findall(r"(?:^|[;{])\s*path=([^;]+)", value)
                arguments = re.findall(r"(?:^|[;{])\s*argv\[\]=([^;]+)", value)
                need(len(paths) == 1 and len(arguments) == 1, "ambiguous kubelet ExecStart")
                executable = paths[0].strip()
                argv = shlex.split(arguments[0].strip())
                need(executable in executables and argv and argv[0] == executable,
                     "custom kubelet executable requires adaptation")
                actual_paths = set(re.findall(r"/[A-Za-z0-9_./+-]+", " ".join(argv)))
                need(actual_paths <= executables | config_paths, "custom kubelet unit path requires adaptation")
                flags = set(re.findall(r"--[a-z][a-z0-9-]*", " ".join(argv)))
                need(flags <= {"--bootstrap-kubeconfig", "--kubeconfig", "--config"},
                     "custom kubelet unit flag requires adaptation")
                variables = set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", " ".join(argv)))
                need(variables <= {"KUBELET_KUBECONFIG_ARGS", "KUBELET_CONFIG_ARGS", "KUBELET_KUBEADM_ARGS", "KUBELET_EXTRA_ARGS"},
                     "custom kubelet argument variable requires adaptation")
            elif prop == "Environment":
                expected = {
                    "KUBELET_KUBECONFIG_ARGS": "--bootstrap-kubeconfig=/etc/kubernetes/bootstrap-kubelet.conf --kubeconfig=/etc/kubernetes/kubelet.conf",
                    "KUBELET_CONFIG_ARGS": "--config=/var/lib/kubelet/config.yaml",
                    "KUBELET_KUBEADM_ARGS": "", "KUBELET_EXTRA_ARGS": ""}
                for item in shlex.split(value):
                    key, separator, assigned = item.partition("=")
                    need(separator and key in expected and assigned == expected[key],
                         "custom kubelet environment requires adaptation")
            else:
                entries = shlex.split(value)
                for item in entries:
                    need(item in environment_files or item in ("(ignore_errors=yes)", "(ignore_errors=no)"),
                         "custom kubelet environment file requires adaptation")
        for location in ("/etc/sysconfig/kubelet", "/etc/default/kubelet"):
            path = self.path(location)
            if path.exists() or path.is_symlink():
                self.safe(path)
                content = path.read_text()
                lines = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
                need(all(re.fullmatch(r"KUBELET_EXTRA_ARGS\s*=\s*(?:\"\"|''|)", line) for line in lines),
                     "nonempty kubelet extra arguments require adaptation")

    def host_checks(self):
        need(socket.gethostname().split(".")[0] == self.name, "hostname mismatch")
        addresses = json.loads(self.checked(["ip", "-j", "-4", "addr", "show", "dev", "ens160"]))
        local = {a["local"] for d in addresses for a in d.get("addr_info", []) if a.get("family") == "inet"}
        need(TARGETS[self.name] in local, "target ens160 IPv4 mismatch")
        versions = {}
        for name, argv, version in (("kubeadm", ["kubeadm", "version", "-o", "short"], "v1.36.4"),
                                    ("kubelet", ["kubelet", "--version"], "v1.36.4"),
                                    ("containerd", ["containerd", "--version"], "v2.3.5")):
            observed = self.checked(argv).strip()
            need(re.search(r"(?<![0-9A-Za-z])" + re.escape(version) + r"(?![0-9A-Za-z.+-])", observed),
                 name + " version mismatch")
            versions[name] = version
        socket_path = self.path("/run/containerd/containerd.sock")
        need(socket_path.exists() and stat.S_ISSOCK(socket_path.lstat().st_mode), "fixed containerd socket absent")
        need(not socket_path.is_symlink() and socket_path.stat().st_uid == 0, "untrusted containerd socket")
        need(self.checked(["systemctl", "is-active", "containerd"]).strip() == "active", "containerd inactive")
        self.configuration_checks()
        return versions

    def firewall_checks(self):
        need(self.checked(["firewall-cmd", "--state"]).strip() == "running", "firewalld not running")
        need(self.checked(["firewall-cmd", "--get-zone-of-interface=ens160"]).strip() == "public", "ens160 public zone changed")
        required = [(source, port) for source in ("10.1.93.102", "10.1.93.103", "10.1.93.104")
                    for port in ("2379-2380", "10250")]
        required.append(("10.1.93.109", "6443"))
        for source, port in required:
            rule = 'rule family="ipv4" source address="' + source + '/32" port port="' + port + '" protocol="tcp" accept'
            need(self.checked(["firewall-cmd", "--zone=public", "--query-rich-rule=" + rule]).strip() == "yes",
                 "required existing Runtime rule missing: " + source + ":" + port)
        return {"state": "EXISTING_RUNTIME_RULES_PRESENT", "cp_rules": 6, "lb_rules": 1, "mutation": False}

    def prepare_checks(self):
        versions = self.host_checks()
        firewall = self.firewall_checks()
        for location in ("/var/lib/kubelet/config.yaml", "/var/lib/kubelet/kubeadm-flags.env",
                         "/etc/kubernetes/kubelet.conf", "/etc/kubernetes/bootstrap-kubelet.conf",
                         "/var/lib/etcd"):
            path = self.path(location)
            need(not path.exists() and not path.is_symlink(), "prejoin path now exists: " + location)
        self.safe(self.manifests, True)
        expected = set(MANIFESTS) if self.name == "k8s-control02" else set()
        need(set(os.listdir(self.manifests)) == expected, "manifest inventory changed")
        for name in expected:
            path = self.manifests / name
            self.safe(path)
            need(sha(path) == MANIFESTS[name], "known manifest hash mismatch: " + name)
        listeners = self.checked(["ss", "-H", "-lnt"])
        for line in listeners.splitlines():
            fields = line.split()
            need(len(fields) >= 4, "unexpected socket listing")
            need(not re.search(r":(?:6443|2379|2380|10250|10257|10259)$", fields[3]), "prejoin service port in use")
        return {"versions": versions, "inventory": self.inventory(), "manifest_count": len(expected), "firewall": firewall}

    def inspect(self):
        view = self.prepare_checks()
        return {"schema": SCHEMA, "state": "PREPARE_CHECKS_PASS", "node": self.name,
                "job_id": self.job, "versions": view["versions"], "manifest_count": view["manifest_count"],
                "firewall": view["firewall"],
                "configuration_mutation": False, "join_readiness": "CONTROLLER_CLUSTER_AND_HANDOFF_CHECK_REQUIRED"}

    def prepare(self):
        need(self.read_state() is None, "job already exists; inspect state, do not repeat prepare")
        view = self.prepare_checks()  # no service/file mutation before all known SHA checks
        self.private_dir(self.work)
        self.save("BACKUP_IN_PROGRESS", baseline_inventory=view["inventory"], versions=view["versions"])
        backup = self.work / "private-backup" / "etc-kubernetes"
        self.private_dir(backup.parent)
        shutil.copytree(self.etc, backup, copy_function=shutil.copy2)
        for row in view["inventory"]:
            if row["kind"] == "file":
                need(sha(backup / row["path"]) == row["sha256"], "backup hash mismatch")
        need(self.inventory() == view["inventory"], "configuration drift during backup")
        self.save("BACKUP_VERIFIED", backup_path=str(backup))
        if self.name == "k8s-control02":
            self.save("CLEANUP_STARTED", configuration_mutation=True)
            self.checked(["systemctl", "stop", "kubelet"])
            state = self.command(["systemctl", "is-active", "kubelet"], 15)
            need(state["stdout"].strip() in ("inactive", "failed"), "kubelet did not stop")
            quarantine = self.work / "quarantined-manifests"
            self.private_dir(quarantine)
            for name in sorted(MANIFESTS):
                need(sha(self.manifests / name) == MANIFESTS[name], "manifest changed before quarantine")
                os.rename(self.manifests / name, quarantine / name)
        return self.save("PREPARED", configuration_mutation=self.name == "k8s-control02",
                         cleanup="KNOWN_THREE_MANIFESTS_QUARANTINED" if self.name == "k8s-control02" else "NOT_NEEDED_CP3_EMPTY",
                         join_attempted=False, automatic_reset=False)

    def validate_join_config(self):
        cfg = self.req.get("join_config")
        need(isinstance(cfg, dict), "join_config required")
        need(set(cfg) == {"apiVersion", "kind", "nodeRegistration", "discovery", "controlPlane"}, "unsupported JoinConfiguration fields")
        need(cfg["apiVersion"] == "kubeadm.k8s.io/v1beta4" and cfg["kind"] == "JoinConfiguration", "JoinConfiguration schema mismatch")
        need(cfg["nodeRegistration"] == {"name": self.name, "criSocket": ENDPOINT}, "nodeRegistration mismatch")
        discovery = cfg["discovery"]
        need(set(discovery) == {"bootstrapToken"}, "unsupported discovery fields")
        token = discovery["bootstrapToken"]
        need(set(token) == {"apiServerEndpoint", "token", "caCertHashes"}, "unsupported bootstrap token fields")
        need(token["apiServerEndpoint"] == "10.1.93.109:6443", "fixed LB endpoint mismatch")
        need(re.fullmatch(r"[a-z0-9]{6}\.[a-z0-9]{16}", token["token"]), "invalid bootstrap token")
        need(len(token["caCertHashes"]) == 1 and re.fullmatch(r"sha256:[0-9a-f]{64}", token["caCertHashes"][0]), "CA hash required")
        control = cfg["controlPlane"]
        need(set(control) == {"localAPIEndpoint", "certificateKey"}, "unsupported controlPlane fields")
        need(control["localAPIEndpoint"] == {"advertiseAddress": TARGETS[self.name], "bindPort": 6443}, "local API endpoint mismatch")
        need(re.fullmatch(r"[0-9a-f]{64}", control["certificateKey"]), "invalid certificate key")
        self.secrets = (token["token"], control["certificateKey"])
        return cfg

    def join(self):
        cfg = self.validate_join_config()
        before = self.read_state()
        need(before and before["state"] == "PREPARED", "join requires PREPARED, never repeat unknown/completed attempt")
        self.host_checks()
        self.firewall_checks()
        need(not os.listdir(self.manifests), "manifest directory not empty before join")
        for location in ("/var/lib/kubelet/config.yaml", "/var/lib/kubelet/kubeadm-flags.env", "/etc/kubernetes/kubelet.conf",
                         "/etc/kubernetes/bootstrap-kubelet.conf", "/var/lib/etcd"):
            path = self.path(location)
            need(not path.exists() and not path.is_symlink(), "state changed after prepare: " + location)
        temporary_dir = self.path("/run/fds-cp-join") / self.job
        self.private_dir(temporary_dir)
        secret = temporary_dir / "JOIN.json"
        fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(cfg, stream)
            self.save("JOIN_RUNNING", join_attempted=True, configuration_mutation=True, automatic_reset=False)
            result = self.command(["kubeadm", "join", "--config", str(secret)], 600)
            public = {"rc": result["rc"], "timed_out": result.get("timed_out", False),
                      "stdout": redact(result["stdout"], self.secrets)[-32768:],
                      "stderr": redact(result["stderr"], self.secrets)[-32768:]}
            success = result["rc"] == 0 and not result.get("timed_out")
            return self.save("JOIN_COMMAND_SUCCEEDED_CLUSTER_VERIFICATION_REQUIRED" if success else "FAILED_RECOVERY_REQUIRED",
                             join_result=public, automatic_reset=False,
                             cluster_acceptance=False, retry_allowed=False)
        finally:
            if secret.exists():
                secret.unlink()

    def status(self):
        need(socket.gethostname().split(".")[0] == self.name, "hostname mismatch")
        state = self.read_state()
        result = {"schema": SCHEMA, "job_id": self.job, "node": self.name, "observed_at": now(),
                  "state": state["state"] if state else "NO_JOB", "configuration_mutation": False,
                  "journal": state, "services": {}, "manifest_hashes": {}}
        for unit in ("kubelet", "containerd"):
            probe = self.command(["systemctl", "show", unit, "--property=ActiveState,SubState"], 15)
            result["services"][unit] = {"rc": probe["rc"], "value": redact(probe["stdout"])[-2048:]}
        if self.manifests.exists():
            self.safe(self.manifests, True)
            for path in sorted(self.manifests.iterdir()):
                self.safe(path)
                result["manifest_hashes"][path.name] = sha(path)
        return result

    def dispatch(self):
        mode = self.req.get("mode")
        need(mode in ("inspect", "prepare", "join", "status"), "unsupported mode; no automatic reset")
        if mode in ("inspect", "status"):
            return getattr(self, mode)()
        self.private_dir(self.base)
        lockpath = self.base / "LOCK"
        lockfd = os.open(lockpath, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            meta = os.fstat(lockfd)
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0 and stat.S_IMODE(meta.st_mode) == 0o600, "unsafe operation lock")
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return getattr(self, mode)()
        except Exception as exc:
            state = self.read_state()
            if state and state["state"] in ("CLEANUP_STARTED", "JOIN_RUNNING"):
                self.save("FAILED_RECOVERY_REQUIRED", error=redact(str(exc), self.secrets), automatic_reset=False, retry_allowed=False)
            raise
        finally:
            os.close(lockfd)


def main(request):
    """Remote payload API: returns sanitized JSON-compatible result."""
    node = None
    try:
        need(os.geteuid() == 0, "root required")
        os.umask(0o077)
        node = Node(request)
        result = node.dispatch()
        result["status"] = result["state"]
        return result
    except Exception as exc:
        state = node.read_state() if node else None
        status = "FAILED_RECOVERY_REQUIRED" if state and state["state"] == "FAILED_RECOVERY_REQUIRED" else "STOP_REVIEW_REQUIRED"
        return {"schema": SCHEMA, "state": status, "status": status,
                "error": redact(str(exc), node.secrets if node else ()),
                "automatic_reset": False, "cluster_acceptance": False}


def cli():
    node = None
    try:
        need(os.geteuid() == 0, "root required")
        os.umask(0o077)
        raw = sys.stdin.buffer.read(131073)
        need(len(raw) <= 131072, "request too large")
        result = main(json.loads(raw))
        print(json.dumps(result, sort_keys=True))
        return 2 if result.get("state") in ("FAILED_RECOVERY_REQUIRED", "STOP_REVIEW_REQUIRED") else 0
    except Exception as exc:
        print(json.dumps({"schema": SCHEMA, "state": "STOP_REVIEW_REQUIRED",
                          "error": redact(str(exc), node.secrets if node else ()),
                          "automatic_reset": False, "cluster_acceptance": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(cli())
