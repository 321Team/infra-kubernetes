#!/usr/bin/env python3
"""Pinned controller for read-only CP inspection and explicit CP1 transactions."""
import argparse
import datetime as dt
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
NODE_SOURCE = KIT / "scripts/cp-canary-node.py"
spec = importlib.util.spec_from_file_location("cp_canary_node", NODE_SOURCE)
node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)
ANSIBLE_COMMIT = "5cbf3b657014b32a20cb5816d90d3ea5fe4951ab"
ANSIBLE_REPO = Path("/home/server01_ansible/p1-ans-01")
KEY = Path("/home/server01_ansible/.ssh/p1_ansible_ed25519")
KNOWN_HOSTS = KEY.parent / "known_hosts"
KEY_FINGERPRINT = "SHA256:VPGV/RGA8TrwO/3FC4YPxGzRNhBWuB+9hCzPV5plkws"
SOURCE_IP = "10.250.10.10"


def run(argv, cwd=None, input=None, timeout=30, env=None):
    return subprocess.run(argv, cwd=cwd, input=input, text=True, capture_output=True,
                          timeout=timeout, env=env)


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
        required = {"scripts/run-cp-canary.py", "scripts/cp-canary-node.py",
                    "policies/icr-d-net-01-01/cp-firewall-v1.json",
                    "docs/change/ICR-D-NET-01-01/cp-firewall-plan-20260910.json"}
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
    plan = KIT / "docs/change/ICR-D-NET-01-01/cp-firewall-plan-20260910.json"
    node.need(digest(plan) == node.PLAN, "plan checksum mismatch")
    return commit


def validate_inventory(inv):
    group = inv.get("k8s_control_plane", {})
    node.need(set(group.get("hosts", [])) == set(node.NODES) and not group.get("children"), "CP inventory group differs")
    for name, ip in node.NODES.items():
        variables = inv["_meta"]["hostvars"][name]
        node.need(variables.get("ansible_host") == ip and variables.get("ansible_user", "svc_ansible") == "svc_ansible", "inventory identity differs: " + name)


def preflight():
    node.need(os.geteuid() != 0 and pwd.getpwuid(os.getuid()).pw_name == "server01_ansible", "run as server01_ansible on ansible01, without sudo")
    node.need(checked(["git", "rev-parse", "HEAD"], ANSIBLE_REPO) == ANSIBLE_COMMIT, "approved Ansible commit differs")
    origin = checked(["git", "remote", "get-url", "origin"], ANSIBLE_REPO)
    node.need(re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)321Team/infra-ansible(?:\.git)?", origin), "Ansible origin differs")
    node.need(not checked(["git", "status", "--porcelain", "--untracked-files=no", "--", "ansible.cfg", "inventory"], ANSIBLE_REPO), "Ansible configuration/inventory is modified")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANSIBLE_", "PYTHON"))}
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
        result = run(ssh_argv(name, ["/usr/bin/python3", "-c", identity_code]), timeout=40)
        node.need(result.returncode == 0, "SSH identity failed; rc=" + str(result.returncode) + "; " + node.redact(result.stderr))
        identity = json.loads(result.stdout)
        connection = identity.get("connection", [])
        node.need(identity["user"] == "svc_ansible" and identity["hostname"] == name and len(connection) == 4 and connection[0] == SOURCE_IP and connection[2] == node.NODES[name], "SSH session identity differs")
        phase = "SUDO_PYTHON"
        payload = "REQUEST = " + repr(request) + "\n" + NODE_SOURCE.read_text()
        result = run(ssh_argv(name, ["sudo", "-n", "/usr/bin/python3", "-"]), input=payload, timeout=900)
        node.need(result.returncode == 0, "remote process failed; rc=" + str(result.returncode) + "; " + node.redact(result.stderr))
        phase = "RESULT_PARSE"
        data = json.loads(result.stdout)
        node.need(isinstance(data, dict) and "status" in data, "invalid remote result")
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


def load_checked(path, expected):
    node.need(re.fullmatch(r"[0-9a-f]{64}", expected or ""), "provide the input file SHA-256")
    node.need(digest(path) == expected, "input checksum mismatch")
    return json.loads(path.read_text())


