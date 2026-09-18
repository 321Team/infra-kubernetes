"""Read only CP1 topology and frozen-job status; no command runs on import.

Caller supplies the approved SSH transport as ssh_run(payload, timeout), returning
subprocess.CompletedProcess-like returncode/stdout/stderr. Remote argv must be
sudo -n /usr/bin/python3 -I -B -. Failures are observations, not collection gates.
"""
import hashlib
import json

CP1_JOB = "c564ae97530dfe80"
HELPER_SHA256 = "ea3b15454120431f7b7120829068e846f8991e32d2cd2e06056f9a4ab7110cd2"
POLICY_SHA256 = "7624989f489d983e44a9754d5c9baeeaa665ab80cfed9c8c189791b9578b5021"
CP1_FILES = {
    "cp1_minimal_change.py": "840fb71dcdab33f91393eb48fd1f400b94d7f1f4a311c23407e2c7da018fa3f6",
    "run-cp1-minimal-change.py": "c1f5cd7730847f15b72f585827671fab95d63ac4b24e19effdc7147a5577c7ea",
    "node-firewall.py": "93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa",
    "run-node-firewall.py": "cb49cc6403b74c20f10d948c582da7b606209b1201b0eba740eb673ba1e7adb3",
    "analyze-cp1-cni-trial.py": "8d54ffd3358124bc513a0a0220eea5babb954786a4e8f747f1d5aea152fbd114",
}
_STATUS_BODY = r'''
import os,stat,json,hashlib,subprocess,sys,socket
from pathlib import Path
if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='k8s-control01':
 raise SystemExit('CP1 identity mismatch')
job=Path('/var/lib/fds/cp1-minimal')/JOB
for p in reversed([job]+list(job.parents)):
 s=p.lstat()
 if not(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022):
  raise SystemExit('untrusted root path')
# The existing status entry uses this lock. Require it to exist so this read
# cannot create a new persistent lock file. It never schedules recovery.
s=(job.parent/'LOCK').lstat()
if not(stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o077):
 raise SystemExit('existing status lock unavailable')
for name,digest in FILES.items():
 p=job/'scripts'/name
 for q in (p.parent,p):
  s=q.lstat()
  if s.st_uid!=0 or s.st_mode&0o022 or stat.S_ISLNK(s.st_mode):
   raise SystemExit('untrusted frozen source')
 if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
  raise SystemExit('frozen source differs')
r=subprocess.run(['/usr/bin/python3','-I','-B',str(job/'scripts/run-cp1-minimal-change.py'),
                  'status','--job',JOB],capture_output=True,text=True,timeout=180)
sys.stdout.write(r.stdout);sys.stderr.write(r.stderr);raise SystemExit(r.returncode)
'''


def _canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def _result(ssh_run, payload, timeout):
    """Never expose arbitrary stderr or raw diagnostic stdout on a failed read."""
    try:
        response = ssh_run(payload, timeout)
        if response.returncode != 0:
            return {"ok": False, "status": "INCOMPLETE", "reason": "REMOTE_READ_FAILED",
                    "returncode": response.returncode}
        if response.stderr.strip():
            return {"ok": False, "status": "INCOMPLETE", "reason": "REMOTE_STDERR_PRESENT"}
        raw = response.stdout
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("oversized output")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("invalid document")
        return {"ok": True, "data": data}
    except Exception as error:
        return {"ok": False, "status": "INCOMPLETE", "reason": "READ_OR_PARSE_FAILED",
                "exception_type": type(error).__name__}


def read_cp1(helper_bytes, policy_bytes, ssh_run):
    """Return observations independently; an unavailable topology does not skip status."""
    results = {}
    try:
        if hashlib.sha256(helper_bytes).hexdigest() != HELPER_SHA256:
            raise ValueError("helper digest differs")
        if hashlib.sha256(policy_bytes).hexdigest() != POLICY_SHA256:
            raise ValueError("policy digest differs")
        policy = json.loads(policy_bytes)
        if hashlib.sha256(_canonical(policy)).hexdigest() != POLICY_SHA256:
            raise ValueError("policy canonical digest differs")
        request = {"mode": "topology", "node": "k8s-control01", "policy": policy,
                   "policy_sha256": POLICY_SHA256}
        payload = "REQUEST = " + repr(request) + "\n" + helper_bytes.decode("utf-8")
        topology = _result(ssh_run, payload, 200)
        if topology.get("ok"):
            raw = topology.pop("data")
            stable = raw.get("stable")
            if (raw.get("status") != "TOPOLOGY_REVIEW_REQUIRED" or
                    raw.get("hostname") != "k8s-control01" or not isinstance(stable, dict) or
                    hashlib.sha256(_canonical(stable)).hexdigest() != raw.get("sha256")):
                topology = {"ok": False, "status": "INCOMPLETE", "reason": "TOPOLOGY_INVALID"}
            else:
                topology.update(status=raw["status"], observed_at=raw.get("created_at"),
                                topology=stable, sha256=raw["sha256"],
                                api_readyz_passed=True)
        results["topology"] = topology
    except Exception as error:
        results["topology"] = {"ok": False, "status": "INCOMPLETE", "reason": "SOURCE_INVALID",
                               "exception_type": type(error).__name__}
    payload = "JOB=" + repr(CP1_JOB) + "\nFILES=" + repr(CP1_FILES) + "\n" + _STATUS_BODY
    status = _result(ssh_run, payload, 200)
    if status.get("ok"):
        raw = status.pop("data")
        if raw.get("job_id") != CP1_JOB or not isinstance(raw.get("status"), str):
            status = {"ok": False, "status": "INCOMPLETE", "reason": "STATUS_IDENTITY_INVALID"}
        else:
            keys = ("status", "job_id", "source_bundle_sha256", "observed_at", "withdraw_at",
                    "recovery_armed", "cp1_accepted", "independent_acceptance",
                    "independent_review_status", "session_decision_sha256",
                    "handoff_sha256", "change_receipt_sha256")
            status.update({key: raw[key] for key in keys if key in raw})
    results["status"] = status
    return results
