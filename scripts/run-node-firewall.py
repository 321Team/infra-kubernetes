#!/usr/bin/env python3
"""Pinned one-node firewall controller; no join/reset/NTP or bulk apply."""
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
NODE_SOURCE = KIT / "scripts/node-firewall.py"
spec = importlib.util.spec_from_file_location("worker_followup_node", NODE_SOURCE)
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
        required = {"scripts/run-node-firewall.py", "scripts/node-firewall.py",
                    "docs/runbooks/NODE_FIREWALL_FOLLOWUP.md"}
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
        result = run(ssh_argv(name, ["sudo", "-n", "/usr/bin/python3", "-"]), input=payload, timeout=720)
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


def archive_output(directory):
    files = sorted(p for p in directory.iterdir() if p.is_file())
    (directory / "SHA256SUMS").write_text("".join(digest(p) + "  " + p.name + "\n" for p in files))
    archive = directory.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(directory, arcname=directory.name)
    sidecar = Path(str(archive) + ".sha256")
    sidecar.write_text(digest(archive) + "  " + archive.name + "\n")
    return archive, sidecar


def load_checked(path, expected):
    node.need(path and expected and re.fullmatch(r"[0-9a-f]{64}", expected), "input path and SHA256 are required")
    path = Path(path)
    node.need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 8 * 1024 * 1024, "unsafe input")
    node.need(digest(path) == expected, "input checksum mismatch")
    return json.loads(path.read_bytes())


def require_result(wrapper, status):
    node.need(wrapper.get("transport") == "PASS" and wrapper.get("result", {}).get("status") == status,
              "remote result did not satisfy " + status)
    return wrapper["result"]


def validate_authorization(data, commit, target, mode):
    node.need(data.get("schema") == "fds.node-firewall-authorization.v1", "authorization schema differs")
    node.need(data.get("source_commit") == commit and data.get("target") == target and data.get("mode") == mode,
              "authorization is not for this commit/target/mode")
    node.need(data.get("operator") == "GWONUK" and isinstance(data.get("independent_reviewer"), str)
              and data["independent_reviewer"].strip() and data["independent_reviewer"].casefold() != "gwonuk", "independent review record absent")
    node.need(re.fullmatch(r"https://github\.com/321Team/infra-kubernetes/pull/[0-9]+#pullrequestreview-[0-9]+",
                          data.get("review_url") or ""), "review URL absent")
    node.need(data.get("review_decision") == "APPROVED" and data.get("maintenance_freeze") is True,
              "review approval and join/configuration freeze must be recorded")
    node.need(data.get("console_recovery_available") is True and data.get("cp1_canary_accepted") is True,
              "CP1 acceptance/console recovery record absent")
    node.need(re.fullmatch(r"[0-9a-f]{64}", data.get("cp1_acceptance_report_sha256") or ""), "CP1 acceptance report digest absent")
    start = dt.datetime.fromisoformat(data["window_start"])
    end = dt.datetime.fromisoformat(data["window_end"])
    current = dt.datetime.now(dt.timezone.utc)
    node.need(start <= current <= end and 0 < (end - start).total_seconds() <= 7200, "outside bounded operation window")
    node.need(current.astimezone(dt.timezone(dt.timedelta(hours=9))).weekday() < 5, "no weekend classroom runtime work")
    # These fields record human review. This offline controller does not authenticate GitHub approvals.


