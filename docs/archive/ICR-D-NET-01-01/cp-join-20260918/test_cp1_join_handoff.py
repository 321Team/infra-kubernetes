"""Focused ownership transition tests; no lab/systemd commands are executed."""
import contextlib
import copy
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("handoff", Path(__file__).with_name("cp1_join_handoff.py"))
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)
NOW = dt.datetime(2026, 9, 18, 2, 50, tzinfo=dt.timezone.utc)


def authority():
    return {"schema": "fds.cp-join-controller-verified-review.v1",
            "repository": "321Team/infra-kubernetes", "source_commit": "a" * 40,
            "reviewed_commit": "a" * 40, "contract_sha256": "b" * 64,
            "expected_contract_sha256": "b" * 64, "reviewer": "hayeounglee",
            "decision": "APPROVED", "verified_by": "controller-live-github-api",
            "review_url": "https://github.com/321Team/infra-kubernetes/pull/16#pullrequestreview-123456",
            "reviewed_at": "2026-09-18T02:45:00+00:00",
            "verified_at": "2026-09-18T02:49:00+00:00", "scope": copy.deepcopy(h.SCOPE)}


def request(mode="transfer"):
    return {"mode": mode, "job_id": h.JOB, "expected_change_sha256": h.OLD_CHANGE,
            "handoff_sha256": h.OLD_HANDOFF, "session_decision_sha256": h.OLD_DECISION,
            "authority": authority()}


class FakeBackend:
    def __init__(self):
        self.documents = {}
        self.stops = 0
        self.loaded = 0
        self.armed = True
        self.after_stop_service = "inactive"
        self.before_service = "inactive"
        self.queued_after_stop = False
        self.gc = False
        self.state = "c" * 64
        self.fail_commit = False
        self.load_error = None
        self.clock_value = NOW

    @contextlib.contextmanager
    def locked(self):
        yield

    def load(self):
        self.loaded += 1
        if self.load_error:
            raise ValueError(self.load_error)

    def clock(self):
        return self.clock_value

    def before(self):
        if not self.armed:
            raise ValueError("old timer missing")
        return self.state

    def retained_state(self):
        return self.state

    def units(self):
        active = self.before_service if self.armed else self.after_stop_service
        load = "not-found" if self.gc and not self.armed else "loaded"
        return {"service": {"LoadState": load, "ActiveState": active,
                            "SubState": "dead" if active == "inactive" else "start", "Job": ""},
                "timer": {"LoadState": load, "ActiveState": "active" if self.armed else "inactive",
                          "SubState": "waiting" if self.armed else "dead", "Job": ""},
                "queued": self.queued_after_stop and not self.armed}

    def stop_timer(self):
        self.stops += 1
        self.armed = False

    def read(self, name):
        return copy.deepcopy(self.documents.get(name))

    def write_once(self, name, value):
        if name == "TRANSFER_COMMITTED.json" and self.fail_commit:
            raise OSError("simulated fsync/storage failure")
        if name in self.documents:
            raise FileExistsError(name)
        self.documents[name] = copy.deepcopy(value)


