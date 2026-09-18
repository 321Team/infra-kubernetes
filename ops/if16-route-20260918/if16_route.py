#!/usr/bin/env python3
"""Review candidate: local, additive IF-16 route repair; default is read-only plan."""
import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import uuid

HOSTS = {"edge01": "10.1.93.50", "harbor01": "10.1.93.54", "db01": "10.1.93.55"}
DEVICES = {"edge01": "ens160", "harbor01": "ens160", "db01": "ens192"}
DEST, VIA = "10.250.10.0/24", "10.1.93.58"
NETWORK = ipaddress.ip_network(DEST)
PROPERTIES = ("connection.uuid", "connection.type", "connection.interface-name",
              "connection.autoconnect", "ipv4.method", "ipv4.addresses", "ipv4.gateway",
              "ipv4.dns", "ipv4.dns-search", "ipv4.route-table", "ipv4.route-metric",
              "ipv4.routing-rules", "ipv4.never-default", "ipv4.ignore-auto-routes")
EMPTY = ("", "--")

class Stop(Exception):
    pass

def require(ok, reason):
    if not ok:
        raise Stop(reason)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

class Commands:
    def __call__(self, *args):
        require(args[0] in ("ip", "nmcli"), "Command outside fixed allowlist")
        program = "/usr/sbin/ip" if args[0] == "ip" else "/usr/bin/nmcli"
        try:
            result = subprocess.run([program, *args[1:]], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                timeout=20, env={"PATH": "/usr/sbin:/usr/bin", "LC_ALL": "C", "LANG": "C"})
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise Stop("Command completion unknown: " + type(exc).__name__) from exc
        require(len(result.stdout) <= 1_000_000, "Command output too large")
        # Raw stderr may contain unrelated system values; only status is recorded.
        require(result.returncode == 0, "Command failed rc=" + str(result.returncode) + ": " + args[0])
        return result.stdout.strip()

def parse_routes(raw):
    """Parse nmcli -g plain routes, matching upstream non-PRETTY serialization."""
    if raw in EMPTY:
        return []
    require(not any(c in raw for c in "{}\n\r"), "Unrecognized ipv4.routes format; no change")
    attributes = {"advmss", "cwnd", "initcwnd", "initrwnd", "lock-advmss", "lock-cwnd",
                  "lock-initcwnd", "lock-initrwnd", "lock-mtu", "lock-window", "mtu", "onlink",
                  "quickack", "rto_min", "scope", "src", "table", "tos", "type", "weight", "window"}
    routes = []
    for part in raw.split(","):
        words = part.split()
        require(bool(words), "Empty NM route")
        item = {"ip": str(ipaddress.IPv4Network(words.pop(0), strict=True))}
        if words and "." in words[0] and "=" not in words[0]:
            item["nh"] = str(ipaddress.IPv4Address(words.pop(0)))
        if words and "=" not in words[0]:
            require(re.fullmatch(r"\d+", words[0]) is not None, "Unknown route metric")
            item["mt"] = words.pop(0)
        for field in words:
            pair = field.split("=", 1)
            require(len(pair) == 2 and pair[0] in attributes and pair[0] not in item
                    and bool(pair[1]), "Unknown or ambiguous NM route attribute")
            item[pair[0]] = pair[1]
        routes.append(item)
    return sorted(routes, key=canonical)

