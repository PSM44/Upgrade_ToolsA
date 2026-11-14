import json, os, sqlite3, unittest
from pathlib import Path


class Session2RealRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value=os.environ.get("NORTHSTAR_SESSION2_RUNTIME")
        if not value:raise unittest.SkipTest("NORTHSTAR_SESSION2_RUNTIME not set")
        cls.root=Path(value)

    def test_two_real_objectives_complete_without_human_relay(self):
        with sqlite3.connect(self.root/"control_plane.db") as c:
            rows=c.execute("select objective_id,state,result_json from objectives order by objective_id").fetchall()
        self.assertEqual(len(rows),2)
        for oid,state,result in rows:
            self.assertEqual(state,"COMPLETED",oid);self.assertEqual(json.loads(result)["human_relay_count"],0)

    def test_every_edge_consumer_acked_once(self):
        with sqlite3.connect(self.root/"transport.db") as c:
            out=c.execute("select count(*),count(distinct message_id),sum(state='ACKED') from outbox").fetchone()
            inbox=c.execute("select count(*),sum(state='ACKED') from inbox").fetchone()
        self.assertEqual(out,(14,14,14));self.assertEqual(inbox,(14,14))

    def test_real_hermes_receipts_are_correlated_and_cost_unknown(self):
        with sqlite3.connect(self.root/"model_calls.db") as c:
            c.row_factory=sqlite3.Row;rows=list(c.execute("select * from calls order by request_id"))
        self.assertEqual(len(rows),8);self.assertEqual(len({r["request_id"] for r in rows}),8)
        self.assertTrue(all(r["status"]=="COMPLETED" and r["actual_model"]=="20261030_RORCH" for r in rows))
        self.assertTrue(all(r["session_id"] and r["total_tokens"]>0 for r in rows))
        self.assertTrue(all(r["cost_status"]=="unknown" and r["cost_value"] is None for r in rows))

    def test_restart_and_timeout_recovery_did_not_duplicate_edges(self):
        with sqlite3.connect(self.root/"control_plane.db") as c:
            starts=c.execute("select count(*) from events where event_type='CONTROL_PLANE_STARTED'").fetchone()[0]
            rows=c.execute("select message_id,attempt,state from dispatches where objective_id='SESSION2-RESTART-HERMES-003'").fetchall()
        self.assertGreaterEqual(starts,2);self.assertEqual(len(rows),7)
        self.assertEqual(len({r[0] for r in rows}),7);self.assertTrue(all(r[2]=="COMPLETED" for r in rows))
        self.assertEqual(dict((r[0],r[1]) for r in rows)["SESSION2-RESTART-HERMES-003:delivery-1"],2)

    def test_independent_qa_negative_then_positive(self):
        for oid in ("SESSION2-REAL-HERMES-002","SESSION2-RESTART-HERMES-003"):
            qa=self.root/"objectives"/oid/"qa"
            self.assertNotEqual(json.loads((qa/"qa_round1.json").read_text())["exit_code"],0)
            self.assertEqual(json.loads((qa/"qa_round2.json").read_text())["exit_code"],0)


if __name__=="__main__":unittest.main()

