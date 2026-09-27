#!/usr/bin/env python3
"""CNI handoff adapter: original policy, independent acceptance or reviewed delegation."""
import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import stat
import subprocess
import tarfile
import tempfile

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cni_handoff_consumer", KIT / "scripts/cp1_minimal_consumer.py")
cni = importlib.util.module_from_spec(spec); spec.loader.exec_module(cni)
NODE_SOURCE = KIT / "scripts/node-firewall-session.py"
spec = importlib.util.spec_from_file_location("worker_followup_node", NODE_SOURCE)
node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)
ANSIBLE_COMMIT = "5cbf3b657014b32a20cb5816d90d3ea5fe4951ab"
ANSIBLE_REPO = Path("/home/server01_ansible/p1-ans-01")
KEY = Path("/home/server01_ansible/.ssh/p1_ansible_ed25519")
KNOWN_HOSTS = KEY.parent / "known_hosts"
KEY_FINGERPRINT = "SHA256:VPGV/RGA8TrwO/3FC4YPxGzRNhBWuB+9hCzPV5plkws"
SOURCE_IP = "10.250.10.10"
POLICY_PATH = KIT / "policies/icr-d-net-01-01/node-firewall-v2.json"


def controller_env():
    # Keep the user's approved executable search path, but do not forward arbitrary tool/credential settings.
    result = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "PATH") if k in os.environ}
    # Ansible requires a UTF-8 locale; plain C makes locale.getlocale()[1] None.
    result.update(LANG="C.UTF-8", LC_ALL="C.UTF-8")
    return result


class ControllerLock:
    """Serialize all mutation targets on the approved controller; not a distributed lock."""
    def __init__(self):
        directory = Path.home() / ".local/state/fds-node-firewall"
        for path in reversed([directory] + list(directory.parents)):
            if not path.exists() and not path.is_symlink():
                path.mkdir(mode=0o700)
            info = path.lstat()
            node.need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022,
                      "unsafe controller lock directory")
        self.fd = os.open(directory / "transaction.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(self.fd)
            node.need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                      "unsafe controller lock")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)


def run(argv, cwd=None, input=None, timeout=30, env=None):
    return subprocess.run(argv, cwd=cwd, input=input, text=True, capture_output=True,
                          timeout=timeout, env=controller_env() if env is None else env)


def checked(argv, cwd=None, env=None):
    result = run(argv, cwd=cwd, env=env)
    node.need(result.returncode == 0, "local command failed: " + shlex.join(argv) + "; " + node.redact(result.stderr)[:2000])
    return result.stdout.strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_source():
    """Outer ZIP digest must be checked separately against the published digest."""
    lock_path = KIT / "SOURCE_LOCK.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        node.need(lock["repository"] == "321Team/infra-kubernetes", "wrong source repository")
        commit = lock["commit"]
        files = lock["files"]
        required = {"scripts/run-node-firewall.py", "scripts/node-firewall.py", "scripts/node-firewall-session.py",
                    "scripts/run-node-firewall-cni.py", "scripts/cp1_minimal_consumer.py",
                    "scripts/cp1_minimal_change.py", "scripts/run-cp1-minimal-change.py",
                    "scripts/analyze-cp1-cni-trial.py",
                    "docs/runbooks/NODE_FIREWALL_FOLLOWUP.md", "policies/icr-d-net-01-01/node-firewall-v2.json"}
        node.need(required <= set(files), "incomplete source lock")
        for name, expected in files.items():
            path = KIT / name
            node.need(not Path(name).is_absolute() and ".." not in Path(name).parts and not path.is_symlink(), "unsafe source path")
            node.need(digest(path) == expected, "source checksum mismatch: " + name)
    else:
        commit = checked(["git", "rev-parse", "HEAD"], KIT)
        node.need(not checked(["git", "status", "--porcelain", "--untracked-files=no"], KIT), "source checkout is modified")
        origin = checked(["git", "remote", "get-url", "origin"], KIT)
        node.need(re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)321Team/infra-kubernetes(?:\.git)?", origin), "source origin differs")
    node.need(re.fullmatch(r"[0-9a-f]{40}", commit), "invalid source commit")
    return commit