def collect(run, hostname=None):
    host = hostname or socket.gethostname().split(".")[0]
    require(host in HOSTS, "Host outside edge01/harbor01/db01")
    device = DEVICES[host]
    addresses = json.loads(run("ip", "-j", "-4", "address", "show", "dev", device))
    require(len(addresses) == 1, "Expected one interface")
    info = addresses[0]
    require(info.get("ifname") == device, "Unexpected interface in address query")
    pairs = sorted((a.get("local"), a.get("prefixlen")) for a in info.get("addr_info", []))
    require(pairs == [(HOSTS[host], 24)] and "UP" in info.get("flags", []),
            "Expected single host IPv4 /24 on UP " + device)
    managed = run("nmcli", "-g", "GENERAL.NM-MANAGED", "device", "show", device)
    state = run("nmcli", "-g", "GENERAL.STATE", "device", "show", device)
    require(managed == "yes" and state.startswith("100 "), "Interface not NM-managed/connected")
    active = run("nmcli", "-t", "-f", "UUID,DEVICE", "connection", "show", "--active")
    matches = [line.split(":")[0] for line in active.splitlines() if line.endswith(":" + device)]
    require(len(matches) == 1, "Ambiguous active profile")
    profile = str(uuid.UUID(matches[0]))
    props = {p: run("nmcli", "-g", p, "connection", "show", "uuid", profile) for p in PROPERTIES}
    require(props["connection.uuid"] == profile, "UUID mismatch")
    require(props["connection.type"] == "802-3-ethernet" and props["connection.interface-name"] == device,
            "Profile must explicitly bind ethernet " + device)
    require(props["ipv4.method"] == "manual" and props["ipv4.addresses"] == HOSTS[host] + "/24",
            "Static profile identity mismatch")
    require(props["connection.autoconnect"] == "yes", "Profile does not autoconnect")
    require(props["ipv4.route-table"] in ("0", "254") and props["ipv4.routing-rules"] in EMPTY,
            "Non-main profile table or routing rules require review")
    all_ids = run("nmcli", "-g", "UUID", "connection", "show").splitlines()
    bindings = {}
    for identity in all_ids:
        identity = str(uuid.UUID(identity))
        typ = run("nmcli", "-g", "connection.type", "connection", "show", "uuid", identity)
        if typ != "802-3-ethernet":
            continue
        interface = run("nmcli", "-g", "connection.interface-name", "connection", "show", "uuid", identity)
        require(interface not in EMPTY or identity == profile, "Unbound ethernet profile is ambiguous")
        bindings[identity] = interface
    require([key for key, value in bindings.items() if value == device] == [profile],
            "Multiple profiles bind " + device)
    kernel = json.loads(run("ip", "-j", "-4", "route", "show", "table", "all"))
    rules = json.loads(run("ip", "-j", "-4", "rule", "show"))
    expected_rules = [(0, "all", "local"), (32766, "all", "main"), (32767, "all", "default")]
    require([(r.get("priority"), r.get("src"), r.get("table")) for r in rules] == expected_rules,
            "Non-default routing rules require review")
    persistent = parse_routes(run("nmcli", "-g", "ipv4.routes", "connection", "show", "uuid", profile))
    return {"host": host, "ip": HOSTS[host], "device": device, "addresses": pairs,
            "profile": profile, "properties": props, "bindings": bindings,
            "kernel": sorted(kernel, key=canonical), "persistent": persistent, "rules": rules}

def target_routes(state):
    host = state.get("host")
    require(host in HOSTS and state.get("ip") == HOSTS[host]
            and state.get("device") == DEVICES[host], "Host/IP/device binding mismatch")
    device = DEVICES[host]
    runtime, saved = [], []
    for route in state["kernel"]:
        dest = route.get("dst", "default")
        if dest == "default":
            continue
        net = ipaddress.ip_network(dest, strict=False)
        if not net.overlaps(NETWORK):
            continue
        require(dest == DEST, "Overlapping runtime route requires review")
        require(route.get("gateway") == VIA and route.get("dev") == device
                and route.get("table", "main") in ("main", 254)
                and route.get("type", "unicast") == "unicast"
                and not route.get("nexthops") and not route.get("flags")
                and route.get("protocol") == "static"
                and set(route) <= {"dst", "gateway", "dev", "protocol", "scope", "metric", "flags", "table", "type", "prefsrc"},
                "Conflicting or advanced runtime route")
        runtime.append(route)
    for route in state["persistent"]:
        net = ipaddress.ip_network(route["ip"])
        if not net.overlaps(NETWORK) or net.prefixlen == 0:
            continue
        require(route["ip"] == DEST and route.get("nh") == VIA
                and set(route) <= {"ip", "nh", "mt", "table"}
                and route.get("table", "254") == "254", "Conflicting or advanced persistent route")
        require(route.get("table") == "254" or state["properties"].get("ipv4.route-table") == "254",
                "Existing implicit persistent table is ambiguous under profile table 0")
        saved.append(route)
    require(len(runtime) <= 1 and len(saved) <= 1, "Duplicate target route")
    return runtime, saved