def validate_cp1_receipt(wrapper):
    data = wrapper.get("result", wrapper)
    receipt = data.get("receipt", {})
    node.need(data.get("status") == receipt.get("status") == "APPLIED_REVIEW_REQUIRED", "CP1 apply receipt absent")
    node.need(receipt.get("hostname") == "k8s-control01" and receipt.get("schema") == "fds.cp1-canary-receipt.v1", "wrong CP1 receipt")
    node.need(receipt.get("source_commit") == "068661e076de02669170d3582f3d596d6e1a8eb3" and
              receipt.get("source_sha256") == "5b1c9bccaf80cafc7f52eaf1ad9799d4a342981784f8fdb484013e27b19ae41b" and
              receipt.get("plan_sha256") == "2c1e6101724d078a0a8fd58497d4d84ae5453e938529496c3045b96ea0fef7d7", "CP1 source/plan differs")
    node.need(node.fingerprint(receipt) == data.get("receipt_sha256"), "CP1 receipt content checksum differs")
    node.need(len(receipt.get("operations", [])) == 12 and all(x.get("status") in ("added", "preserved") for x in receipt["operations"]),
              "CP1 apply is incomplete")


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "apply", "rollback", "receipt"))
    parser.add_argument("--node", required=True, choices=tuple(node.TARGETS))
    for field in ("inspection", "authorization", "receipt", "cp1-receipt"):
        parser.add_argument("--" + field)
        parser.add_argument("--" + field + "-sha256")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    os.umask(0o077)
    out = Path(tempfile.mkdtemp(prefix="FDS_Node_Firewall_", dir=Path.cwd()))
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(6)
    summary = {"schema": "fds.node-firewall-controller.v1", "mode": args.mode, "hostname": args.node,
               "run_id": run_id, "started_at": node.now(), "status": "STARTING", "runtime_applied": "not_attempted"}
    print("OUTPUT=" + str(out), flush=True)
    try:
        commit = verify_source()
        summary["source_commit"] = commit
        summary["preflight"] = preflight()
        request = {"mode": args.mode, "node": args.node, "source_commit": commit,
                   "source_sha256": digest(NODE_SOURCE), "run_id": run_id, "plan_sha256": node.plan_hash(args.node)}
        def remote(name, mode, filename):
            wrapper = remote_request(name, dict(request, mode=mode, node=name))
            (out / filename).write_bytes(node.encoded(wrapper))
            return wrapper
        if args.mode == "inspect":
            # Do not generate short-lived apply inputs until the actual operation window.
            target = require_result(remote(args.node, "inspect", "TARGET.json"), "INSPECTED_REVIEW_REQUIRED")
            topology = require_result(remote("k8s-control01", "topology", "CP1_TOPOLOGY.json"), "TOPOLOGY_REVIEW_REQUIRED")
            topology_gate(topology, args.node, unjoined=True)
            inputs = {"schema": "fds.node-firewall-input.v1", "hostname": args.node, "created_at": summary["started_at"],
                      "source_commit": commit, "source_sha256": request["source_sha256"], "plan_sha256": request["plan_sha256"],
                      "snapshot": target["snapshot"], "before_sha256": target["before_sha256"],
                      "topology": topology["stable"], "topology_sha256": topology["sha256"]}
            (out / "INSPECTION.json").write_bytes(node.encoded(inputs))
            summary["status"] = "INSPECTED_REVIEW_REQUIRED"
        elif args.mode == "receipt":
            node.need(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", args.run_id or ""), "valid original run ID required")
            request["run_id"] = args.run_id
            wrapper = remote(args.node, "receipt", "TARGET.json")
            node.need(wrapper.get("transport") == "PASS" and "receipt" in wrapper.get("result", {}), "receipt retrieval failed")
            summary["status"] = "RECEIPT_RETRIEVED_REVIEW_REQUIRED"
        else:
            auth = load_checked(args.authorization, args.authorization_sha256)
            validate_authorization(auth, commit, args.node, args.mode)
            request["authorization_sha256"] = args.authorization_sha256
            (out / "AUTHORIZATION.json").write_bytes(node.encoded(auth))
            if args.mode == "apply":
                validate_cp1_receipt(load_checked(args.cp1_receipt, args.cp1_receipt_sha256))
                summary["cp1_receipt_input_sha256"] = args.cp1_receipt_sha256
                inputs = load_checked(args.inspection, args.inspection_sha256)
                node.need(inputs.get("schema") == "fds.node-firewall-input.v1" and inputs.get("hostname") == args.node, "wrong inspection")
                for key in ("source_commit", "source_sha256", "plan_sha256"):
                    node.need(inputs[key] == request[key], "inspection source/plan differs")
                node.fresh(inputs["created_at"])
                node.need(node.fingerprint(inputs["snapshot"]) == inputs["before_sha256"] and
                          node.fingerprint(inputs["topology"]) == inputs["topology_sha256"], "inspection content digest differs")
                request.update(before_sha256=inputs["before_sha256"], topology_sha256=inputs["topology_sha256"],
                               inspection_created_at=inputs["created_at"])
                (out / "INPUT_INSPECTION.json").write_bytes(node.encoded(inputs))
            else:
                wrapper = load_checked(args.receipt, args.receipt_sha256)
                receipt_data = wrapper.get("result", wrapper)
                receipt = receipt_data["receipt"]
                request.update(run_id=receipt["run_id"], topology_sha256=receipt["topology_sha256"],
                               receipt_sha256=receipt_data["receipt_sha256"], inspection_created_at=node.now())
                node.need(node.fingerprint(receipt) == request["receipt_sha256"], "receipt body digest differs")
                node.validate_receipt(receipt, request)
                (out / "INPUT_RECEIPT.json").write_bytes(node.encoded(wrapper))
            topology = require_result(remote("k8s-control01", "topology", "CP1_BEFORE.json"), "TOPOLOGY_REVIEW_REQUIRED")
            topology_gate(topology, args.node, request["topology_sha256"], unjoined=True)
            (out / "REQUEST.json").write_bytes(node.encoded(request))
            print("RUN_ID=" + request["run_id"], flush=True)
            summary["runtime_applied"] = "attempted_requires_receipt_review"
            wrapper = remote(args.node, args.mode, "TARGET.json")
            success = "APPLIED_REVIEW_REQUIRED" if args.mode == "apply" else "ROLLED_BACK_REVIEW_REQUIRED"
            data = require_result(wrapper, success)
            # Each call uses a new SSH connection; verifies management remains available.
            post = require_result(remote(args.node, "inspect", "TARGET_AFTER.json"), "INSPECTED_REVIEW_REQUIRED")
            expected = data["receipt"]["after" if args.mode == "apply" else "before"]
            node.need(post["snapshot"] == expected, "post-change verification differs")
            topology = require_result(remote("k8s-control01", "topology", "CP1_AFTER.json"), "TOPOLOGY_REVIEW_REQUIRED")
            topology_gate(topology, args.node, request["topology_sha256"], unjoined=True)
            summary["status"] = success
            summary["runtime_applied"] = "applied" if args.mode == "apply" else "rolled_back"
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
    raise SystemExit(2 if summary["status"] == "STOP_REVIEW_REQUIRED" else 0)


if __name__ == "__main__":
    main()