class HandoffTests(unittest.TestCase):
    def test_inspect_and_uncommitted_status_never_stop_timer_or_write(self):
        for mode in ("inspect", "status"):
            io = FakeBackend()
            result = h.run(request(mode), io)
            self.assertEqual(result["status"], "CP1_HANDOFF_NOT_COMMITTED")
            self.assertFalse(result["join_may_start"])
            self.assertTrue(io.armed)
            self.assertEqual(io.stops, 0)
            self.assertEqual(io.documents, {})

    def test_transfer_is_idempotent_and_original_result_is_not_acceptance(self):
        io = FakeBackend()
        first = h.run(request(), io)
        self.assertEqual(first["status"], "CP1_JOIN_RUNTIME_RETAINED")
        self.assertTrue(first["join_may_start"])
        self.assertFalse(first["cp1_accepted"])
        self.assertEqual(first["independent_result_acceptance"], "PENDING")
        self.assertEqual(io.stops, 1)
        self.assertEqual(set(io.documents), {"TRANSFER_INTENT.json", "TRANSFER_COMMITTED.json"})
        self.assertFalse(io.documents["TRANSFER_COMMITTED.json"]["old_change_edited"])
        self.assertEqual(h.run(request(), io), first)
        self.assertEqual(h.run(request("status"), io), first)
        self.assertEqual(io.stops, 1)

    def test_active_recovery_before_transfer_has_no_mutation(self):
        io = FakeBackend()
        io.before_service = "activating"
        result = h.run(request(), io)
        self.assertFalse(result["join_may_start"])
        self.assertFalse(result["timer_mutation_attempted"])
        self.assertEqual(io.stops, 0)
        self.assertEqual(io.documents, {})

    def test_recovery_race_after_timer_stop_preserves_intent_and_blocks_join(self):
        for queued in (False, True):
            io = FakeBackend()
            if queued:
                io.queued_after_stop = True
            else:
                io.after_stop_service = "activating"
            result = h.run(request(), io)
            self.assertFalse(result["join_may_start"])
            self.assertTrue(result["timer_mutation_attempted"])
            self.assertNotIn("TRANSFER_COMMITTED.json", io.documents)
            self.assertIn("TRANSFER_INTENT.json", io.documents)
            self.assertFalse(h.run(request(), io)["join_may_start"])
            self.assertEqual(io.stops, 1)

    def test_failed_commit_is_not_automatic_rearm_or_join_success(self):
        io = FakeBackend(); io.fail_commit = True
        result = h.run(request(), io)
        self.assertEqual(result["status"], "CP1_HANDOFF_STOP_REVIEW_REQUIRED")
        self.assertFalse(result["join_may_start"])
        self.assertFalse(io.armed)
        self.assertEqual(io.stops, 1)
        self.assertIn("TRANSFER_INTENT.json", io.documents)
        self.assertFalse(h.run(request("status"), io)["join_may_start"])

    def test_foreign_job_and_evidence_rejected_before_loading(self):
        for key in ("job_id", "expected_change_sha256", "handoff_sha256", "session_decision_sha256"):
            io = FakeBackend(); bad = request(); bad[key] = "d" * len(bad[key])
            result = h.run(bad, io)
            self.assertFalse(result["join_may_start"])
            self.assertEqual(io.loaded, 0)
            self.assertEqual(io.stops, 0)

    def test_changed_review_source_or_scope_never_mutates(self):
        for key, value in (("reviewed_commit", "e" * 40), ("reviewer", "GWONUK"),
                           ("decision", "PENDING"), ("expected_contract_sha256", "f" * 64),
                           ("scope", dict(h.SCOPE, deadline="WITHDRAW"))):
            io = FakeBackend(); bad = request(); bad["authority"][key] = value
            result = h.run(bad, io)
            self.assertFalse(result["join_may_start"])
            self.assertEqual(io.stops, 0)
            self.assertEqual(io.documents, {})

    def test_idempotence_requires_same_approved_operation_and_live_state(self):
        io = FakeBackend(); self.assertTrue(h.run(request(), io)["join_may_start"])
        changed = request(); changed["authority"]["source_commit"] = "e" * 40
        changed["authority"]["reviewed_commit"] = "e" * 40
        self.assertFalse(h.run(changed, io)["join_may_start"])
        io.state = "f" * 64
        self.assertFalse(h.run(request("status"), io)["join_may_start"])
        self.assertEqual(io.stops, 1)

    def test_receipt_tampering_is_detected(self):
        io = FakeBackend(); h.run(request(), io)
        io.documents["TRANSFER_COMMITTED.json"]["authority"]["contract_sha256"] = "f" * 64
        result = h.run(request("status"), io)
        self.assertFalse(result["join_may_start"])

    def test_transient_unit_garbage_collection_is_safe_after_stop(self):
        io = FakeBackend(); io.gc = True
        self.assertTrue(h.run(request(), io)["join_may_start"])
        self.assertTrue(h.run(request("status"), io)["join_may_start"])

    def test_frozen_source_pin_really_checked_before_import_or_commands(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "scripts").mkdir()
            # First pinned file is altered; load must stop before any import,
            # systemd call, job write or source-compatible recovery execution.
            first = next(iter(h.FILE_PINS))
            (directory / "scripts" / first).write_text("raise RuntimeError('must not import')\n")
            backend = h.RootBackend(); backend.directory = directory
            with mock.patch.object(h, "trusted", side_effect=lambda path, directory=False: Path(path)):
                with self.assertRaisesRegex(ValueError, "frozen source differs"):
                    backend.load()

    def test_bundle_pin_matches_actual_five_file_map(self):
        self.assertEqual(h.digest(h.FILE_PINS), h.OLD_BUNDLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
