#!/usr/bin/env python3
"""Collect missing CP Join observations once. No configuration/Join mutations."""
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import pwd
import shlex
import subprocess
import sys
import tarfile
import tempfile
import types

NODE_PAYLOAD_B64 = "__NODE_PAYLOAD__"
CP1_ADAPTER_B64 = "__CP1_ADAPTER__"
APPROVED_HELPER_B64 = "__APPROVED_HELPER__"
APPROVED_POLICY_B64 = "__APPROVED_POLICY__"
KEY = Path("/home/server01_ansible/.ssh/p1_ansible_ed25519")
KNOWN_HOSTS = Path("/home/server01_ansible/.ssh/known_hosts")
SOURCE_IP = "10.250.10.10"
TARGETS = {"k8s-control01": "10.1.93.102", "k8s-control02": "10.1.93.103", "k8s-control03": "10.1.93.104"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def ssh_read(hostname, payload, timeout, remote_args=()):
    if hostname not in TARGETS:
        raise ValueError("unknown target")
    argv = ["ssh", "-F", "/dev/null", "-i", str(KEY), "-b", SOURCE_IP, "-T"]
    options = ["BatchMode=yes", "PreferredAuthentications=publickey", "PasswordAuthentication=no",
               "KbdInteractiveAuthentication=no", "StrictHostKeyChecking=yes",
               "UserKnownHostsFile=" + str(KNOWN_HOSTS), "IdentitiesOnly=yes", "IdentityAgent=none",
               "ControlMaster=no", "ControlPersist=no", "ControlPath=none", "ConnectTimeout=10",
               "ConnectionAttempts=1", "ServerAliveInterval=10", "ServerAliveCountMax=2",
               "UpdateHostKeys=no", "PermitLocalCommand=no", "ForwardAgent=no",
               "ClearAllForwardings=yes", "EscapeChar=none"]
    for option in options:
        argv.extend(["-o", option])
    argv.extend(["svc_ansible@" + TARGETS[hostname], shlex.join(
        ["sudo", "-n", "/usr/bin/python3", "-I", "-B", "-", *remote_args])])
    return subprocess.run(argv, input=payload, capture_output=True, text=True, timeout=timeout)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def load_adapter():
    module = types.ModuleType("cp1_reuse_adapter")
    exec(compile(base64.b64decode(CP1_ADAPTER_B64), "cp1_reuse_adapter.py", "exec"), module.__dict__)
    return module


def package(evidence):
    files = sorted(p for p in evidence.rglob("*") if p.is_file())
    (evidence / "SHA256SUMS").write_text("".join(
        digest(p.read_bytes()) + "  " + p.relative_to(evidence).as_posix() + "\n" for p in files))
    archive = evidence.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(evidence, arcname=evidence.name)
    sha = digest(archive.read_bytes())
    sidecar = Path(str(archive) + ".sha256")
    sidecar.write_text(sha + "  " + archive.name + "\n")
    print("ARCHIVE=" + str(archive), flush=True)
    print("SHA256=" + sha, flush=True)
    print("SIDECAR=" + str(sidecar), flush=True)


def main():
    if sys.argv[1:]:
        raise SystemExit("Usage: python3 -I -B READ_CP_PREJOIN.py")
    if pwd.getpwuid(os.getuid()).pw_name != "server01_ansible":
        raise SystemExit("Run as server01_ansible on ansible01; do not use controller sudo.")
    for p in (KEY, KNOWN_HOSTS):
        if not p.is_file():
            raise SystemExit("Required onsite SSH input missing: " + str(p))
    os.umask(0o077)
    started = dt.datetime.now(dt.timezone.utc)
    evidence = Path(tempfile.mkdtemp(prefix="ICR_CP_Prejoin_" + started.strftime("%Y%m%dT%H%M%SZ") + "_",
                                    dir="/home/server01_ansible"))
    source = Path(__file__).read_bytes()
    (evidence / "READ_CP_PREJOIN.py").write_bytes(source)
    state = {"schema": "fds.cp-prejoin-collection.v1", "started_at": started.isoformat(),
             "collector_sha256": digest(source), "configuration_mutation": False,
             "join_executed": False, "join_readiness": "NOT_ASSESSED",
             "prior_cp1_job": "c564ae97530dfe80", "nodes": {}}
    try:
        print("[1/3] CP1 topology and existing session status (read only)", flush=True)
        adapter = load_adapter()
        cp1 = adapter.read_cp1(base64.b64decode(APPROVED_HELPER_B64),
                               base64.b64decode(APPROVED_POLICY_B64),
                               lambda payload, timeout: ssh_read("k8s-control01", payload, timeout))
        write_json(evidence / "CP1_CURRENT.json", cp1)
        state["cp1_reads_complete"] = all(cp1.get(k, {}).get("ok") for k in ("topology", "status"))
        print("CP1_STATUS=" + cp1.get("status", {}).get("status", "INCOMPLETE"), flush=True)
        if cp1.get("status", {}).get("withdraw_at"):
            print("CP1_WITHDRAW_AT=" + cp1["status"]["withdraw_at"], flush=True)
        node_payload = base64.b64decode(NODE_PAYLOAD_B64).decode("utf-8")
        for index, name in enumerate(("k8s-control02", "k8s-control03"), 2):
            print("[" + str(index) + "/3] " + name + " manifests/services/listeners/LB (read only)", flush=True)
            try:
                r = ssh_read(name, node_payload, 180, (name, TARGETS[name]))
                if r.returncode != 0 or len(r.stdout) > 4 * 1024 * 1024:
                    data = {"status": "COLLECTION_FAILED", "returncode": r.returncode,
                            "stderr_sha256": digest(r.stderr.encode()), "stderr_bytes": len(r.stderr.encode())}
                else:
                    data = json.loads(r.stdout)
                    if (data.get("schema") != "fds.cp-prejoin-observation.v1" or
                            data.get("hostname") != name or data.get("ip") != TARGETS[name] or
                            data.get("configuration_mutation") is not False):
                        raise ValueError("unexpected target observation")
                write_json(evidence / (name + ".json"), data)
                collected = data.get("schema") == "fds.cp-prejoin-observation.v1"
                state["nodes"][name] = {"collected": collected, "join_readiness": "NOT_ASSESSED"}
                if collected:
                    print(name + "_MANIFEST_ENTRIES=" + str(len(data.get("manifests", {}).get("files", []))), flush=True)
                    print(name + "_LB_TCP=" + data.get("lb_tcp", {}).get("connect", "UNKNOWN"), flush=True)
            except Exception as exc:
                state["nodes"][name] = {"collected": False, "exception_type": type(exc).__name__}
                write_json(evidence / (name + ".json"), state["nodes"][name])
        complete = state.get("cp1_reads_complete") and all(
            state["nodes"].get(name, {}).get("collected") for name in ("k8s-control02", "k8s-control03"))
        state["status"] = "OBSERVATIONS_COLLECTED_REVIEW_REQUIRED" if complete else "PARTIAL_OBSERVATIONS"
    except KeyboardInterrupt:
        state["status"] = "PARTIAL_OBSERVATIONS_INTERRUPTED"
    except Exception as exc:
        state["status"] = "PARTIAL_OBSERVATIONS"
        state["exception_type"] = type(exc).__name__
    finally:
        state["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(evidence / "RUN_STATE.json", state)
        print("STATUS=" + state["status"], flush=True)
        print("CONFIGURATION_MUTATION=NOT_EXECUTED", flush=True)
        print("JOIN_READINESS=NOT_ASSESSED", flush=True)
        package(evidence)
    return 0 if state["status"] == "OBSERVATIONS_COLLECTED_REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    sys.exit(main())
