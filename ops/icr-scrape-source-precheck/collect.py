#!/usr/bin/env python3
"""Bounded observation of existing scrape traffic. No traffic generation or writes."""
import concurrent.futures
import datetime
import getpass
import hashlib
import json
import os
import pathlib
import re
import socket
import stat
import subprocess
import sys

EXPECTED = {"k8s-worker01": "10.1.93.105", "k8s-worker03": "10.1.93.108"}
POD_NAME = "prometheus-58c848c688-kzvhp"
POD_UID = "46c00f91-2073-440a-800c-6cedba89d291"
POD_IP = "10.244.39.219"
IMAGE_ID = "10.1.93.54/fds/prometheus@sha256:b77f9cee82fd1c61521634d2d6b192fc526ffd938e904b8e6836b6c0cf8d875a"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "TZ": "Asia/Seoul"}
# Source is deliberately unrestricted on the destination: do not presuppose SNAT.
FILTER = "ip and tcp and host 10.1.93.105 and (port 9100 or port 10250) and (tcp[tcpflags] & (tcp-syn|tcp-rst) != 0)"


def now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat()


def tool(name, optional=False):
    for directory in ("/usr/sbin", "/usr/bin", "/sbin", "/bin", "/usr/local/bin"):
        path = pathlib.Path(directory, name)
        if path.exists():
            resolved = path.resolve()
            info = resolved.stat()
            if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
                raise ValueError("untrusted executable: " + str(path))
            if not os.access(resolved, os.X_OK):
                raise ValueError("non-executable: " + str(path))
            return str(resolved)
    if optional:
        return None
    raise ValueError("missing existing tool: " + name)


def run(argv, timeout=12):
    start = now()
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, env=ENV, check=False)
        out, err, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as exc:
        out, err, rc = exc.stdout or b"", exc.stderr or b"", 125
    record = {"command": argv, "started_kst": start, "finished_kst": now(), "rc": rc,
              "stdout_sha256": hashlib.sha256(out).hexdigest()}
    if len(out) + len(err) > 4 * 1024 * 1024:
        raise ValueError("output exceeds 4 MiB bound")
    record.update(stdout=out.decode("utf-8", "replace"), stderr=err.decode("utf-8", "replace"))
    return record


def require(record):
    if record["rc"] != 0:
        raise ValueError("read failed: " + record["command"][-1])
    return record


def sender_fields(pod):
    md, sp, status = pod["metadata"], pod["spec"], pod["status"]
    image_ids = [c["imageID"] for c in status.get("containerStatuses", []) if c["name"] == "prometheus"]
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []))
    if not (md["name"] == POD_NAME and md["uid"] == POD_UID and md["namespace"] == "monitoring-api"
            and sp["nodeName"] == "k8s-worker03" and sp["serviceAccountName"] == "prometheus"
            and not sp.get("hostNetwork", False) and status["podIP"] == POD_IP
            and status["hostIP"] == EXPECTED["k8s-worker03"] and status["phase"] == "Running"
            and ready and image_ids == [IMAGE_ID] and not md.get("deletionTimestamp")):
        raise ValueError("sender changed or not Ready; collect new identity before observing")
    return {"name": md["name"], "uid": md["uid"], "node": sp["nodeName"], "ip": status["podIP"],
            "host_ip": status["hostIP"], "sa": sp["serviceAccountName"], "image_ids": image_ids, "ready": ready}


def interfaces_for(host, addresses, route=None):
    if not any(x.get("ifname") == "ens160" and "UP" in x.get("flags", [])
               and any(a.get("local") == EXPECTED[host] for a in x.get("addr_info", [])) for x in addresses):
        raise ValueError("ens160 identity or UP state mismatch")
    interfaces = ["ens160"]
    if host == "k8s-worker03":
        if not isinstance(route, list) or len(route) != 1:
            raise ValueError("ambiguous local sender route")
        dev = route[0].get("dev", "")
        if not re.fullmatch(r"cali[A-Za-z0-9]{1,11}", dev) or route[0].get("gateway"):
            raise ValueError("sender route is not a direct local Calico veth")
        interfaces.append(dev)
    return interfaces