def validate_inventory(inv):
    for group_name, expected in (("k8s_control_plane", node.CP), ("k8s_workers", node.WORKERS)):
        group = inv.get(group_name, {})
        node.need(set(group.get("hosts", [])) == set(expected) and not group.get("children"), "inventory group differs: " + group_name)
    for name, ip in node.NODES.items():
        variables = inv["_meta"]["hostvars"][name]
        node.need(variables.get("ansible_host") == ip and variables.get("ansible_user", "svc_ansible") == "svc_ansible", "inventory identity differs: " + name)


def preflight():
    node.need(os.geteuid() != 0 and pwd.getpwuid(os.getuid()).pw_name == "server01_ansible", "run as server01_ansible on ansible01, without sudo")
    node.need(checked(["git", "rev-parse", "HEAD"], ANSIBLE_REPO) == ANSIBLE_COMMIT, "approved Ansible commit differs")
    origin = checked(["git", "remote", "get-url", "origin"], ANSIBLE_REPO)
    node.need(re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)321Team/infra-ansible(?:\.git)?", origin), "Ansible origin differs")
    node.need(not checked(["git", "status", "--porcelain", "--untracked-files=no", "--", "ansible.cfg", "inventory"], ANSIBLE_REPO), "Ansible configuration/inventory is modified")
    env = controller_env()
    env.update(ANSIBLE_CONFIG=str(ANSIBLE_REPO / "ansible.cfg"), ANSIBLE_HOST_KEY_CHECKING="True")
    raw = checked(["ansible-inventory", "-i", str(ANSIBLE_REPO / "inventory/hosts.yml"), "--list"], ANSIBLE_REPO, env)
    validate_inventory(json.loads(raw))
    for path in (KEY, KNOWN_HOSTS):
        info = path.lstat()
        node.need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022, "unsafe SSH file: " + str(path))
    key_info = KEY.stat()
    node.need(not key_info.st_mode & 0o077, "private key must be owner-only")
    directory = KEY.parent.lstat()
    node.need(stat.S_ISDIR(directory.st_mode) and directory.st_uid == os.getuid() and not directory.st_mode & 0o077, "unsafe SSH directory")
    public = checked(["ssh-keygen", "-y", "-P", "", "-f", str(KEY)])
    result = run(["ssh-keygen", "-E", "sha256", "-lf", "-"], input=public + "\n")
    node.need(result.returncode == 0 and len(result.stdout.split()) >= 2 and result.stdout.split()[1] == KEY_FINGERPRINT, "approved SSH key fingerprint differs")
    addresses = json.loads(checked(["ip", "-j", "-4", "addr", "show"]))
    node.need(any(value.get("local") == SOURCE_IP for item in addresses for value in item.get("addr_info", [])),
              "approved SSH source address is not assigned on the controller")
    return {"inventory_commit": ANSIBLE_COMMIT, "ssh_key_fingerprint": KEY_FINGERPRINT,
            "controller_source_ip": SOURCE_IP, "remote_user": "svc_ansible"}


def ssh_argv(name, remote):
    node.need(name in node.NODES, "unknown SSH target")
    options = {"BatchMode": "yes", "PreferredAuthentications": "publickey", "PasswordAuthentication": "no",
               "KbdInteractiveAuthentication": "no", "StrictHostKeyChecking": "yes", "UserKnownHostsFile": str(KNOWN_HOSTS),
               "IdentitiesOnly": "yes", "IdentityAgent": "none", "ControlMaster": "no", "ControlPersist": "no",
               "ControlPath": "none", "ConnectTimeout": "10", "ConnectionAttempts": "1", "ServerAliveInterval": "10",
               "ServerAliveCountMax": "2", "UpdateHostKeys": "no", "PermitLocalCommand": "no"}
    argv = ["ssh", "-F", "/dev/null", "-i", str(KEY), "-b", SOURCE_IP, "-T"]
    for key, value in options.items():
        argv += ["-o", key + "=" + value]
    return argv + ["svc_ansible@" + node.NODES[name], shlex.join(remote)]


