"""Offline contract tests; all GitHub dictionaries below are MOCK fixtures.

No GitHub requests, SSH connections, or Runtime commands are performed.
The delivered package is read only; tampering occurs only in temporary copies.
"""
import copy
import contextlib
import datetime as dt
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
ENTRY_FILE = PROJECT / "tools" / "RUN_CP_SESSION_APPROVED.py"
if not ENTRY_FILE.is_file():
    ENTRY_FILE = PROJECT / "RUN_CP_SESSION_APPROVED.py"
KIT = PROJECT / "FDS_CP_Session_cfae027_v4.0"
if not KIT.is_dir():
    KIT = PROJECT.parent / "FDS_CP_Session_cfae027_v4.0"
SPEC = importlib.util.spec_from_file_location("approved_entry_under_test", ENTRY_FILE)
ENTRY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ENTRY
SPEC.loader.exec_module(ENTRY)

UTC = dt.timezone.utc
CLOCK = dt.datetime(2026, 9, 18, 2, 0, tzinfo=UTC)
COMMIT = "cfae027fad62a36ee228da3217011fa0787c7486"
TOKEN = "CP_SESSION_DELEGATION_APPROVED / source " + COMMIT
API = "https://api.github.com/repos/321Team/infra-kubernetes"
WEB = "https://github.com/321Team/infra-kubernetes/pull/14"


class FixedDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return CLOCK.astimezone(tz) if tz is not None else CLOCK.replace(tzinfo=None)


def mock_authority():
    """MOCK payloads modeled on the supplied two-record approval shape."""
    user = {"login": "hayeounglee", "id": 95111439, "type": "User"}
    pr = {
        "number": 14,
        "state": "open",
        "html_url": WEB,
        "url": API + "/pulls/14",
        "head": {"sha": COMMIT},
        "base": {"repo": {"full_name": "321Team/infra-kubernetes"}},
    }
    review = {
        "id": 5242208554,
        "user": copy.deepcopy(user),
        "state": "APPROVED",
        "commit_id": COMMIT,
        "submitted_at": "2026-09-17T22:53:09Z",
        "body": "",
        "html_url": WEB + "#pullrequestreview-5242208554",
        "pull_request_url": API + "/pulls/14",
    }
    grant = {
        "id": 5723302935,
        "user": copy.deepcopy(user),
        "body": TOKEN,
        "created_at": "2026-09-18T00:48:11Z",
        "updated_at": "2026-09-18T00:48:11Z",
        "html_url": WEB + "#issuecomment-5723302935",
        "url": API + "/issues/comments/5723302935",
        "issue_url": API + "/issues/14",
    }
    return pr, [review], grant


