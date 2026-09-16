#!/usr/bin/env python3
"""Offline CP1 transition consistency check. Never authorizes a node change.

Inputs contain human review and measurement assertions, not authenticated proof.
No SSH, firewall mutation, receipt rewriting, or live-state collection is provided.
The existing run-node-firewall.py gate remains unchanged and rejects CNI drift.
"""
import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = {
    "scripts/node-firewall.py": "93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa",
    "scripts/run-node-firewall.py": "cb49cc6403b74c20f10d948c582da7b606209b1201b0eba740eb673ba1e7adb3",
}
ZONE = "fds-cp1-cni"
POLICY = "fds-cp1-cni-local"
SCOPE = {"host": "k8s-control01", "source_cidr": "10.244.246.64/26",
         "destination_cidr": "10.244.246.64/26", "ingress": "cali+", "egress": "cali+",
         "layer4": "ALL", "same_node_only": True, "purpose": "PRE_JOIN_CANDIDATE"}
MAX_BYTES = 8 * 1024 * 1024


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(value):
    return sha((json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2,
                           allow_nan=False) + "\n").encode())


def exact(a, b):
    # bool and int compare equal in Python; their serialized types must not.
    return fingerprint(a) == fingerprint(b)


def digest(value):
    need(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "digest absent/invalid")


def instant(value):
    need(isinstance(value, str), "timestamp absent")
    result = dt.datetime.fromisoformat(value)
    need(result.utcoffset() is not None, "timestamp needs timezone")
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def load_checked(path, expected):
    digest(expected)
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    need(len(raw) <= MAX_BYTES, "input too large")
    need(sha(raw) == expected, "input bytes differ from supplied SHA256")
    def bad_constant(_):
        raise ValueError("non-finite JSON number")
    data = json.loads(raw, object_pairs_hook=unique_object, parse_constant=bad_constant)
    need(isinstance(data, dict), "JSON object required")
    return data