def plan(state):
    runtime, saved = target_routes(state)
    if runtime:
        metric = int(runtime[0].get("metric", 0))
    elif saved:
        require("mt" in saved[0], "Existing implicit route metric requires review")
        metric = int(saved[0]["mt"])
    else:
        metric = 100
    require(0 <= metric <= 4294967295, "Metric out of range")
    if runtime and saved and "mt" in saved[0]:
        require(metric == int(saved[0]["mt"]), "Runtime/persistent metric mismatch")
    return {"schema": "fds.if16-route-plan.v1", "host": state["host"],
            "device": state["device"], "before_sha256": digest(state), "metric": metric,
            "add_runtime": not runtime, "add_persistent": not saved,
            "ac": "ICR-AC-13", "reboot_persistence": "NOT_TESTED",
            "packet_source_preservation": "NOT_TESTED", "whole_ac_pass": False}

def without_target(state):
    cleaned = copy.deepcopy(state)
    cleaned["kernel"] = [r for r in state["kernel"] if r.get("dst") != DEST]
    cleaned["persistent"] = [r for r in state["persistent"] if r.get("ip") != DEST]
    return cleaned

def expected_state(before, current, owned, metric):
    require(without_target(before) == without_target(current), "Unrelated state drift; no compensation")
    br, bp = target_routes(before)
    cr, cp = target_routes(current)
    for label, old, new in (("runtime", br, cr), ("persistent", bp, cp)):
        if not owned[label]:
            require(old == new, "Unowned target changed: " + label)
        else:
            require(not old and len(new) == 1, "Owned addition missing or changed: " + label)
            require(int(new[0].get("metric", 0) if label == "runtime" else new[0].get("mt", -1)) == metric,
                    "Owned metric changed: " + label)
            if label == "runtime":
                require(not new[0].get("prefsrc") and new[0].get("scope", "global") == "global",
                        "Owned runtime source/scope changed")
            else:
                require(new[0].get("table") == "254", "Owned persistent route table changed")

def runtime_args(action, metric, device):
    return ("ip", "-4", "route", action, DEST, "via", VIA, "dev", device,
            "proto", "static", "metric", str(metric), "table", "main")

def profile_args(action, profile, metric):
    return ("nmcli", "--wait", "15", "connection", "modify", "uuid", profile,
            action + "ipv4.routes", DEST + " " + VIA + " " + str(metric) + " table=254")