def remote_request(name, request):
    phase = "SSH_IDENTITY"
    result = None
    try:
        identity_code = "import os,pwd,socket,json;print(json.dumps({'user':pwd.getpwuid(os.getuid()).pw_name,'hostname':socket.gethostname().split('.')[0],'connection':os.environ.get('SSH_CONNECTION','').split()}))"
        result = run(ssh_argv(name, ["/usr/bin/python3", "-I", "-c", identity_code]), timeout=40)
        node.need(result.returncode == 0, "SSH identity failed; rc=" + str(result.returncode) + "; " + node.redact(result.stderr))
        identity = json.loads(result.stdout)
        connection = identity.get("connection", [])
        node.need(identity["user"] == "svc_ansible" and identity["hostname"] == name and len(connection) == 4 and connection[0] == SOURCE_IP and connection[2] == node.NODES[name], "SSH session identity differs")
        phase = "SUDO_PYTHON"
        payload = "REQUEST = " + repr(request) + "\n" + NODE_SOURCE.read_text()
        result = run(ssh_argv(name, ["sudo", "-n", "/usr/bin/python3", "-I", "-"]), input=payload, timeout=720)
        node.need(result.returncode == 0, "remote process failed; rc=" + str(result.returncode) + "; " + node.redact(result.stderr))
        phase = "RESULT_PARSE"
        data = json.loads(result.stdout)
        node.need(isinstance(data, dict) and "status" in data, "invalid remote result")
        node.need(isinstance(data["status"], str), "invalid remote status")
        node.need(data["status"] == "STOP" or data.get("hostname", data.get("receipt", {}).get("hostname")) == name, "result node differs")
        return {"transport": "PASS", "identity": identity, "stderr": node.redact(result.stderr), "result": data}
    except subprocess.TimeoutExpired as error:
        detail = error.stderr or ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        return {"transport": "ERROR", "phase": phase, "error": "TIMEOUT; outcome may be unknown", "stderr": node.redact(detail)}
    except Exception as error:
        return {"transport": "ERROR", "phase": phase, "error": node.redact(error),
                "stderr": node.redact(result.stderr)[:8000] if result else "",
                "stdout_excerpt": node.redact(result.stdout)[:1000] if result else ""}


def bind_policy_reader(policy, policy_sha256):
    """Supply the verified policy to the consumer's CP1 read-only request.

    remote_status deliberately knows only the CP1 host and mode. The same
    helper protocol used by inspect also requires policy and policy_sha256.
    Keep the existing transport and remote validation, with an explicit
    read-only boundary on this callback.
    """
    policy = copy.deepcopy(policy)

    def read(name, request):
        node.need(name == request.get("node") == "k8s-control01"
                  and request.get("mode") == "cp1-state", "CP1 read-only request required")
        return remote_request(name, dict(request, policy=copy.deepcopy(policy),
                                         policy_sha256=policy_sha256))

    return read


def archive_output(directory):
    files = sorted(p for p in directory.iterdir() if p.is_file())
    (directory / "SHA256SUMS").write_text("".join(digest(p) + "  " + p.name + "\n" for p in files))
    archive = directory.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(directory, arcname=directory.name)
    sidecar = Path(str(archive) + ".sha256")
    sidecar.write_text(digest(archive) + "  " + archive.name + "\n")
    return archive, sidecar


def read_checked(path):
    """Read a small input once under O_NOFOLLOW; return (raw bytes, sha256 hex)."""
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        node.need(stat.S_ISREG(info.st_mode) and info.st_size <= 8 * 1024 * 1024, "unsafe input")
        raw = handle.read(8 * 1024 * 1024 + 1)
    node.need(len(raw) <= 8 * 1024 * 1024, "unsafe input")
    return raw, node.sha(raw)


def parse_unique(raw):
    def unique(pairs):
        data = {}
        for key, value in pairs:
            node.need(key not in data, "duplicate JSON key")
            data[key] = value
        return data
    return json.loads(raw, object_pairs_hook=unique)


def load_checked(path, expected):
    node.need(path and expected and re.fullmatch(r"[0-9a-f]{64}", expected), "input path and SHA256 are required")
    raw, actual = read_checked(path)
    node.need(actual == expected, "input checksum mismatch")
    return parse_unique(raw)