def capture(sudo, timeout_bin, tcpdump, interface):
    # -p: no promiscuous mode. No -A/-X/-w: text headers only, no stored pcap/payload.
    r = run([sudo, "-n", timeout_bin, "--signal=INT", "--kill-after=2s", "35s", tcpdump,
             "-p", "-nn", "-tttt", "-l", "-s", "96", "-c", "64", "-i", interface, FILTER], timeout=43)
    r["interface"] = interface
    r["status"] = "WINDOW_ENDED" if r["rc"] == 124 else "COUNT_ENDED" if r["rc"] == 0 else "FAILED"
    return r


def main():
    result = {"schema": "icr-scrape-source-precheck/v1", "started_kst": now(),
              "host": socket.gethostname().split(".")[0], "account": getpass.getuser(),
              "collector_sha256": globals().get("SOURCE_SHA256", "LOCAL_UNRECORDED"),
              "phase": sys.argv[1] if len(sys.argv) == 2 else "INVALID",
              "reads": [], "captures": [], "runtime_ac": "NOT_ASSESSED", "result": "STOP"}
    try:
        host, phase = result["host"], result["phase"]
        if result["account"] != "svc_ansible":
            raise ValueError("unexpected account")
        sudo = tool("sudo")
        if phase in ("guard-before", "guard-after"):
            if host != "k8s-control01":
                raise ValueError("sender guard requires control01")
            cmd = [sudo, "-n", tool("kubectl"), "--kubeconfig=/etc/kubernetes/admin.conf",
                   "--server=https://10.1.93.109:6443", "--request-timeout=10s", "get",
                   "--raw=/api/v1/namespaces/monitoring-api/pods/" + POD_NAME]
            r = run(cmd, 15)
            raw = r.pop("stdout")
            result["reads"].append(r)  # Do not retain Pod env/args/annotations.
            require(r)
            pod = json.loads(raw)
            result["sender"] = sender_fields(pod)
        elif phase in ("prepare", "observe"):
            if host not in EXPECTED:
                raise ValueError("unexpected worker")
            ip, tcpdump, timeout_bin = tool("ip"), tool("tcpdump"), tool("timeout")
            a = run([ip, "-j", "-4", "address", "show", "dev", "ens160"])
            result["reads"].append(a)
            require(a)
            route = None
            if host == "k8s-worker03":
                r = run([ip, "-j", "-4", "route", "get", POD_IP])
                result["reads"].append(r)
                require(r)
                route = json.loads(r["stdout"])
            interfaces = interfaces_for(host, json.loads(a["stdout"]), route)
            result["interfaces"] = interfaces
            if phase == "prepare":
                commands = [[sudo, "-n", timeout_bin, "5s", tcpdump, "--version"],
                            [sudo, "-n", timeout_bin, "5s", tcpdump, "-p", "-i", "ens160", "-d", FILTER]]
                commands += [[ip, "-j", "link", "show", "dev", dev] for dev in interfaces]
                for command in commands:
                    r = run(command)
                    result["reads"].append(r)
                    require(r)
            else:
                # Existing listeners, host routes and kernel rules only. No counter reset.
                result["reads"].append(run([tool("ss"), "-lnt", "( sport = :9100 or sport = :10250 )"]))
                for address in (POD_IP, EXPECTED["k8s-worker01"], EXPECTED["k8s-worker03"]):
                    result["reads"].append(run([ip, "-4", "route", "get", address]))
                saver = tool("iptables-save", optional=True)
                result["iptables_save_available"] = bool(saver)
                if saver:
                    for table in ("filter", "nat"):
                        r = run([sudo, "-n", saver, "-c", "-t", table])
                        r["snapshot"] = "before"
                        result["reads"].append(r)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(capture, sudo, timeout_bin, tcpdump, dev) for dev in interfaces]
                    result["captures"] = [f.result() for f in futures]
                if saver:
                    for table in ("filter", "nat"):
                        r = run([sudo, "-n", saver, "-c", "-t", table])
                        r["snapshot"] = "after"
                        result["reads"].append(r)
                if any(c["status"] == "FAILED" for c in result["captures"]):
                    raise ValueError("capture failed; no installation or privilege changes allowed")
        else:
            raise ValueError("unknown phase")
        result["result"] = "COLLECTED_PENDING_ANALYSIS"
        result["read_failures"] = sum(r["rc"] != 0 for r in result["reads"])
        result["reason"] = "No packets is inconclusive; correlate both workers and sender guards. Not IF-06B evidence."
        rc = 0
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        result["reason"] = str(exc)
        rc = 2
    result["finished_kst"] = now()
    print(json.dumps(result, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
