#!/usr/bin/env python3
"""Candidate CP1 Runtime ownership handoff; never executes Join or recovery.

GitHub approval is authenticated by the calling controller. The root helper
validates the caller's explicit attestation and bindings; JSON is not an
independent identity or signature. Existing frozen job files stay unchanged.
"""
import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


JOB = "c564ae97530dfe80"
OLD_BUNDLE = "d229b8ba40716ffb4baeacf9ea80a009dc28c8155a9d38f4f84961fa4da28a41"
OLD_CHANGE = "27c75c6e9f606800a2cc49ee271ea3d8a91a2dffd1550ed81286e57e9e824585"
OLD_HANDOFF = "c3cf97941c147146183b5c5342d98ada706611725f12a899f417d08f5bf51417"
OLD_DECISION = "22a7cc8a41a43eaf3bdd56a5833918c7a16d8417ce56913e0aa34785932002c8"
OLD_WITHDRAW = "2026-09-18T15:15:00+09:00"
FILE_PINS = {
    "cp1_minimal_change.py": "840fb71dcdab33f91393eb48fd1f400b94d7f1f4a311c23407e2c7da018fa3f6",
    "run-cp1-minimal-change.py": "c1f5cd7730847f15b72f585827671fab95d63ac4b24e19effdc7147a5577c7ea",
    "node-firewall.py": "93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa",
    "run-node-firewall.py": "cb49cc6403b74c20f10d948c582da7b606209b1201b0eba740eb673ba1e7adb3",
    "analyze-cp1-cni-trial.py": "8d54ffd3358124bc513a0a0220eea5babb954786a4e8f747f1d5aea152fbd114",
}
SCOPE = {
    "join": True,
    "cp1_runtime_ownership_transfer": True,
    "success": "RETAIN_JOINED_CLUSTER_AND_RUNTIME",
    "failure": "PHASE_OWNED_RECOVERY_ONLY",
    "deadline": "NO_AUTOMATIC_WITHDRAWAL",
}
BASE = Path("/var/lib/fds/cp1-minimal")
TRANSFER_BASE = Path("/var/lib/fds/cp-join-handoff")
HEX64 = r"[0-9a-f]{64}"


def need(value, message):
    if not value:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2,
                       allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def instant(value):
    stamp = dt.datetime.fromisoformat(value)
    need(stamp.utcoffset() is not None, "timezone required")
    return stamp


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def validate_authority(authority, clock=None):
    """Validate controller attestation, never claim to authenticate its author."""
    clock = clock or utcnow()
    need(isinstance(authority, dict), "controller verified review absent")
    need(authority.get("schema") == "fds.cp-join-controller-verified-review.v1"
         and authority.get("repository") == "321Team/infra-kubernetes"
         and authority.get("reviewer") == "hayeounglee"
         and authority.get("decision") == "APPROVED"
         and authority.get("verified_by") == "controller-live-github-api",
         "explicit combined review attestation required")
    source = authority.get("source_commit", "")
    need(re.fullmatch(r"[0-9a-f]{40}", source)
         and authority.get("reviewed_commit") == source,
         "review is not for the executed source")
    contract = authority.get("contract_sha256", "")
    need(re.fullmatch(HEX64, contract)
         and authority.get("expected_contract_sha256") == contract,
         "combined contract binding differs")
    need(authority.get("scope") == SCOPE, "Join ownership/end scope differs")
    need(re.fullmatch(r"https://github.com/321Team/infra-kubernetes/pull/[1-9][0-9]*"
                      r"#(?:pullrequestreview|issuecomment)-[1-9][0-9]*",
                      authority.get("review_url", "")), "exact review URL required")
    reviewed = instant(authority.get("reviewed_at", ""))
    checked = instant(authority.get("verified_at", ""))
    need(reviewed <= checked <= clock and 0 <= (clock - checked).total_seconds() <= 900,
         "controller review verification is missing, future or stale")
    return authority


