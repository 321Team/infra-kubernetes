#!/usr/bin/env bash
# Pure read-only pre-check; ICR #14 / K8S #10 / SEC #19 / MON #17.
# Run only via the existing ansible01 inventory on k8s-control01.
# Requires existing svc_ansible SSH, sudo -n kubectl, python3.
# Four API GET requests; no remote file writes, installs or restarts.
# The private-key field below is a file PATH, never private-key content.
# Failure is nonzero; partial output remains useful. No automatic remediation.
# RC=0 means collection succeeded, not TLS readiness or Runtime acceptance.
# No CA trust, signing-key availability, Prometheus loaded configuration,
# CSR issuance/approval or TLS/RBAC acceptance is proved by this collector.
test "$(hostname -s)" = k8s-control01 || exit 1
python3 - << "PY"
import datetime, json, re, subprocess, sys
base = ["sudo", "-n", "kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s"]
failed = 0
def get(path):
    p = subprocess.run(base + ["get", "--raw", path], capture_output=True, text=True, timeout=22)
    if p.returncode:
        kind = next((x for x in ("Forbidden", "Unauthorized", "NotFound", "timeout", "connection refused") if x.lower() in p.stderr.lower()), "COMMAND_FAILED")
        raise RuntimeError("kubectl rc=%s %s" % (p.returncode, kind))
    return json.loads(p.stdout)
def check(name, fn):
    global failed
    print("CHECK=" + name, flush=True)
    try:
        print(json.dumps(fn(), ensure_ascii=False, indent=2))
        print("RC=0", flush=True)
    except Exception as e:
        failed += 1
        msg = str(e) if isinstance(e, RuntimeError) else type(e).__name__
        print("RC=1 ERROR=" + msg, flush=True)
def node():
    x = get("/api/v1/nodes/k8s-worker03")
    a = x["status"]["addresses"]
    if x["metadata"]["name"] != "k8s-worker03" or not any(v["type"] == "InternalIP" and v["address"] == "10.1.93.108" for v in a):
        raise RuntimeError("NODE_IDENTITY_MISMATCH")
    return {"name": x["metadata"]["name"], "addresses": a}
def effective():
    c = get("/api/v1/nodes/k8s-worker03/proxy/configz")["kubeletconfig"]
    keys = ("serverTLSBootstrap", "rotateCertificates", "tlsCertFile", "tlsPrivateKeyFile", "authentication", "authorization", "featureGates")
    return {k: c.get(k, "NOT_PRESENT") for k in keys}
def source_config():
    x = get("/api/v1/namespaces/kube-system/configmaps/kubelet-config")
    s = x["data"]["kubelet"]
    keys = ("serverTLSBootstrap", "rotateCertificates", "tlsCertFile", "tlsPrivateKeyFile")
    return {k: re.findall(r"^" + k + r":[ \t]*(.*)$", s, re.M) or ["NOT_EXPLICIT"] for k in keys}
def signers():
    items = get("/api/v1/namespaces/kube-system/pods?labelSelector=component%3Dkube-controller-manager")["items"]
    expected = {"k8s-control01", "k8s-control02", "k8s-control03"}
    if len(items) != 3 or {p["spec"]["nodeName"] for p in items} != expected:
        raise RuntimeError("CONTROLLER_SET_MISMATCH")
    allowed = ("--controllers", "--cluster-signing-cert-file", "--cluster-signing-key-file", "--cluster-signing-kubelet-serving-cert-file", "--cluster-signing-kubelet-serving-key-file", "--cluster-signing-duration", "--feature-gates")
    out = []
    for p in items:
        c = next(c for c in p["spec"]["containers"] if c["name"] == "kube-controller-manager")
        args = c.get("command", []) + c.get("args", [])
        flags = {}
        for i, a in enumerate(args):
            key, sep, val = a.partition("=")
            if key in allowed:
                flags.setdefault(key, []).append(val if sep else (args[i+1] if i+1 < len(args) and not args[i+1].startswith("--") else "VALUE_MISSING"))
        out.append({"pod": p["metadata"]["name"], "node": p["spec"]["nodeName"], "flags": flags})
    return out
print(datetime.datetime.now().astimezone().isoformat())
check("WORKER03_IDENTITY", node)
if failed:
    sys.exit(1)
check("WORKER03_EFFECTIVE_KUBELET", effective)
check("KUBEADM_KUBELET_CONFIG", source_config)
check("CONTROLLER_SIGNING_FLAGS", signers)
print("TLS_PRECHECK_FAILED=%s" % failed)
sys.exit(1 if failed else 0)
PY
