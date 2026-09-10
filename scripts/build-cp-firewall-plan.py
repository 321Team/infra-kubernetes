#!/usr/bin/env python3
"""Build a review-only CP firewall plan from a verified collection archive.

No remote access, subprocess execution, firewall writes, or approval mutation.
Generated commands are review data. Applying them requires the linked runbook.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import tarfile

TARGETS = {"k8s-control01": "10.1.93.102", "k8s-control02": "10.1.93.103",
           "k8s-control03": "10.1.93.104"}
SOURCES = [ip + "/32" for ip in TARGETS.values()]
PORTS = [{"port": "2379-2380", "protocol": "tcp", "contract": "IF-12A"},
         {"port": "10250", "protocol": "tcp", "contract": "IF-12B"}]
BASELINE = "2d93395a0e19ea67e5be199d7183a7edbaa6cf4c"
INVENTORY = "5cbf3b657014b32a20cb5816d90d3ea5fe4951ab"
REVIEWED_ARCHIVE = "265e7d3fe04c8c215b8c76847bac769c5445682d788c3f9ad2bf9afef100c594"
MAX_ARCHIVE = 10 * 1024 * 1024
MAX_CONTENT = 20 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def parse_json(data):
    return json.loads(data, object_pairs_hook=no_duplicates)


def validate_policy(policy):
    require(policy["schema"] == "fds.cp-firewall-policy.v1", "policy schema mismatch")
    require(policy["ticket"] == "321Team/fds-project-baseline#14", "ticket mismatch")
    require(policy["baseline_commit"] == BASELINE, "baseline mismatch")
    require(policy["inventory_commit"] == INVENTORY, "inventory source mismatch")
    require(policy["targets"] == TARGETS, "CP target expansion rejected")
    require(policy["sources"] == SOURCES, "only the three reviewed /32 sources are allowed")
    require(policy["ports"] == PORTS, "port or protocol expansion rejected")
    require(policy["zone"] == "public" and policy["interface"] == "ens160", "zone mismatch")
    require(policy["planes"] == ["permanent", "runtime"], "plane order mismatch")
    require(policy["evidence"]["sha256"] == REVIEWED_ARCHIVE, "unreviewed evidence rejected")


def read_archive(archive, sidecar, expected_hash):
    require(archive.stat().st_size <= MAX_ARCHIVE, "compressed archive too large")
    raw = archive.read_bytes()
    actual_hash = digest(raw)
    require(actual_hash == expected_hash, "archive differs from reviewed evidence")
    line = sidecar.read_text().strip()
    match = re.fullmatch(r"([0-9a-f]{64})\s+\*?(.+)", line)
    require(match and match[1] == actual_hash, "sidecar checksum mismatch")
    require(PurePosixPath(match[2]).name == archive.name, "sidecar filename mismatch")
    # Parse the same bytes that were hashed, avoiding a second-read race.
    import io
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as handle:
        members = handle.getmembers()
        require(len(members) <= 100, "too many archive members")
        require(sum(m.size for m in members) <= MAX_CONTENT, "expanded archive too large")
        names = set()
        files = {}
        for member in members:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts, "unsafe archive path")
            require(member.name not in names, "duplicate archive member")
            names.add(member.name)
            require(member.isdir() or member.isfile(), "archive links/special files rejected")
            if member.isfile():
                require(len(name.parts) == 2, "unexpected archive directory depth")
                files[str(name)] = handle.extractfile(member).read()
        manifests = [name for name in files if name.endswith("/SHA256SUMS")]
        require(len(manifests) == 1, "expected one internal checksum manifest")
        manifest = manifests[0]
        prefix = str(PurePosixPath(manifest).parent) + "/"
        verified = set()
        for line in files[manifest].decode().splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})\s+\*?([^/]+)", line)
            require(match and match[2] not in (".", ".."), "invalid internal checksum entry")
            name = prefix + match[2]
            require(name not in verified and name in files, "missing/duplicate checked file")
            require(digest(files[name]) == match[1], "internal checksum mismatch")
            verified.add(name)
        require(verified == set(files) - {manifest}, "incomplete checksum coverage")
        required = {"COLLECTION_SUMMARY.json", "controller_context.json", "collection_parameters.json",
                    "ansible_collection.log", "ansible_syntax_check.log", "SHA256SUMS"}
        required.update(name + ".json" for name in TARGETS)
        require(set(files) == {prefix + name for name in required}, "unexpected evidence file set")
        return {name[len(prefix):]: value for name, value in files.items()}


def zone_fields(output):
    lines = output.splitlines()
    require(lines and lines[0].split()[0] == "public", "unexpected zone output")
    fields, rules, in_rules = {}, [], False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "rich rules:":
            in_rules = True
        elif in_rules:
            if stripped:
                require(stripped.startswith("rule "), "unexpected rich rule format")
                shlex.split(stripped)  # Reject malformed quoting; never execute it.
                rules.append(stripped)
        elif ":" in stripped:
            key, value = stripped.split(":", 1)
            require(key not in fields, "duplicate zone field")
            fields[key] = value.strip()
    require(in_rules and "ssh" in fields.get("services", "").split(), "management SSH not preserved")
    require(fields.get("target") == "default" and not fields.get("sources"), "zone routing changed")
    return fields, rules


def wanted_rules():
    return [(port["contract"], f'rule family="ipv4" source address="{source}" '
             f'port port="{port["port"]}" protocol="tcp" accept')
            for source in SOURCES for port in PORTS]


def operations(existing, plane):
    existing_tokens = {tuple(shlex.split(rule)) for rule in existing}
    prefix = ["firewall-cmd"] + (["--permanent"] if plane == "permanent" else []) + ["--zone=public"]
    result = []
    for contract, rule in wanted_rules():
        present = tuple(shlex.split(rule)) in existing_tokens
        result.append({"contract": contract, "plane": plane, "rule": rule,
                       "present_in_evidence": present,
                       "action": "preserve" if present else "candidate_add",
                       "query_argv": prefix + ["--query-rich-rule=" + rule],
                       "add_argv": None if present else prefix + ["--add-rich-rule=" + rule],
                       "rollback_argv": None if present else prefix + ["--remove-rich-rule=" + rule],
                       "rollback_requires": "successful add receipt, absent before change, protected service state"})
    return result


def build_plan(policy, files):
    validate_policy(policy)
    summary = parse_json(files["COLLECTION_SUMMARY.json"])
    require(summary["status"] == "COLLECTED_REVIEW_REQUIRED" and summary["ansible_returncode"] == 0,
            "collection did not succeed")
    require(summary["collected_nodes"] == summary["expected_nodes"] == 3, "incomplete CP collection")
    require(set(summary["nodes"]) == set(TARGETS), "summary target mismatch")
    require(summary["repo_head"] == INVENTORY and summary["kit_version"] == "1.1", "collector source mismatch")
    require(summary["ended_at"] == policy["evidence"]["ended_at"], "collection time mismatch")
    nodes = {}
    for name, ip in TARGETS.items():
        node = parse_json(files[name + ".json"])
        require(summary["nodes"][name]["status"] == node["status"] == "COLLECTED_REVIEW_REQUIRED",
                "node collection failure")
        require(node["transport_rc"] == 0 and node["identity"]["hostname"] == name and
                node["identity"]["expected_ip"] == ip and node["identity"]["uid"] == 0, "identity mismatch")
        for stage in ("ssh", "module", "become"):
            result = node["preflight"][stage]
            require(result.get("rc") == 0 and not any(result.get(k) for k in ("failed", "unreachable", "skipped")),
                    "preflight failure")
        require(node["preflight"]["ssh"]["stdout"].strip() == "svc_ansible" and
                node["preflight"]["become"]["stdout"].strip() == "0", "access identity mismatch")
        queries = node["queries"]
        needed = ["unit_state", "firewall_active_zones", "firewall_public_runtime", "firewall_public_permanent"]
        for key in needed:
            require(queries[key]["rc"] == 0 and not any(queries[key].get(k) for k in
                    ("stdout_truncated", "stderr_truncated")), "incomplete firewall query")
        units = [dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
                 for block in queries["unit_state"]["stdout"].strip().split("\n\n")]
        require(any(u.get("Id") == "firewalld.service" and u.get("ActiveState") == "active" and
                    u.get("SubState") == "running" for u in units), "firewalld is not active")
        active = queries["firewall_active_zones"]["stdout"].strip().splitlines()
        require(len(active) == 2 and active[0].split()[0] == "public" and
                active[1].strip() == "interfaces: ens160", "active zone/interface changed")
        by_plane = {}
        snapshot = {}
        for plane in policy["planes"]:
            output = queries["firewall_public_" + plane]["stdout"]
            fields, rules = zone_fields(output)
            require(fields.get("interfaces") == ("ens160" if plane == "runtime" else ""),
                    "interface binding changed; inspect NetworkManager")
            require(not fields.get("ports") and not fields.get("protocols"), "unexpected broad port/protocol allowance")
            snapshot[plane] = {"fields": fields, "rich_rules": rules}
            by_plane[plane] = operations(rules, plane)
        nodes[name] = {"ip": ip, "zone": "public", "interface": "ens160", "before": snapshot,
                       "before_sha256": digest(canonical(snapshot)), "operations": by_plane}
    count = sum(op["action"] == "candidate_add" for node in nodes.values()
                for ops in node["operations"].values() for op in ops)
    return {"schema": "fds.cp-firewall-plan.v1", "status": "DRAFT_PLAN_REVIEW_REQUIRED",
            "runtime_applied": False, "policy_sha256": digest(canonical(policy)),
            "evidence": policy["evidence"], "baseline_commit": BASELINE, "inventory_commit": INVENTORY,
            "execution_order": list(TARGETS), "candidate_add_count": count, "nodes": nodes,
            "limits": ["Offline evidence plan; live identity/zone/rules and NetworkManager must be checked before apply.",
                       "A generated rollback command is not proof that its matching add ran.",
                       "No /24, direct CP 6443, Worker, NTP, join/reset or HEP/firewalld handoff changes.",
                       "CP2 residual configuration and etcd membership remain under baseline issue #10."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        policy = parse_json(args.policy.read_bytes())
        validate_policy(policy)
        require(args.archive.name == policy["evidence"]["archive"], "archive name mismatch")
        files = read_archive(args.archive, args.sidecar, policy["evidence"]["sha256"])
        plan = build_plan(policy, files)
        encoded = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode()
        with args.out.open("xb") as handle:
            handle.write(encoded)
        print("STATUS=DRAFT_PLAN_REVIEW_REQUIRED")
        print("CANDIDATE_ADDS=" + str(plan["candidate_add_count"]))
        print("PLAN_SHA256=" + digest(encoded))
        print("RUNTIME_APPLIED=false")
    except (ValueError, KeyError, TypeError, OSError, tarfile.TarError) as error:
        parser.exit(2, "STOP: " + str(error) + "\n")


if __name__ == "__main__":
    main()
