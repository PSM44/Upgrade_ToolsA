from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from secure_transport import (  # noqa: E402
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    LeaseError,
    ReplayError,
    SecureTransport,
)


BOOT = b"fixture-bootstrap-not-production"
KEYS = {
    "morch": b"fixture-morch-key-32-bytes-long!",
    "rorch": b"fixture-rorch-key-32-bytes-long!",
    "qa": b"fixture-qa-key-32-bytes-long!!!!",
    "uws": b"fixture-uws-key-32-bytes-long!!!",
}


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "transport.db"
        self.t = SecureTransport(self.db, BOOT, replay_window_s=60)
        for actor, role in (("morch", "MORCH"), ("rorch", "RORCH"), ("qa", "QA"), ("uws", "UWS")):
            self.t.register_actor(actor, role, KEYS[actor], bootstrap_secret=BOOT)

    def tearDown(self):
        self.temp.cleanup()

    def enqueue_task(self, mid="m1", nonce="n1", now=None, retries=3):
        now = time.time() if now is None else now
        return self.t.enqueue(producer="morch", producer_key=KEYS["morch"], recipient="rorch",
                              message_type="TASK_HANDOFF", payload={"objective": "build"},
                              message_id=mid, nonce=nonce, issued_at=now, now=now, max_retries=retries)

    def test_outbox_inbox_ack_are_distinct_phases(self):
        self.assertEqual(self.enqueue_task(), "PENDING")
        state = self.t.state("m1")
        self.assertEqual(state["outbox"]["state"], "PENDING")
        self.assertIsNone(state["inbox"])
        delivery = self.t.claim(consumer="rorch", consumer_key=KEYS["rorch"])
        self.assertIsNotNone(delivery)
        self.assertEqual(self.t.state("m1")["inbox"]["state"], "PROCESSING")
        self.t.ack(consumer="rorch", consumer_key=KEYS["rorch"], message_id="m1", claim_token=delivery.claim_token)
        self.assertEqual(self.t.state("m1")["outbox"]["state"], "AWAITING_RECONCILIATION")
        self.assertEqual(self.t.reconcile()["acked"], 1)
        self.assertEqual(self.t.state("m1")["outbox"]["state"], "ACKED")

    def test_duplicate_suppressed_and_id_conflict_detected(self):
        self.enqueue_task()
        self.assertEqual(self.enqueue_task(), "DUPLICATE")
        with self.assertRaises(ConflictError):
            self.t.enqueue(producer="morch", producer_key=KEYS["morch"], recipient="rorch",
                           message_type="TASK_HANDOFF", payload={"objective": "different"},
                           message_id="m1", nonce="different-nonce")
        self.assertEqual(self.t.state("m1")["outbox"]["state"], "CONFLICT")

    def test_replay_nonce_and_stale_timestamp_rejected(self):
        now = 1000.0
        self.enqueue_task(now=now)
        with self.assertRaises(ReplayError):
            self.t.enqueue(producer="morch", producer_key=KEYS["morch"], recipient="rorch",
                           message_type="TASK_HANDOFF", payload={}, message_id="m2", nonce="n1",
                           issued_at=now, now=now)
        with self.assertRaises(ReplayError):
            self.t.enqueue(producer="morch", producer_key=KEYS["morch"], recipient="rorch",
                           message_type="TASK_HANDOFF", payload={}, message_id="m3", nonce="n3",
                           issued_at=now - 61, now=now)

    def test_spoofed_qa_and_forged_pass_rejected(self):
        with self.assertRaises(AuthenticationError):
            self.t.enqueue(producer="qa", producer_key=KEYS["rorch"], recipient="morch",
                           message_type="QA_PASS", payload={"pass": True}, message_id="q1", nonce="q1")
        with self.assertRaises(AuthorizationError):
            self.t.enqueue(producer="rorch", producer_key=KEYS["rorch"], recipient="morch",
                           message_type="QA_PASS", payload={"pass": True}, message_id="q2", nonce="q2")

    def test_role_route_prevents_privilege_escalation(self):
        with self.assertRaises(AuthorizationError):
            self.t.enqueue(producer="qa", producer_key=KEYS["qa"], recipient="rorch",
                           message_type="ACCEPTED", payload={}, message_id="x1", nonce="x1")
        with self.assertRaises(AuthenticationError):
            self.t.register_actor("evil", "QA", b"evil-key", bootstrap_secret=b"wrong")

    def test_only_intended_consumer_can_claim_and_ack(self):
        self.enqueue_task()
        self.assertIsNone(self.t.claim(consumer="qa", consumer_key=KEYS["qa"]))
        delivery = self.t.claim(consumer="rorch", consumer_key=KEYS["rorch"])
        with self.assertRaises(LeaseError):
            self.t.ack(consumer="qa", consumer_key=KEYS["qa"], message_id="m1", claim_token=delivery.claim_token)

    def test_concurrent_claim_has_single_winner(self):
        self.enqueue_task()
        barrier = threading.Barrier(3)
        results = []

        def worker():
            barrier.wait()
            results.append(self.t.claim(consumer="rorch", consumer_key=KEYS["rorch"], lease_s=10))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_nack_retries_then_dead_letter(self):
        self.enqueue_task(retries=2)
        d1 = self.t.claim(consumer="rorch", consumer_key=KEYS["rorch"])
        self.assertEqual(self.t.nack(consumer="rorch", consumer_key=KEYS["rorch"], message_id="m1",
                                     claim_token=d1.claim_token, reason="temporary"), "PENDING")
        d2 = self.t.claim(consumer="rorch", consumer_key=KEYS["rorch"])
        self.assertEqual(self.t.nack(consumer="rorch", consumer_key=KEYS["rorch"], message_id="m1",
                                     claim_token=d2.claim_token, reason="again"), "DEAD")

    def test_uws_redrive_dead_resets_retry_budget_and_is_audited(self):
        self.enqueue_task(retries=1)
        delivery = self.t.claim(consumer="rorch", consumer_key=KEYS["rorch"])
        self.assertEqual(self.t.nack(consumer="rorch", consumer_key=KEYS["rorch"], message_id="m1",
                                     claim_token=delivery.claim_token, reason="implementation defect"), "DEAD")
        self.assertEqual(self.t.redrive_dead("m1", reason="implementation repaired"), "PENDING")
        state = self.t.state("m1")["outbox"]
        self.assertEqual(state["attempt_count"], 0)
        self.assertEqual(self.t.audit_rows()[-1]["action"], "UWS_DEAD_REDRIVE")
        with self.assertRaises(ConflictError):
            self.t.redrive_dead("m1", reason="duplicate redrive")

    def test_expired_lease_recovers_without_duplicate_inbox(self):
        base = time.time()
        self.enqueue_task(now=base)
        d1 = self.t.claim(consumer="rorch", consumer_key=KEYS["rorch"], lease_s=1, now=base)
        self.assertIsNotNone(d1)
        self.assertEqual(self.t.reconcile(now=base + 2)["requeued"], 1)
        d2 = self.t.claim(consumer="rorch", consumer_key=KEYS["rorch"], lease_s=1, now=base + 2)
        self.assertEqual(d2.attempt, 2)
        self.assertEqual(self.t.counts()["inbox"], 1)

    def test_ack_ambiguous_crash_reconciles_after_reopen(self):
        self.enqueue_task()
        d = self.t.claim(consumer="rorch", consumer_key=KEYS["rorch"])
        receipt = self.t.ack(consumer="rorch", consumer_key=KEYS["rorch"], message_id="m1", claim_token=d.claim_token)
        self.assertTrue(receipt)
        reopened = SecureTransport(self.db, BOOT, replay_window_s=60)
        self.assertEqual(reopened.state("m1")["outbox"]["state"], "AWAITING_RECONCILIATION")
        self.assertEqual(reopened.reconcile()["acked"], 1)

    def test_audit_is_append_only_even_for_authenticated_uws(self):
        self.enqueue_task()
        seq = self.t.audit_rows()[0]["seq"]
        with self.assertRaises(sqlite3.IntegrityError):
            self.t.attempt_audit_mutation(actor="uws", actor_key=KEYS["uws"], seq=seq)
        with self.assertRaises(AuthorizationError):
            self.t.attempt_audit_mutation(actor="rorch", actor_key=KEYS["rorch"], seq=seq)

    def test_payload_claims_do_not_grant_authority(self):
        self.t.enqueue(producer="rorch", producer_key=KEYS["rorch"], recipient="qa",
                       message_type="DELIVERY", payload={"actor": "QA", "role": "MORCH", "verdict": "PASS"},
                       message_id="d1", nonce="d1")
        delivery = self.t.claim(consumer="qa", consumer_key=KEYS["qa"])
        self.assertEqual(delivery.producer, "rorch")
        self.assertEqual(delivery.message_type, "DELIVERY")


if __name__ == "__main__":
    unittest.main(verbosity=2)

