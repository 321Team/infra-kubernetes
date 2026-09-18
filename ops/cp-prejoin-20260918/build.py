"""Build the single-file observer; source bytes remain pinned and unmodified."""
import argparse
import base64
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINNED = {
    "scripts/node-firewall-session.py": "ea3b15454120431f7b7120829068e846f8991e32d2cd2e06056f9a4ab7110cd2",
    "policies/icr-d-net-01-01/node-firewall-v2.json": "7624989f489d983e44a9754d5c9baeeaa665ab80cfed9c8c189791b9578b5021",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=HERE.parents[1])
    args = parser.parse_args()
    pinned = {}
    for name, expected in PINNED.items():
        raw = (args.source_root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise SystemExit("Source mismatch: " + name)
        pinned[name] = raw
    source = (HERE / "controller_template.py").read_text()
    replacements = {
        "__NODE_PAYLOAD__": (HERE / "prejoin_node_read.py").read_bytes(),
        "__CP1_ADAPTER__": (HERE / "cp1_reuse_adapter.py").read_bytes(),
        "__APPROVED_HELPER__": pinned["scripts/node-firewall-session.py"],
        "__APPROVED_POLICY__": pinned["policies/icr-d-net-01-01/node-firewall-v2.json"],
    }
    for marker, raw in replacements.items():
        if source.count(marker) != 1:
            raise SystemExit("Bad template marker: " + marker)
        source = source.replace(marker, base64.b64encode(raw).decode())
    compile(source, "READ_CP_PREJOIN.py", "exec")
    destination = HERE / "READ_CP_PREJOIN.py"
    destination.write_text(source)
    sha = hashlib.sha256(destination.read_bytes()).hexdigest()
    (HERE / "READ_CP_PREJOIN.py.sha256").write_text(sha + "  READ_CP_PREJOIN.py\n")
    print("COLLECTOR_SHA256=" + sha)
    print("BYTES=" + str(destination.stat().st_size))


if __name__ == "__main__":
    main()