def request_for(args, commit, run_id):
    request = {"mode": args.mode, "node": "k8s-control01", "source_commit": commit,
               "source_sha256": digest(NODE_SOURCE), "plan_sha256": node.PLAN, "run_id": run_id}
    if args.mode == "apply":
        node.need(args.input is not None, "apply requires the CP1 inspection JSON")
        wrapper = load_checked(args.input, args.input_sha256)
        node.need(wrapper["source_commit"] == commit and wrapper["source_sha256"] == request["source_sha256"], "inspection source differs")
        node.need(wrapper["transport"] == "PASS", "inspection transport failed")
        data = wrapper["result"]
        node.need(data["hostname"] == "k8s-control01" and data["canary_inputs_complete"], "CP1 inspection is incomplete")
        node.need(node.fingerprint(data["snapshot"]) == data["before_sha256"] and node.fingerprint(data["topology"]) == data["topology_sha256"], "inspection state hash differs")
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(data["created_at"])).total_seconds()
        node.need(0 <= age <= 900, "inspection older than 15 minutes; inspect again")
        request.update(inspection_created_at=data["created_at"], before_sha256=data["before_sha256"], topology_sha256=data["topology_sha256"])
    elif args.mode == "rollback":
        node.need(args.input is not None, "rollback requires the CP1 result JSON")
        wrapper = load_checked(args.input, args.input_sha256)
        data = wrapper["result"]
        receipt = data["receipt"]
        node.need(node.fingerprint(receipt) == data["receipt_sha256"], "embedded receipt hash differs")
        node.need(receipt["source_sha256"] == request["source_sha256"] and receipt["source_commit"] == commit, "receipt source differs")
        request.update(run_id=receipt["run_id"], receipt_sha256=data["receipt_sha256"])
    elif args.mode == "receipt":
        node.need(args.run_id is not None, "receipt requires --run-id from the interrupted run")
        request["run_id"] = args.run_id
    return request


def archive_output(directory):
    files = sorted(p for p in directory.iterdir() if p.is_file())
    (directory / "SHA256SUMS").write_text("".join(digest(p) + "  " + p.name + "\n" for p in files))
    archive = directory.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(directory, arcname=directory.name)
    sidecar = Path(str(archive) + ".sha256")
    sidecar.write_text(digest(archive) + "  " + archive.name + "\n")
    return archive, sidecar


def target_summary(wrapper):
    data = wrapper.get("result", {})
    return {"transport": wrapper["transport"], "status": data.get("status", "TRANSPORT_ERROR"),
            "diagnostic_gaps": data.get("gaps", []), "failed_queries": data.get("failed_queries", []),
            "canary_inputs_complete": data.get("canary_inputs_complete")}


def collection_status(targets):
    incomplete = any(v["transport"] != "PASS" or v["status"] == "STOP" or v["status"].startswith("PARTIAL_")
                     or v.get("diagnostic_gaps") or v.get("failed_queries") for v in targets.values())
    return "INCOMPLETE_REVIEW_REQUIRED" if incomplete else "COLLECTED_REVIEW_REQUIRED"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "apply", "rollback", "receipt"), nargs="?", default="inspect")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--input-sha256")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    os.umask(0o077)
    out = Path(tempfile.mkdtemp(prefix="FDS_CP1_Canary_", dir=Path.cwd()))
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(6)
    summary = {"schema": "fds.cp-canary-controller.v1", "mode": args.mode, "run_id": run_id,
               "started_at": node.now(), "status": "STARTING", "targets": {}, "runtime_applied": "not_attempted"}
    print("OUTPUT=" + str(out), flush=True)
    print("RUN_ID=" + run_id, flush=True)
    try:
        commit = verify_source()
        summary["source_commit"] = commit
        summary["preflight"] = preflight()
        request = request_for(args, commit, run_id)
        summary["run_id"] = request["run_id"]
        targets = list(node.NODES) if args.mode == "inspect" else ["k8s-control01"]
        (out / "REQUEST.json").write_bytes(node.encoded(request))
        for name in targets:
            print(name + "=" + args.mode.upper() + "_STARTED", flush=True)
            request["node"] = name
            if args.mode in ("apply", "rollback"):
                summary["runtime_applied"] = "unknown_until_receipt_review"
            result = remote_request(name, request)
            result.update(source_commit=commit, source_sha256=digest(NODE_SOURCE), mode=args.mode)
            (out / (name + ".json")).write_bytes(node.encoded(result))
            summary["targets"][name] = target_summary(result)
            print(name + "=" + summary["targets"][name]["status"], flush=True)
        summary["status"] = collection_status(summary["targets"])
    except (Exception, KeyboardInterrupt) as error:
        summary["status"] = "STOP_REVIEW_REQUIRED"
        summary["error"] = node.redact(error)
    finally:
        summary["ended_at"] = node.now()
        (out / "RESULT.json").write_bytes(node.encoded(summary))
        archive, sidecar = archive_output(out)
        print("STATUS=" + summary["status"])
        print("ARCHIVE=" + str(archive))
        print("SIDECAR=" + str(sidecar))
    raise SystemExit(0 if summary["status"] == "COLLECTED_REVIEW_REQUIRED" else 2)


if __name__ == "__main__":
    main()