def original_validator():
    for path, expected in DEPENDENCIES.items():
        need(sha((ROOT / path).read_bytes()) == expected, "pinned original validator changed")
    spec = importlib.util.spec_from_file_location("cp1_original_controller", ROOT / "scripts/run-node-firewall.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_cp1_receipt


def validate_stable(state):
    need(isinstance(state, dict) and set(state) == {"firewall", "protection", "zone_inventory"},
         "full transition state fields required")
    fw = state["firewall"]
    need(fw.get("hostname") == "k8s-control01", "wrong state host")
    need(isinstance(state["protection"], dict) and bool(state["protection"]), "protection snapshot absent")
    zones = state["zone_inventory"]
    need(set(zones) == {"runtime", "permanent"}, "both zone inventories required")
    for plane in zones:
        need(isinstance(zones[plane], dict) and "public" in zones[plane], "public zone inventory absent")
        need(exact(zones[plane]["public"], fw["planes"][plane]), "public zone projections differ")


def expected_after(before, changes):
    """Replay only owned additions and the reviewed active-policy display change.

    This constrains the state delta, not nft semantics or zone compatibility.
    Those require the producer, native rule inspection and real packet evidence.
    """
    validate_stable(before)
    need(isinstance(changes, list) and 1 <= len(changes) <= 5, "bounded owned delta required")
    result = copy.deepcopy(before)
    seen = set()
    for change in changes:
        need(set(change) == {"path", "before_exists", "before", "after"}, "invalid delta fields")
        path = change["path"]
        need(isinstance(path, list) and all(isinstance(x, str) for x in path), "invalid delta path")
        key = tuple(path)
        need(key not in seen, "duplicate delta path")
        seen.add(key)
        owned_zone = len(path) == 3 and path[0] == "zone_inventory" and path[1] in ("runtime", "permanent") and path[2] == ZONE
        owned_policy = len(path) == 5 and path[:3] == ["firewall", "active_policies", "configurations"] and path[3] in ("runtime", "permanent") and path[4] == POLICY
        active = path == ["firewall", "active_policies", "active_output"]
        need(owned_zone or owned_policy or active, "delta outside owned CNI objects")
        parent = result
        for component in path[:-1]:
            need(isinstance(parent, dict) and component in parent, "delta parent absent")
            parent = parent[component]
        need(isinstance(parent, dict), "delta parent is not an object")
        present = path[-1] in parent
        need(type(change["before_exists"]) is bool and change["before_exists"] is present,
             "delta original presence differs")
        need(exact(parent.get(path[-1]), change["before"]), "delta original value differs")
        if owned_zone or owned_policy:
            need(not present and change["before"] is None, "pre-existing object is not owned")
            need(isinstance(change["after"], dict) and bool(change["after"]), "owned definition absent")
        else:
            need(present and isinstance(change["after"], str), "active policy text absent")
        need(not exact(change["before"], change["after"]), "no-op delta")
        parent[path[-1]] = copy.deepcopy(change["after"])
    need(("zone_inventory", "runtime", ZONE) in seen and
         ("firewall", "active_policies", "configurations", "runtime", POLICY) in seen,
         "runtime zone and policy delta required")
    validate_stable(result)
    return result


def validate(contract, original, original_input_sha256, bundle, observation, current=None):
    current = current or dt.datetime.now(dt.timezone.utc)
    need(current.utcoffset() is not None, "current time needs timezone")
    digest(original_input_sha256)
    receipt0 = original_validator()(original)
    need(contract.get("schema") == "fds.cp1-cni-handoff-contract.v1", "contract schema differs")
    need(exact(contract.get("scope"), SCOPE), "candidate scope differs")
    need(contract.get("status") == "REVIEWED_CONTRACT", "contract still draft")
    need(contract.get("original_input_sha256") == original_input_sha256 and
         contract.get("original_receipt_sha256") == fingerprint(receipt0), "original receipt link differs")
    review = contract["review"]
    need(review.get("reviewer") == "hayeounglee" and review.get("decision") == "APPROVED", "contract review absent")
    need(re.fullmatch(r"https://github\.com/321Team/infra-kubernetes/pull/[0-9]+#(?:issuecomment|pullrequestreview)-[0-9]+",
                      review.get("url") or ""), "contract review URL invalid")
    producer = contract["producer"]
    need(set(producer) == {"source_commit", "source_sha256", "plan_sha256"}, "producer pins incomplete")
    need(re.fullmatch(r"[0-9a-f]{40}", producer.get("source_commit") or ""), "producer commit invalid")
    digest(producer["source_sha256"])
    digest(producer["plan_sha256"])
    before = contract["before"]
    need(exact(before["firewall"], receipt0["after"]), "transition before differs from original after")
    after = expected_after(before, contract["changes"])
    start, end = instant(contract["window"]["start"]), instant(contract["window"]["end"])
    need(contract["window"].get("end_exclusive") is True and 0 < (end - start).total_seconds() <= 7200,
         "candidate window must be bounded to at most two hours")
    need(instant(receipt0["ended_at"]) <= instant(review["reviewed_at"]) <= start <= current < end,
         "outside reviewed window or review predates original result")
    need(current.astimezone(dt.timezone(dt.timedelta(hours=9))).weekday() < 5, "weekend window")
    need(bundle.get("schema") == "fds.cp1-cni-handoff-bundle.v1", "bundle schema differs")
    receipt = bundle["change_receipt"]
    need(receipt.get("schema") == "fds.cp1-cni-change-receipt.v1" and
         receipt.get("status") == "ACTIVE_REVIEW_REQUIRED", "change is incomplete/recovered")
    need(receipt.get("contract_sha256") == fingerprint(contract) and exact(receipt.get("producer"), producer),
         "change contract/producer differs")
    need(receipt.get("original_receipt_sha256") == fingerprint(receipt0) and
         receipt.get("original_input_sha256") == original_input_sha256, "change original link differs")
    run_id = receipt.get("run_id")
    need(isinstance(run_id, str) and re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", run_id), "invalid change run ID")
    started, ended = instant(receipt["started_at"]), instant(receipt["ended_at"])
    need(start <= started <= ended <= current < end, "change timestamp outside window")
    need(exact(receipt.get("before"), before) and exact(receipt.get("after"), after), "change state differs from replayed delta")
    operations = receipt.get("operations")
    need(isinstance(operations, list) and len(operations) == len(contract["changes"]), "change operation sequence incomplete")
    previous = started
    for operation, change in zip(operations, contract["changes"]):
        need(exact(operation.get("change"), change), "change operation reordered/differs")
        intent, finished = instant(operation["intent_at"]), instant(operation["completed_at"])
        need(previous <= intent <= finished <= ended, "operation ordering/timestamps invalid")
        previous = finished
        need(operation.get("status") == "CHANGED" and exact(operation.get("result"),
             {"rc": 0, "stdout": "success", "stderr": "", "truncated": False}), "uncertain operation result")
    acceptance = bundle["acceptance"]
    need(acceptance.get("decision") == "ACCEPTED" and acceptance.get("reviewer") == "hayeounglee", "acceptance absent")
    need(acceptance.get("change_receipt_sha256") == fingerprint(receipt) and
         acceptance.get("state_sha256") == fingerprint(after), "acceptance subject differs")
    need(re.fullmatch(r"https://github\.com/321Team/infra-kubernetes/pull/[0-9]+#(?:issuecomment|pullrequestreview)-[0-9]+",
                      acceptance.get("review_url") or ""), "acceptance review URL invalid")
    reviewed = instant(acceptance["reviewed_at"])
    need(ended <= reviewed <= current, "acceptance predates change or is in future")
    evidence = bundle["evidence"]
    need(set(evidence) == set(acceptance.get("evidence_sha256", {})) == {"positive", "negative", "regression"},
         "acceptance categories incomplete")
    for category, report in evidence.items():
        need(acceptance["evidence_sha256"][category] == fingerprint(report), "evidence checksum differs")
        need(report.get("kind") == "ACTUAL_CP1_CALICO_RUNTIME" and report.get("category") == category and
             report.get("status") == "PASS", "synthetic/missing/failed runtime assertion")
        need(report.get("run_id") == run_id and report.get("state_sha256") == fingerprint(after), "evidence subject differs")
        need(ended <= instant(report["measured_at"]) <= reviewed, "evidence timestamp invalid")
        digest(report.get("raw_archive_sha256"))
    need(observation.get("schema") == "fds.cp1-cni-handoff-observation.v1" and
         observation.get("stage") in ("BEFORE_TARGET", "AFTER_TARGET"), "observation stage differs")
    need(observation.get("target") in ("k8s-control02", "k8s-control03") and
         observation.get("target_registered") is False, "pre-join target required")
    need(observation.get("run_id") == run_id and observation.get("change_status") == "ACTIVE_REVIEW_REQUIRED",
         "observed change no longer active")
    observed_at = instant(observation["observed_at"])
    need(reviewed <= observed_at <= current and (current - observed_at).total_seconds() <= 900, "observation stale/future")
    need(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", receipt.get("boot_id") or "") and
         observation.get("boot_id") == receipt["boot_id"], "boot identity differs")
    need(exact(observation.get("stable"), after) and observation.get("state_sha256") == fingerprint(after),
         "live state/protection drift")
    recovery = observation["recovery"]
    need(recovery.get("armed") is True and recovery.get("pending") is False and
         recovery.get("run_id") == run_id and instant(recovery["deadline"]) == end,
         "independent recovery unavailable/in progress")
    reserve = 480 + 180 if observation["stage"] == "BEFORE_TARGET" else 180
    need((end - current).total_seconds() >= reserve, "insufficient target/cleanup window")
    return {"status": "CONSISTENT_REVIEW_REQUIRED", "stage": observation["stage"],
            "checked_at": current.isoformat(), "contract_sha256": fingerprint(contract),
            "state_sha256": fingerprint(after), "runtime_independently_verified": False,
            "authorizes_cp1_acceptance": False, "authorizes_cp2_change": False, "authorizes_join": False,
            "production_consumer_connected": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("contract", "original", "bundle", "observation"):
        parser.add_argument("--" + name, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        data = {name: load_checked(getattr(args, name), getattr(args, name + "_sha256"))
                for name in ("contract", "original", "bundle", "observation")}
        result = validate(**data, original_input_sha256=args.original_sha256)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError, RecursionError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error), "authorizes_cp1_acceptance": False,
                          "authorizes_cp2_change": False, "authorizes_join": False,
                          "production_consumer_connected": False}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