class AuthorityTests(unittest.TestCase):
    def verify(self, pr=None, reviews=None, grant=None):
        original = mock_authority()
        return ENTRY.verify_authority(
            original[0] if pr is None else pr,
            original[1] if reviews is None else reviews,
            original[2] if grant is None else grant,
            CLOCK,
        )

    def test_empty_formal_review_plus_exact_grant_is_accepted_without_rewriting(self):
        pr, reviews, grant = mock_authority()
        unchanged = copy.deepcopy((pr, reviews, grant))
        result = self.verify(pr, reviews, grant)
        self.assertEqual(result["review_url"], grant["html_url"])
        self.assertEqual(result["reviewed_at"], grant["created_at"])
        self.assertEqual(result["source_review_url"], reviews[0]["html_url"])
        self.assertEqual((pr, reviews, grant), unchanged)
        self.assertEqual(reviews[0]["body"], "")

    def test_changed_pr_head_rejected(self):
        pr, _, _ = mock_authority()
        pr["head"]["sha"] = "b" * 40
        with self.assertRaises(ValueError):
            self.verify(pr=pr)

    def test_wrong_pr_or_repository_rejected(self):
        for key, value in (("number", 15), ("repository", "other/infra-kubernetes")):
            with self.subTest(key=key):
                pr, _, _ = mock_authority()
                if key == "repository":
                    pr["base"]["repo"]["full_name"] = value
                else:
                    pr[key] = value
                with self.assertRaises(ValueError):
                    self.verify(pr=pr)

    def test_missing_independent_review_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(reviews=[])

    def test_review_identity_head_and_canonical_urls_are_bound(self):
        changes = (
            ("login", "GWONUK"),
            ("user_id", 95111440),
            ("commit_id", "b" * 40),
            ("html_url", WEB + "#pullrequestreview-999999"),
            ("pull_request_url", API + "/pulls/15"),
        )
        for key, value in changes:
            with self.subTest(key=key):
                _, reviews, _ = mock_authority()
                if key == "login":
                    reviews[0]["user"]["login"] = value
                elif key == "user_id":
                    reviews[0]["user"]["id"] = value
                else:
                    reviews[0][key] = value
                with self.assertRaises(ValueError):
                    self.verify(reviews=reviews)

    def test_future_review_rejected(self):
        _, reviews, _ = mock_authority()
        reviews[0]["submitted_at"] = "2026-09-18T03:00:00Z"
        with self.assertRaises(ValueError):
            self.verify(reviews=reviews)

    def test_latest_changes_requested_or_dismissed_cannot_reuse_old_approval(self):
        for state in ("CHANGES_REQUESTED", "DISMISSED"):
            with self.subTest(state=state):
                _, reviews, _ = mock_authority()
                later = copy.deepcopy(reviews[0])
                later.update(
                    id=5242208555,
                    state=state,
                    submitted_at="2026-09-18T01:30:00Z",
                    html_url=WEB + "#pullrequestreview-5242208555",
                )
                reviews.append(later)
                with self.assertRaises(ValueError):
                    self.verify(reviews=reviews)

    def test_latest_approval_for_different_head_is_rejected(self):
        _, reviews, _ = mock_authority()
        later = copy.deepcopy(reviews[0])
        later.update(
            id=5242208555,
            commit_id="b" * 40,
            submitted_at="2026-09-18T01:30:00Z",
            html_url=WEB + "#pullrequestreview-5242208555",
        )
        reviews.append(later)
        with self.assertRaises(ValueError):
            self.verify(reviews=reviews)

    def test_unrelated_review_does_not_override_real_approval(self):
        _, reviews, _ = mock_authority()
        other = copy.deepcopy(reviews[0])
        other.update(id=5242208556, state="CHANGES_REQUESTED", submitted_at="2026-09-18T01:30:00Z")
        other["user"] = {"login": "someone_else", "id": 99999}
        reviews.append(other)
        self.assertEqual(self.verify(reviews=reviews)["review_url"], WEB + "#issuecomment-5723302935")

    def test_grant_identity_id_token_and_canonical_urls_are_bound(self):
        changes = (
            ("login", "GWONUK"),
            ("user_id", 95111440),
            ("id", 5723302936),
            ("body", "CP_SESSION_DELEGATION_APPROVED / source " + "b" * 40),
            ("body", "APPROVED"),
            ("body", "CP_SESSION_DELEGATION_APPROVED / source " + COMMIT[:-1]),
            ("html_url", WEB + "#issuecomment-5723302936"),
            ("html_url", "https://github.com/321Team/infra-kubernetes/pull/15#issuecomment-5723302935"),
            ("issue_url", API + "/issues/15"),
        )
        for key, value in changes:
            with self.subTest(key=key, value=value):
                _, _, grant = mock_authority()
                if key == "login":
                    grant["user"]["login"] = value
                elif key == "user_id":
                    grant["user"]["id"] = value
                else:
                    grant[key] = value
                with self.assertRaises(ValueError):
                    self.verify(grant=grant)

    def test_future_grant_rejected(self):
        _, _, grant = mock_authority()
        grant.update(created_at="2026-09-18T03:00:00Z", updated_at="2026-09-18T03:00:00Z")
        with self.assertRaises(ValueError):
            self.verify(grant=grant)

    def test_later_same_head_source_approval_preserves_real_grant_time(self):
        _, reviews, grant = mock_authority()
        later = copy.deepcopy(reviews[0])
        later.update(
            id=5242208555,
            submitted_at="2026-09-18T01:30:00Z",
            html_url=WEB + "#pullrequestreview-5242208555",
        )
        reviews.append(later)
        result = self.verify(reviews=reviews, grant=grant)
        self.assertEqual(result["source_review_url"], later["html_url"])
        self.assertEqual(result["reviewed_at"], grant["created_at"])

    def test_future_or_reversed_grant_update_rejected(self):
        for updated in ("2026-09-18T03:00:00Z", "2026-09-18T00:30:00Z"):
            with self.subTest(updated=updated):
                _, _, grant = mock_authority()
                grant["updated_at"] = updated
                with self.assertRaises(ValueError):
                    self.verify(grant=grant)

    def test_timezone_less_grant_rejected(self):
        _, _, grant = mock_authority()
        grant["created_at"] = "2026-09-18T00:48:11"
        with self.assertRaises(ValueError):
            self.verify(grant=grant)


