from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from operational_memory import (
    AuthorizationError,
    CursorError,
    IntegrityError,
    OperationalMemory,
)


class OperationalMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "memory.db"
        self.mem = OperationalMemory(self.db)
        self.task = "NORTHSTAR-HERMES-CONVERGENCE-002"

    def tearDown(self) -> None:
        self.mem.close()
        self.tmp.cleanup()

    def test_record_types_and_chain(self) -> None:
        self.mem.append(task_id=self.task, kind="OBJECTIVE", actor="MORCH_LOCAL_ADAPTER", payload={"goal": "ship"})
        self.mem.append(task_id=self.task, kind="DEV_CONTEXT", actor="RORCH", payload={"file": "app.py"})
        self.mem.append(task_id=self.task, kind="ERROR", actor="QA", payload={"finding": "bug"})
        self.mem.append(task_id=self.task, kind="DECISION", actor="MORCH_LOCAL_ADAPTER", payload={"decision": "REWORK"})
        self.assertTrue(self.mem.verify_chain(self.task))
        self.assertEqual(4, len(self.mem.records(self.task)))

    def test_persists_across_close_and_reopen(self) -> None:
        record = self.mem.append(task_id=self.task, kind="OBJECTIVE", actor="MORCH_LOCAL_ADAPTER", payload={"goal": "persist"})
        self.mem.close()
        self.mem = OperationalMemory(self.db)
        recovered = self.mem.records(self.task)
        self.assertEqual(record.chain_sha256, recovered[0].chain_sha256)
        self.assertTrue(self.mem.verify_chain(self.task))

    def test_read_only_mode_verifies_without_writing(self) -> None:
        self.mem.append(
            task_id=self.task,
            kind="OBJECTIVE",
            actor="MORCH_LOCAL_ADAPTER",
            payload={"goal": "inspect"},
        )
        self.mem.close()
        self.mem = OperationalMemory(self.db, read_only=True)
        self.assertTrue(self.mem.verify_chain(self.task))
        with self.assertRaises(sqlite3.OperationalError):
            self.mem.append(task_id=self.task, kind="ERROR", actor="QA", payload={})

    def test_read_only_refuses_non_empty_wal(self) -> None:
        self.mem.append(
            task_id=self.task,
            kind="OBJECTIVE",
            actor="MORCH_LOCAL_ADAPTER",
            payload={"goal": "inspect"},
        )
        self.mem.close()
        wal = self.db.with_name(self.db.name + "-wal")
        wal.write_bytes(b"\x00" * 32)
        with self.assertRaises(IntegrityError):
            OperationalMemory(self.db, read_only=True)
        wal.write_bytes(b"")
        self.mem = OperationalMemory(self.db, read_only=True)
        self.assertTrue(self.mem.verify_chain(self.task))

    def test_read_only_opens_paths_with_uri_special_characters(self) -> None:
        tricky = Path(self.tmp.name) / "a b#c%d?e" / "memory.db"
        tricky.parent.mkdir()
        mem = OperationalMemory(tricky)
        mem.append(
            task_id=self.task,
            kind="OBJECTIVE",
            actor="MORCH_LOCAL_ADAPTER",
            payload={"goal": "inspect"},
        )
        mem.close()
        mem = OperationalMemory(tricky, read_only=True)
        try:
            self.assertTrue(mem.verify_chain(self.task))
        finally:
            mem.close()

    def test_qa_and_rorch_cannot_write_morch_decisions(self) -> None:
        for actor in ("QA", "RORCH", "UWS"):
            with self.subTest(actor=actor), self.assertRaises(AuthorizationError):
                self.mem.append(task_id=self.task, kind="DECISION", actor=actor, payload={"decision": "PASS"})

    def test_crash_before_checkpoint_commit_rolls_back(self) -> None:
        first = self.mem.checkpoint(task_id=self.task, state="QA_REJECTED", event_id="e1", context={"round": 1})
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            self.mem.checkpoint(task_id=self.task, state="QA_PASS", event_id="e2", context={"round": 2}, simulate_crash=True)
        self.mem.close()
        self.mem = OperationalMemory(self.db)
        rows = self.mem.records(self.task)
        self.assertEqual([first.record_id], [r.record_id for r in rows])
        self.assertEqual("QA_REJECTED", self.mem.recover(self.task)["checkpoint"]["state"])

    def test_recovery_returns_checkpoint_and_later_context(self) -> None:
        checkpoint = self.mem.checkpoint(task_id=self.task, state="RORCH_EXECUTING", event_id="e1", context={"attempt": 2})
        later = self.mem.append(task_id=self.task, kind="DEV_CONTEXT", actor="RORCH", payload={"change": "fixed"})
        recovered = self.mem.recover(self.task)
        self.assertEqual(checkpoint.seq, recovered["checkpoint_seq"])
        self.assertEqual(later.seq, recovered["records_after_checkpoint"][0]["seq"])
        self.assertTrue(recovered["chain_valid"])

    def test_qa_pass_durably_activates_local_morch_without_human(self) -> None:
        activation = self.mem.activate_morch_from_qa_pass(
            task_id=self.task, event_id="qa-pass-1", event_type="QA_PASS", source="QA",
            payload={"verdict": "PASS", "artifact_sha256": "a" * 64},
        )
        self.assertEqual("MORCH_ACTIVATION", activation.kind)
        self.assertEqual("MORCH_LOCAL_DELEGATE", activation.payload["authority_component"])
        self.assertEqual(0, activation.payload["human_relay_count"])
        receipt = self.mem.consume_morch_activation("qa-pass-1")
        self.assertEqual("CONSUMED", receipt["status"])
        self.assertIn("not ChatGPT", receipt["limitation"])

    def test_activation_replay_is_idempotent_but_conflict_fails(self) -> None:
        kwargs = dict(task_id=self.task, event_id="same", event_type="QA_PASS", source="QA", payload={"v": 1})
        first = self.mem.activate_morch_from_qa_pass(**kwargs)
        second = self.mem.activate_morch_from_qa_pass(**kwargs)
        self.assertEqual(first.seq, second.seq)
        with self.assertRaises(IntegrityError):
            self.mem.activate_morch_from_qa_pass(task_id=self.task, event_id="same", event_type="QA_PASS", source="QA", payload={"v": 2})

    def test_non_qa_pass_cannot_activate_morch(self) -> None:
        for source, event_type in (("RORCH", "QA_PASS"), ("QA", "QA_REJECTED")):
            with self.subTest(source=source, event_type=event_type), self.assertRaises(AuthorizationError):
                self.mem.activate_morch_from_qa_pass(task_id=self.task, event_id=str(source), event_type=event_type, source=source, payload={})

    def test_cursor_is_monotonic_and_persistent(self) -> None:
        record = self.mem.append(task_id=self.task, kind="OBJECTIVE", actor="MORCH_LOCAL_ADAPTER", payload={"goal": 1})
        self.mem.set_cursor(consumer="MORCH_LOCAL_ADAPTER", task_id=self.task, last_seq=record.seq)
        self.mem.close()
        self.mem = OperationalMemory(self.db)
        self.assertEqual(record.seq, self.mem.get_cursor(consumer="MORCH_LOCAL_ADAPTER", task_id=self.task))
        with self.assertRaises(CursorError):
            self.mem.set_cursor(consumer="MORCH_LOCAL_ADAPTER", task_id=self.task, last_seq=0)
        with self.assertRaises(CursorError):
            self.mem.set_cursor(consumer="QA", task_id=self.task, last_seq=record.seq + 1)

    def test_chain_detects_tampering(self) -> None:
        self.mem.append(task_id=self.task, kind="OBJECTIVE", actor="MORCH_LOCAL_ADAPTER", payload={"goal": "intact"})
        self.mem._conn.execute("UPDATE operational_memory SET payload_json='{}' WHERE seq=1")
        with self.assertRaises(IntegrityError):
            self.mem.verify_chain(self.task)


if __name__ == "__main__":
    unittest.main(verbosity=2)