def validate_request(request):
    need(isinstance(request, dict), "JSON object required")
    need(request.get("mode") in ("inspect", "transfer", "status"), "unsupported mode")
    need(request.get("job_id") == JOB, "foreign legacy job")
    pins = {"expected_change_sha256": OLD_CHANGE, "handoff_sha256": OLD_HANDOFF,
            "session_decision_sha256": OLD_DECISION}
    need(all(request.get(key) == value for key, value in pins.items()),
         "legacy evidence binding differs")


def trusted(path, directory=False):
    path = Path(path)
    for parent in reversed(path.parents):
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
             and not info.st_mode & 0o022, "untrusted parent: " + str(parent))
    info = path.lstat()
    need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
         and info.st_uid == 0 and not info.st_mode & 0o022,
         "untrusted root state: " + str(path))
    return path


class RootBackend:
    def __init__(self):
        self.directory = BASE / JOB
        self.destination = TRANSFER_BASE / JOB
        self.unit = "fds-cp1-minimal-" + JOB
        self.legacy = self.io = self.record = None

    @contextlib.contextmanager
    def locked(self):
        need(os.geteuid() == 0, "sudo root helper required")
        lock = trusted(BASE / "LOCK")
        fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
                 "unsafe legacy lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def load(self):
        trusted(self.directory, directory=True)
        actual = {}
        for name, expected in FILE_PINS.items():
            path = trusted(self.directory / "scripts" / name)
            actual[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            need(actual[name] == expected, "frozen source differs: " + name)
        need(digest(actual) == OLD_BUNDLE, "legacy source bundle differs")
        saved = json.loads(trusted(self.directory / "SOURCE.json").read_bytes())
        need(saved == actual, "legacy SOURCE.json differs")
        # The pinned CLI imports its module using this temporary fixed directory.
        path = str(self.directory / "scripts")
        sys.path.insert(0, path)
        try:
            spec = importlib.util.spec_from_file_location("join_frozen_cp1_cli", Path(path) / "run-cp1-minimal-change.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(path)
        self.legacy = module
        self.record = json.loads(trusted(self.directory / "CHANGE.json").read_bytes())
        handoff = json.loads(trusted(self.directory / "HANDOFF.json").read_bytes())
        decision = json.loads(trusted(self.directory / "SESSION_DECISION.json").read_bytes())
        need(digest(self.record) == OLD_CHANGE and digest(handoff) == OLD_HANDOFF
             and digest(decision) == OLD_DECISION, "frozen job/evidence changed")
        need(self.record.get("job_id") == JOB
             and self.record.get("schema") == "fds.cp1-minimal-change.v4"
             and self.record.get("source_bundle_sha256") == OLD_BUNDLE
             and self.record.get("status") == "ACTIVE_REVIEW_REQUIRED"
             and self.record.get("withdraw_at") == OLD_WITHDRAW,
             "legacy job identity/state/window differs")
        self.io = module.IO(JOB, self.directory)
        need(module.load_session(self.io, self.record) is not None,
             "original delegated session proof missing")

    def clock(self):
        return utcnow()

    def before(self):
        result = self.legacy.session_status(self.io, self.record)
        need(result.get("status") == "CP_SESSION_ACTIVE_REVIEW_PENDING"
             and result.get("recovery_armed") is True,
             "legacy session is not active and armed")
        return digest(result["current"])

    def retained_state(self):
        view = self.io.snapshot()
        need(self.legacy.m.validate_view(view, self.record["before"])
             and view == self.record["after"], "CP1 Runtime/protection state changed")
        return digest(view)

    def command(self, argv):
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=20,
                                   env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
        need(completed.returncode == 0 and not completed.stderr.strip(),
             "systemd query/action failed: " + " ".join(argv))
        return completed.stdout.strip()

    def units(self):
        fields = "LoadState,ActiveState,SubState,Job"
        data = {}
        for kind in ("timer", "service"):
            text = self.command(["systemctl", "show", self.unit + "." + kind,
                                 "--property=" + fields])
            data[kind] = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        jobs = self.command(["systemctl", "list-jobs", "--no-legend", "--no-pager"])
        data["queued"] = any(self.unit + "." + kind in line.split()
                             for line in jobs.splitlines() for kind in ("service", "timer"))
        if data["service"].get("LoadState") == "loaded":
            exec_start = self.command(["systemctl", "show", self.unit + ".service",
                                       "--property=ExecStart", "--value"])
            expected = "/usr/bin/python3 -I -B " + str(self.directory / "scripts/run-cp1-minimal-change.py") + " recover --job " + JOB
            need("argv[]=" + expected + " ;" in exec_start, "old recovery unit command differs")
        return data

    def stop_timer(self):
        self.command(["systemctl", "stop", self.unit + ".timer"])

    def read(self, name):
        path = self.destination / name
        try:
            trusted(path)
        except FileNotFoundError:
            return None
        need(not path.stat().st_mode & 0o077, "handoff document is not private")
        return json.loads(path.read_bytes())

    def write_once(self, name, value):
        trusted(BASE.parent, directory=True)
        for path in (TRANSFER_BASE, self.destination):
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
            trusted(path, directory=True)
            need(not path.stat().st_mode & 0o077, "handoff directory is not private")
        target = self.destination / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(encoded(value)); stream.flush(); os.fsync(fd)
        finally:
            os.close(fd)
        parent = os.open(self.destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)


def idle_units(data, armed):
    service, timer = data["service"], data["timer"]
    need(not data.get("queued") and service.get("Job", "") in ("", "0")
         and timer.get("Job", "") in ("", "0"), "recovery unit has a queued job")
    loads = ("loaded",) if armed else ("loaded", "not-found")
    need(service.get("LoadState") in loads and service.get("ActiveState") == "inactive"
         and service.get("SubState") == "dead", "recovery service is not inactive")
    need(timer.get("LoadState") in loads
         and timer.get("ActiveState") == ("active" if armed else "inactive"),
         "original timer state differs")


def validate_committed(receipt, intent, clock):
    need(isinstance(intent, dict), "committed handoff has no intact intent")
    expected = dict(intent, schema="fds.cp1-join-handoff-receipt.v1",
                    status="RUNTIME_OWNERSHIP_TRANSFERRED", intent_sha256=digest(intent),
                    committed_at=receipt.get("committed_at"), old_change_edited=False,
                    legacy_timer_active=False, joined_nodes_changed=False, cp1_accepted=False)
    need(receipt == expected, "committed handoff/intent differs")
    created, committed = instant(intent["created_at"]), instant(receipt["committed_at"])
    need(created <= committed <= clock and created < instant(OLD_WITHDRAW),
         "committed handoff timestamp differs")
    validate_authority(intent.get("authority"), created)


def receipt_result(receipt, state_sha256):
    need(receipt.get("schema") == "fds.cp1-join-handoff-receipt.v1"
         and receipt.get("status") == "RUNTIME_OWNERSHIP_TRANSFERRED"
         and receipt.get("job_id") == JOB
         and receipt.get("legacy_change_sha256") == OLD_CHANGE
         and receipt.get("legacy_source_bundle_sha256") == OLD_BUNDLE
         and receipt.get("runtime_sha256") == state_sha256
         and receipt.get("scope") == SCOPE,
         "handoff receipt/current state differs")
    return {"status": "CP1_JOIN_RUNTIME_RETAINED", "join_may_start": True,
            "job_id": JOB, "handoff_receipt_sha256": digest(receipt),
            "runtime_sha256": state_sha256, "legacy_timer_active": False,
            "cp1_accepted": False, "independent_result_acceptance": "PENDING",
            "runtime_persistence": "NOT_ESTABLISHED", "scope": SCOPE}


def run(request, backend=None):
    backend = backend or RootBackend()
    action_attempted = False
    try:
        validate_request(request)
        with backend.locked():
            backend.load()
            receipt = backend.read("TRANSFER_COMMITTED.json")
            if receipt is not None:
                validate_committed(receipt, backend.read("TRANSFER_INTENT.json"), backend.clock())
                if request["mode"] == "transfer":
                    authority = validate_authority(request.get("authority"), backend.clock())
                    # Re-verification may have a newer checked timestamp; approved
                    # source, contract and actual review identity must stay exact.
                    keys = ("source_commit", "reviewed_commit", "contract_sha256", "review_url", "reviewer", "scope")
                    need(all(authority.get(key) == receipt["authority"].get(key) for key in keys),
                         "handoff is already owned by another reviewed operation")
                idle_units(backend.units(), armed=False)
                return receipt_result(receipt, backend.retained_state())
            need(backend.read("TRANSFER_INTENT.json") is None,
                 "incomplete handoff intent requires recovery review; Join is blocked")
            if request["mode"] in ("inspect", "status"):
                runtime_sha = backend.before()
                idle_units(backend.units(), armed=True)
                return {"status": "CP1_HANDOFF_NOT_COMMITTED", "join_may_start": False,
                        "job_id": JOB, "runtime_sha256": runtime_sha,
                        "legacy_withdraw_at": OLD_WITHDRAW, "legacy_timer_active": True,
                        "timer_mutation_attempted": False}
            authority = validate_authority(request.get("authority"), backend.clock())
            runtime_sha = backend.before()
            idle_units(backend.units(), armed=True)
            need(backend.clock() < instant(OLD_WITHDRAW), "legacy recovery deadline reached")
            intent = {"schema": "fds.cp1-join-handoff-intent.v1", "job_id": JOB,
                      "legacy_change_sha256": OLD_CHANGE, "legacy_handoff_sha256": OLD_HANDOFF,
                      "legacy_session_decision_sha256": OLD_DECISION,
                      "legacy_source_bundle_sha256": OLD_BUNDLE,
                      "legacy_withdraw_at": OLD_WITHDRAW, "runtime_sha256": runtime_sha,
                      "authority": authority, "scope": SCOPE,
                      "authority_validation": "CONTROLLER_ATTESTATION_NOT_LOCAL_AUTHENTICATION",
                      "created_at": backend.clock().isoformat()}
            backend.write_once("TRANSFER_INTENT.json", intent)
            # No destructive node action can start until COMMITTED exists.
            action_attempted = True
            backend.stop_timer()
            idle_units(backend.units(), armed=False)
            need(backend.retained_state() == runtime_sha, "state changed during handoff")
            receipt = dict(intent, schema="fds.cp1-join-handoff-receipt.v1",
                           status="RUNTIME_OWNERSHIP_TRANSFERRED",
                           intent_sha256=digest(intent), committed_at=backend.clock().isoformat(),
                           old_change_edited=False, legacy_timer_active=False,
                           joined_nodes_changed=False, cp1_accepted=False)
            backend.write_once("TRANSFER_COMMITTED.json", receipt)
            return receipt_result(receipt, runtime_sha)
    except Exception as error:
        return {"status": "CP1_HANDOFF_STOP_REVIEW_REQUIRED", "join_may_start": False,
                "job_id": JOB, "error": str(error),
                "timer_mutation_attempted": action_attempted,
                "note": "No Join or recovery executed. Preserve state and handoff records; do not retry an incomplete intent."}


def main(request=None):
    os.umask(0o077)
    if request is not None:
        return run(request)
    request = json.load(sys.stdin)
    result = run(request)
    print(encoded(result).decode(), end="")
    return 0 if result.get("status") in ("CP1_HANDOFF_NOT_COMMITTED", "CP1_JOIN_RUNTIME_RETAINED") else 2


if __name__ == "__main__":
    sys.exit(main())