class PackageTests(unittest.TestCase):
    def temporary_kit(self):
        temporary = tempfile.TemporaryDirectory(prefix="cp-approved-entry-test-")
        self.addCleanup(temporary.cleanup)
        kit = Path(temporary.name) / "kit"
        shutil.copytree(KIT, kit)
        return kit

    def test_delivered_kit_matches_pinned_manifest_and_content(self):
        entries = ENTRY.verify_kit(KIT)
        self.assertIsInstance(entries, dict)
        self.assertIn("RUN_CP_SESSION.py", entries)
        self.assertIn("source/scripts/run-cp1-minimal-change.py", entries)

    def test_manifest_tamper_rejected(self):
        kit = self.temporary_kit()
        path = kit / "PACKAGE_SHA256SUMS"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(ValueError):
            ENTRY.verify_kit(kit)

    def test_missing_manifest_entry_rejected(self):
        kit = self.temporary_kit()
        path = kit / "PACKAGE_SHA256SUMS"
        path.write_text("\n".join(path.read_text().splitlines()[1:]) + "\n")
        with self.assertRaises(ValueError):
            ENTRY.verify_kit(kit)

    def test_executable_tamper_rejected_before_any_import(self):
        kit = self.temporary_kit()
        sentinel = kit.parent / "UNSAFE_IMPORT_EXECUTED"
        (kit / "RUN_CP_SESSION.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(sentinel)) + ").touch()\n"
        )
        with self.assertRaises(ValueError):
            ENTRY.verify_kit(kit)
        self.assertFalse(sentinel.exists())

    def test_runtime_source_tamper_rejected(self):
        kit = self.temporary_kit()
        path = kit / "source/scripts/cp1_minimal_change.py"
        path.write_bytes(path.read_bytes() + b"\n# MOCK unexpected source edit\n")
        with self.assertRaises(ValueError):
            ENTRY.verify_kit(kit)

    def test_same_bytes_symlink_rejected(self):
        kit = self.temporary_kit()
        path = kit / "RUN_CP_SESSION.py"
        moved = kit.parent / "outside-entry.py"
        path.rename(moved)
        path.symlink_to(moved)
        with self.assertRaises(ValueError):
            ENTRY.verify_kit(kit)


class EntryIntegrationTests(unittest.TestCase):
    def test_default_plan_never_requests_approval_or_runs_commands(self):
        output = io.StringIO()
        with (
            patch.object(ENTRY, "check_live", side_effect=AssertionError("unexpected GitHub check")) as live,
            patch("subprocess.run", side_effect=AssertionError("unexpected subprocess")),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(ENTRY.main(["--kit", str(KIT)]), 0)
        self.assertIn('"mode": "PLAN_ONLY"', output.getvalue())
        self.assertIn('"entry_code_covered_by_source_review": false', output.getvalue())
        live.assert_not_called()

    def test_missing_authority_stops_before_any_operator_prompt_or_commands(self):
        with (
            patch.object(ENTRY, "check_live", side_effect=ValueError("MOCK missing approval")),
            patch("builtins.input", side_effect=AssertionError("unexpected confirmation")) as prompt,
            patch("subprocess.run", side_effect=AssertionError("unexpected subprocess")),
            self.assertRaisesRegex(ValueError, "MOCK missing approval"),
        ):
            ENTRY.main(["--execute", "--kit", str(KIT)])
        prompt.assert_not_called()

    def test_revocation_during_single_confirmation_stops_before_mutation(self):
        pr, reviews, grant = mock_authority()
        proof = ENTRY.verify_authority(pr, reviews, grant, CLOCK)
        end = CLOCK + dt.timedelta(hours=1)
        output = io.StringIO()
        with (
            patch.object(ENTRY, "check_live", side_effect=[(proof, {}), ValueError("MOCK review revoked")]) as live,
            patch.object(ENTRY.dt, "datetime", FixedDatetime),
            patch.object(ENTRY.os, "geteuid", return_value=1000),
            patch.object(ENTRY.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="server01_ansible")),
            patch.object(ENTRY.socket, "gethostname", return_value="ansible01"),
            patch("builtins.input", return_value="ICR-CP-SESSION-GO") as prompt,
            patch("subprocess.run", side_effect=AssertionError("unexpected subprocess")),
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(ValueError, "MOCK review revoked"),
        ):
            ENTRY.main(["--execute", "--kit", str(KIT), "--window-end", end.isoformat(), "--recovery-margin", "180"])
        self.assertEqual(live.call_count, 2)
        prompt.assert_called_once()


if __name__ == "__main__":
    unittest.main()