def require_result(wrapper, status):
    node.need(wrapper.get("transport") == "PASS" and wrapper.get("result", {}).get("status") == status,
              "remote result did not satisfy " + status)
    return wrapper["result"]


def validate_authorization(data, commit, target, mode):
    node.validate_authorization_record(data, commit, target, mode)


def cp1_rules():
    return [f'rule family="ipv4" source address="{ip}/32" port port="{port}" protocol="tcp" accept'
            for ip in node.CP.values() for port in ("2379-2380", "10250")]


def validate_cp1_receipt(wrapper):
    if "transport" in wrapper:
        node.need(wrapper["transport"] == "PASS", "CP1 receipt transport failed")
    data = wrapper.get("result", wrapper)
    receipt = data.get("receipt", {})
    node.need(data.get("status") == receipt.get("status") == "RUNTIME_APPLIED_REVIEW_REQUIRED", "CP1 apply receipt absent")
    node.need(receipt.get("hostname") == "k8s-control01" and receipt.get("schema") == "fds.cp1-canary-receipt.v2"
              and receipt.get("phase") == "runtime", "wrong CP1 receipt")
    for key, value in node.CP1_CONTRACT.items():
        node.need(receipt.get(key) == value, "CP1 source/plan differs: " + key)
    node.need(node.fingerprint(receipt) == data.get("receipt_sha256"), "CP1 receipt content checksum differs")
    node.need(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", receipt.get("run_id") or ""), "CP1 run ID differs")
    for key in ("authorization_sha256", "topology_sha256"):
        node.digest_field(receipt.get(key), "CP1 digest absent: " + key)
    window = receipt.get("authorization_window")
    node.need(isinstance(window, dict) and window.get("end_exclusive") is True,
              "CP1 authorization window absent")
    node.need(all(isinstance(window.get(key), str) for key in ("start", "end")), "CP1 window timestamps absent")
    start, end = node.instant(window["start"]), node.instant(window["end"])
    node.need(0 < (end - start).total_seconds() <= 7200 and start <= node.instant(receipt["started_at"]) < end,
              "CP1 authorization window invalid")
    rules = cp1_rules()
    ops = receipt.get("operations", [])
    node.need(isinstance(ops, list) and len(ops) == len(rules), "CP1 apply is incomplete")
    before = receipt["before"]
    node.need(before.get("hostname") == "k8s-control01", "CP1 snapshot identity differs")
    expected = copy.deepcopy(before)
    for op, rule in zip(ops, rules):
        node.need(op.get("plane") == "runtime" and op.get("rule") == rule, "CP1 ordered operation differs")
        node.need(isinstance(op.get("started_at"), str), "CP1 operation timestamp absent")
        node.need(start <= node.instant(op["started_at"]) < end, "CP1 operation outside authorization window")
        present = node.canonical_rule(rule) in before["planes"]["runtime"]["rules"]
        node.need(type(op.get("present_before")) is bool and op["present_before"] == present,
                  "CP1 original presence differs")
        node.need(op.get("status") == ("preserved" if present else "added"), "CP1 ownership/status differs")
        if not present:
            node.need(node.outcome(op.get("result"), True, True, False) == "changed", "CP1 mutation result uncertain")
            expected["planes"]["runtime"]["rules"].append(node.canonical_rule(rule))
            expected["planes"]["runtime"]["rules"].sort()
        else:
            node.need("result" not in op, "preserved CP1 rule has mutation result")
    node.need(receipt.get("after") == expected and receipt["after"]["planes"]["permanent"] == before["planes"]["permanent"],
              "CP1 after-state or Permanent plane differs")
    node.need(node.instant(receipt["started_at"]) <= node.instant(receipt["ended_at"]) <= dt.datetime.now(dt.timezone.utc),
              "CP1 receipt timestamp invalid")
    return receipt


def validate_cp1_acceptance(data, receipt, receipt_input_sha256):
    node.need(data.get("schema") == "fds.cp1-runtime-acceptance.v1" and data.get("decision") == "ACCEPTED",
              "CP1 Runtime acceptance absent")
    for key in node.CP1_CONTRACT:
        node.need(data.get(key) == receipt[key], "CP1 acceptance source/plan differs: " + key)
    node.need(data.get("run_id") == receipt["run_id"] and data.get("receipt_sha256") == node.fingerprint(receipt)
              and data.get("receipt_input_sha256") == receipt_input_sha256, "CP1 acceptance receipt differs")
    node.need(data.get("independent_reviewer") == "hayeounglee" and data.get("review_decision") == "APPROVED",
              "CP1 independent acceptance review absent")
    node.need(re.fullmatch(r"https://github\.com/321Team/infra-kubernetes/pull/2#(?:pullrequestreview|issuecomment)-[0-9]+",
                          data.get("review_url") or ""), "CP1 acceptance review URL absent")
    reviewed = node.instant(data["reviewed_at"])
    node.need(node.instant(receipt["ended_at"]) <= reviewed <= dt.datetime.now(dt.timezone.utc),
              "CP1 acceptance predates Runtime result or is in future")
    checks = data.get("checks", {})
    node.need(set(checks) == {"positive", "negative", "regression"}, "CP1 acceptance test categories incomplete")
    for key, result in checks.items():
        node.need(result.get("status") == "PASS", "CP1 acceptance check not passed: " + key)
        node.digest_field(result.get("evidence_sha256"), "CP1 acceptance evidence absent: " + key)
    # Hashes and URLs bind the operator's inputs. A person must verify the linked review and original evidence.


def cp1_state_gate(state, expected=None, receipt=None):
    node.need(node.fingerprint(state["stable"]) == state["sha256"], "CP1 state digest differs")
    if expected is not None:
        node.need(state["sha256"] == expected, "CP1 firewall/protection changed since inspection")
    if receipt is not None:
        node.need(state["stable"]["firewall"] == receipt["after"], "CP1 accepted Runtime state no longer present")


def topology_gate(topology, target, expected=None, unjoined=False):
    node.need(node.fingerprint(topology["stable"]) == topology["sha256"], "topology digest differs")
    if expected:
        node.need(topology["sha256"] == expected, "cluster membership changed; stop before firewall mutation")
    if unjoined:
        node.need(target not in [n[0] for n in topology["stable"]["nodes"]], "target already registered; staged pre-join plan does not apply")
        node.need(not any(m.get("name") == target or "https://" + node.NODES[target] + ":2380" in m.get("peerURLs", [])
                          for m in topology["stable"]["members"]), "target etcd member already exists")
    node.need(any(m.get("name") == "k8s-control01" and not m.get("isLearner") for m in topology["stable"]["members"]),
              "CP1 voting member absent")


def main():
    from types import SimpleNamespace
    controller = SimpleNamespace(cp1_state_gate=cp1_state_gate, remote_request=remote_request,
        require_result=require_result, re=re, run=run, ssh_argv=ssh_argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "apply", "rollback", "receipt", "promote-permanent"))
    parser.add_argument("--node", required=True, choices=tuple(node.TARGETS))
    for field in ("inspection", "authorization", "receipt", "cp1-receipt", "cp1-acceptance", "cp1-handoff", "cp1-trial"):
        parser.add_argument("--" + field)
        parser.add_argument("--" + field + "-sha256")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    os.umask(0o077)
    out = Path(tempfile.mkdtemp(prefix="FDS_Node_Firewall_", dir=Path.cwd()))
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(6)
    lock = None
    summary = {"schema": "fds.node-firewall-controller.v2", "phase": "runtime", "mode": args.mode,
               "hostname": args.node, "run_id": run_id, "started_at": node.now(), "status": "STARTING",
               "runtime_applied": "not_attempted", "permanent_applied": "not_attempted"}
    print("OUTPUT=" + str(out), flush=True)
    try:
        node.need(args.mode != "promote-permanent",
                  "Permanent promotion NOT_IMPLEMENTED; Runtime acceptance and a separate reviewed change are required")
        commit = verify_source()
        policy_raw, _ = read_checked(POLICY_PATH)
        policy = parse_unique(policy_raw)
        node.validate_policy(policy)
        controller.remote_request = bind_policy_reader(policy, node.policy_hash())
        summary.update(source_commit=commit, policy_sha256=node.policy_hash())
        (out / "POLICY.json").write_bytes(node.encoded(policy))
        summary["preflight"] = preflight()
        if args.mode in ("apply", "rollback"):
            lock = ControllerLock()
        request = {"mode": args.mode, "node": args.node, "source_commit": commit,
                   "source_sha256": digest(NODE_SOURCE), "run_id": run_id,
                   "plan_sha256": node.plan_hash(args.node), "policy": policy, "policy_sha256": node.policy_hash()}

        def remote(name, mode, filename):
            wrapper = remote_request(name, dict(request, mode=mode, node=name))
            (out / filename).write_bytes(node.encoded(wrapper))
            return wrapper

        if args.mode == "inspect":
            target = require_result(remote(args.node, "inspect", "TARGET.json"), "INSPECTED_REVIEW_REQUIRED")
            node.need(target.get("phase") == "runtime" and target.get("policy_sha256") == request["policy_sha256"]
                      and target.get("plan_sha256") == request["plan_sha256"], "target helper policy/version differs")
            node.need(node.fingerprint(target["snapshot"]) == target["before_sha256"], "target snapshot digest differs")
            topology = require_result(remote("k8s-control01", "topology", "CP1_TOPOLOGY.json"), "TOPOLOGY_REVIEW_REQUIRED")
            topology_gate(topology, args.node, unjoined=True)
            cp1_state = require_result(remote("k8s-control01", "cp1-state", "CP1_STATE.json"), "CP1_STATE_REVIEW_REQUIRED")
            cp1_state_gate(cp1_state)
            inputs = {"schema": "fds.node-firewall-input.v2", "phase": "runtime", "hostname": args.node,
                      "created_at": summary["started_at"], "source_commit": commit,
                      "source_sha256": request["source_sha256"], "plan_sha256": request["plan_sha256"],
                      "policy_sha256": request["policy_sha256"], "snapshot": target["snapshot"],
                      "before_sha256": target["before_sha256"],
                      "residual_metadata_sha256": node.fingerprint(target["snapshot"]["residual_metadata"]),
                      "topology": topology["stable"], "topology_sha256": topology["sha256"],
                      "cp1_state": cp1_state["stable"], "cp1_state_sha256": cp1_state["sha256"]}
            (out / "INSPECTION.json").write_bytes(node.encoded(inputs))
            summary["status"] = "INSPECTED_REVIEW_REQUIRED"
        elif args.mode == "receipt":
            node.need(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", args.run_id or ""), "valid original run ID required")
            request["run_id"] = args.run_id
            wrapper = remote(args.node, "receipt", "TARGET.json")
            node.need(wrapper.get("transport") == "PASS" and "receipt" in wrapper.get("result", {}), "receipt retrieval failed")
            data = wrapper["result"]
            node.need(node.fingerprint(data["receipt"]) == data["receipt_sha256"], "retrieved receipt digest differs")
            summary["receipt_schema"] = data["receipt"].get("schema")
            summary["legacy_recovery_review_required"] = summary["receipt_schema"] != "fds.node-firewall-receipt.v2"
            summary["status"] = "RECEIPT_RETRIEVED_REVIEW_REQUIRED"
        else:
            auth = load_checked(args.authorization, args.authorization_sha256)
            validate_authorization(auth, commit, args.node, args.mode)
            inputs = load_checked(args.inspection, args.inspection_sha256)
            node.need(inputs.get("schema") == "fds.node-firewall-input.v2" and inputs.get("phase") == "runtime"
                      and inputs.get("hostname") == args.node, "wrong inspection")
            for key in ("source_commit", "source_sha256", "plan_sha256", "policy_sha256"):
                node.need(inputs[key] == request[key], "inspection source/policy differs: " + key)
            node.fresh(inputs["created_at"])
            for body, digest_key in (("snapshot", "before_sha256"), ("topology", "topology_sha256"),
                                     ("cp1_state", "cp1_state_sha256")):
                node.need(node.fingerprint(inputs[body]) == inputs[digest_key], "inspection content digest differs: " + body)
            node.need(node.fingerprint(inputs["snapshot"]["residual_metadata"]) == inputs["residual_metadata_sha256"],
                      "inspection residual metadata digest differs")
            for key in ("source_sha256", "plan_sha256", "policy_sha256", "before_sha256",
                        "residual_metadata_sha256", "cp1_state_sha256"):
                node.need(auth.get(key) == inputs[key], "authorization input binding differs: " + key)
            node.need(auth.get("inspection_sha256") == args.inspection_sha256, "authorization inspection file differs")
            node.validate_zone_controls(inputs["snapshot"], auth.get("zone_controls"))
            request.update(before_sha256=inputs["before_sha256"], topology_sha256=inputs["topology_sha256"],
                           inspection_created_at=inputs["created_at"], zone_controls=auth["zone_controls"],
                           residual_metadata_sha256=inputs["residual_metadata_sha256"],
                           authorization_sha256=args.authorization_sha256, authorization=auth)
            (out / "AUTHORIZATION.json").write_bytes(node.encoded(auth))
            (out / "INPUT_INSPECTION.json").write_bytes(node.encoded(inputs))
            cp1_receipt = None
            cni_record = None
            if args.mode == "apply":
                proof = load_checked(args.cp1_receipt, args.cp1_receipt_sha256)
                cp1_receipt = validate_cp1_receipt(proof)
                acceptance = load_checked(args.cp1_acceptance, args.cp1_acceptance_sha256)
                handoff = load_checked(args.cp1_handoff, args.cp1_handoff_sha256)
                cni_record = cni.validate(proof, handoff, acceptance, args.cp1_trial, args.cp1_trial_sha256)
                (out / "CP1_CNI_HANDOFF.json").write_bytes(node.encoded(handoff))
                delegated = acceptance.get('schema') == 'fds.cp-session-continuation.v1'
                if delegated:
                    node.need(auth.get('schema') == 'fds.node-firewall-authorization.v3' and
                              auth.get('cp1_canary_accepted') is False and auth.get('cp1_continuation_authorized') is True and
                              auth.get('cp1_continuation_sha256') == args.cp1_acceptance_sha256 and
                              auth.get('cp1_session_job') == cni_record['job_id'] and
                              auth.get('operator_decision_url') == cni_record['operator_decision_url'] and
                              auth.get('review_url') == cni_record['review_url'] and
                              auth.get('cp1_receipt_input_sha256') == args.cp1_receipt_sha256 and
                              args.node in acceptance['targets'], 'session target/source/authority binding differs')
                    summary.update(independent_acceptance='PENDING', cp1_accepted=False)
                else:
                    node.need(auth.get('schema') == 'fds.node-firewall-authorization.v2' and
                              auth.get("cp1_canary_accepted") is True and
                              auth.get("cp1_receipt_input_sha256") == args.cp1_receipt_sha256 and
                              auth.get("cp1_acceptance_sha256") == args.cp1_acceptance_sha256,
                              "authorization CP1 receipt/acceptance binding differs")
                request.update(cp1_receipt_sha256=node.fingerprint(cp1_receipt),
                               cp1_receipt_input_sha256=args.cp1_receipt_sha256,
                               cp1_acceptance_sha256=None if delegated else args.cp1_acceptance_sha256)
                if delegated:
                    request['cp1_continuation_sha256'] = args.cp1_acceptance_sha256
                (out / "CP1_RECEIPT.json").write_bytes(node.encoded(proof))
                (out / ("CP1_SESSION_DECISION.json" if delegated else "CP1_ACCEPTANCE.json")).write_bytes(node.encoded(acceptance))
            else:
                wrapper = load_checked(args.receipt, args.receipt_sha256)
                receipt_data = wrapper.get("result", wrapper)
                receipt = receipt_data["receipt"]
                node.need(auth.get("receipt_input_sha256") == args.receipt_sha256, "authorization rollback receipt differs")
                request.update(run_id=receipt["run_id"], receipt_sha256=receipt_data["receipt_sha256"])
                node.need(node.fingerprint(receipt) == request["receipt_sha256"], "receipt body digest differs")
                node.validate_receipt(receipt, request)
                (out / "INPUT_RECEIPT.json").write_bytes(node.encoded(wrapper))
            topology = require_result(remote("k8s-control01", "topology", "CP1_BEFORE.json"), "TOPOLOGY_REVIEW_REQUIRED")
            topology_gate(topology, args.node, request["topology_sha256"], unjoined=True)
            cp1_state = require_result(remote("k8s-control01", "cp1-state", "CP1_STATE_BEFORE.json"), "CP1_STATE_REVIEW_REQUIRED")
            if cni_record is not None:
                if cni_record.get('schema') == 'fds.cp1-minimal-change.v4':
                    cni.m.validate_session_decision(acceptance, handoff)
                else:
                    cni.m.validate_acceptance(acceptance, handoff)
                if cni_record.get('schema') in ('fds.cp1-minimal-change.v2', 'fds.cp1-minimal-change.v3', 'fds.cp1-minimal-change.v4'):
                    live = cni.before_or_after(controller, cp1_state, inputs["cp1_state_sha256"], cni_record,
                                               acceptance=acceptance, required_until=auth['window_end'])
                else:
                    live = cni.before_or_after(controller, cp1_state, inputs["cp1_state_sha256"], cni_record, minimum_seconds=900)
                (out / "CP1_CNI_LIVE_BEFORE.json").write_bytes(node.encoded(live))
            else:
                cp1_state_gate(cp1_state, inputs["cp1_state_sha256"], cp1_receipt)
            (out / "REQUEST.json").write_bytes(node.encoded(request))
            print("RUN_ID=" + request["run_id"], flush=True)
            summary["runtime_applied"] = "attempted_requires_receipt_review"
            wrapper = remote(args.node, args.mode, "TARGET.json")
            success = "RUNTIME_APPLIED_REVIEW_REQUIRED" if args.mode == "apply" else "ROLLED_BACK_REVIEW_REQUIRED"
            data = require_result(wrapper, success)
            node.need(node.fingerprint(data["receipt"]) == data["receipt_sha256"], "remote receipt body digest differs")
            if args.mode == "apply":
                node.validate_receipt(data["receipt"], request)
            post = require_result(remote(args.node, "inspect", "TARGET_AFTER.json"), "INSPECTED_REVIEW_REQUIRED")
            expected = data["receipt"]["after" if args.mode == "apply" else "before"]
            node.need(post["snapshot"] == expected, "post-change verification differs")
            topology = require_result(remote("k8s-control01", "topology", "CP1_AFTER.json"), "TOPOLOGY_REVIEW_REQUIRED")
            topology_gate(topology, args.node, request["topology_sha256"], unjoined=True)
            cp1_state = require_result(remote("k8s-control01", "cp1-state", "CP1_STATE_AFTER.json"), "CP1_STATE_REVIEW_REQUIRED")
            if cni_record is not None:
                if cni_record.get('schema') == 'fds.cp1-minimal-change.v4':
                    cni.m.validate_session_decision(acceptance, handoff)
                else:
                    cni.m.validate_acceptance(acceptance, handoff)
                live = cni.before_or_after(controller, cp1_state, inputs["cp1_state_sha256"], cni_record,
                                           acceptance=acceptance if cni_record.get('schema') in ('fds.cp1-minimal-change.v2', 'fds.cp1-minimal-change.v3', 'fds.cp1-minimal-change.v4') else None)
                (out / "CP1_CNI_LIVE_AFTER.json").write_bytes(node.encoded(live))
            else:
                cp1_state_gate(cp1_state, inputs["cp1_state_sha256"], cp1_receipt)
            summary["status"] = success
            summary["runtime_applied"] = "applied" if args.mode == "apply" else "rolled_back"
    except (Exception, KeyboardInterrupt) as error:
        summary["status"] = "STOP_REVIEW_REQUIRED"
        summary["error"] = node.redact(error)
    finally:
        if lock:
            lock.close()
        summary["ended_at"] = node.now()
        (out / "RESULT.json").write_bytes(node.encoded(summary))
        archive, sidecar = archive_output(out)
        print("STATUS=" + summary["status"])
        print("ARCHIVE=" + str(archive))
        print("SIDECAR=" + str(sidecar))
    raise SystemExit(2 if summary["status"] == "STOP_REVIEW_REQUIRED" else 0)


if __name__ == "__main__":
    main()