def apply(run, snapshot, write, probe):
    before = snapshot()
    proposed = plan(before)
    metric = proposed["metric"]
    owned = {"runtime": False, "persistent": False}
    record = {"schema": "fds.if16-route-receipt.v1", "before": before, "plan": proposed,
              "events": [], "status": "PREPARED", "recovery_complete": False}
    write(record)  # durable preimage precedes every mutation
    try:
        require(snapshot() == before, "State drift before first mutation")
        for kind in ("runtime", "persistent"):
            if not proposed["add_" + kind]:
                continue
            expected_state(before, snapshot(), owned, metric)
            record["events"].append({"intent": "add_" + kind})
            write(record)
            argv = runtime_args("add", metric, before["device"]) if kind == "runtime" else profile_args("+", before["profile"], metric)
            run(*argv)
            owned[kind] = True
            record["events"].append({"completed": "add_" + kind, "rc": 0})
            write(record)
            expected_state(before, snapshot(), owned, metric)
        current = snapshot()
        expected_state(before, current, owned, metric)
        route = probe()
        require(len(route) == 1 and route[0].get("gateway") == VIA and route[0].get("dev") == before["device"]
                and route[0].get("prefsrc", route[0].get("src")) == before["ip"], "Post route-get mismatch")
        record.update(status="APPLIED" if any(owned.values()) else "ALREADY_CORRECT",
                      after=current, route_get=route, mutation_confirmed=any(owned.values()),
                      persistent_profile_verified=True, reboot_persistence="NOT_TESTED",
                      packet_source_preservation="NOT_TESTED", whole_ac_pass=False)
        write(record)
        return record
    except (Exception, KeyboardInterrupt) as exc:
        record["error"] = str(exc) if isinstance(exc, Stop) else type(exc).__name__
        record["status"] = "RECOVERY_IN_PROGRESS"
        # If journal writing fails, still try conservative owned-only compensation.
        try:
            write(record)
        except OSError:
            pass
        try:
            for kind in ("persistent", "runtime"):
                if not owned[kind]:
                    continue
                expected_state(before, snapshot(), owned, metric)
                record["events"].append({"intent": "remove_own_" + kind})
                try:
                    write(record)
                except OSError:
                    record["journal_write_failed"] = True
                argv = profile_args("-", before["profile"], metric) if kind == "persistent" else runtime_args("del", metric, before["device"])
                run(*argv)
                owned[kind] = False
                record["events"].append({"completed": "remove_own_" + kind, "rc": 0})
                try:
                    write(record)
                except OSError:
                    record["journal_write_failed"] = True
            final = snapshot()
            require(final == before, "Preimage not restored; unknown/unowned change remains")
            record.update(status="FAILED_RESTORED", recovery_complete=True, after=final)
        except (Exception, KeyboardInterrupt) as recovery_error:
            record.update(status="PARTIAL_REVIEW_REQUIRED", recovery_complete=False,
                          recovery_error=str(recovery_error) if isinstance(recovery_error, Stop) else type(recovery_error).__name__)
        write(record)
        return record

def journal():
    root = Path("/var/lib/fds/if16-routes")
    # No user-controlled path and no symlink parents.
    for parent in (Path("/var/lib"), Path("/var/lib/fds"), root):
        if not parent.exists():
            parent.mkdir(mode=0o700)
        attrs = parent.lstat()
        require(stat.S_ISDIR(attrs.st_mode) and attrs.st_uid == 0 and not attrs.st_mode & 0o022,
                "Receipt parent not root-owned/private enough")
    filename = root / (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex + ".jsonl")
    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(directory)
    os.close(directory)
    def write(record):
        payload = (canonical(record) + "\n").encode()
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
    return filename, fd, write

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="explicit local add-only change after independent code review")
    args = parser.parse_args()
    run = Commands()
    if not args.apply:
        state = collect(run)
        print(json.dumps({"mode": "PLAN_ONLY", "mutation_attempted": False, "plan": plan(state), "before": state}, indent=2))
        return 0
    require(os.geteuid() == 0, "Apply requires root on the target host")
    # Advisory lock prevents concurrent copies of this tool; operators must avoid parallel NM changes.
    lock = os.open("/run/fds-if16-route.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    require(os.fstat(lock).st_uid == 0 and stat.S_ISREG(os.fstat(lock).st_mode), "Unsafe lock")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path, fd, write = journal()
    print(json.dumps({"receipt": str(path), "status": "RECEIPT_CREATED"}), flush=True)
    def interrupted(signum, frame):
        raise Stop("Interrupted by signal " + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        result = apply(run, lambda: collect(run), write,
                       lambda: json.loads(run("ip", "-j", "-4", "route", "get", "10.250.10.30")))
        print(json.dumps({"status": result["status"], "receipt": str(path),
                          "recovery_complete": result["recovery_complete"], "whole_ac_pass": False}))
        return 0 if result["status"] in ("APPLIED", "ALREADY_CORRECT") else 2
    finally:
        os.close(fd)
        os.close(lock)

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Stop, ValueError, OSError) as error:
        print(json.dumps({"status": "STOP", "error": str(error) if isinstance(error, Stop) else type(error).__name__,
                          "whole_ac_pass": False}))
        raise SystemExit(2)
