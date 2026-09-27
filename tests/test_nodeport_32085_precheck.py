from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/nodeport-30080-allow/field-collect-32085.sh"
DOC = ROOT / "ops/nodeport-30080-allow/PRECHECK_32085.md"


def test_precheck_is_read_only_and_covers_both_ports():
    text = SCRIPT.read_text()
    for forbidden in ("kubectl apply", "kubectl patch", "kubectl delete",
                      "firewall-cmd", "calicoctl apply"):
        assert forbidden not in text
    assert "30080" in text
    assert "32085" in text
    assert "transaction-api-nodeport" in text
    assert "nodeport-30080-allow" in text


def test_archive_manifest_excludes_itself():
    text = SCRIPT.read_text()
    assert "! -name SHA256SUMS" in text
    assert 'sha256sum --check "$OUT/SHA256SUMS"' in text


def test_document_forbids_32085_policy_shortcut():
    text = DOC.read_text()
    assert "Do **not** add an Allow or Deny rule for 32085" in text
    assert "no metrics NodePort" in text
