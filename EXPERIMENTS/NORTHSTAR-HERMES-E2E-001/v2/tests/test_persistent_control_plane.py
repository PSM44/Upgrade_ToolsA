import json, os, sqlite3, sys, tempfile, unittest
from pathlib import Path

SRC=Path(__file__).resolve().parents[1]/"src"; sys.path.insert(0,str(SRC))
from operational_memory import OperationalMemory


class MemoryRetrievalTest(unittest.TestCase):
    def test_relevant_memory_survives_reopen(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"memory.db"
            with OperationalMemory(db) as m:
                m.append(task_id="T",kind="DECISION",actor="MORCH_LOCAL_ADAPTER",
                         payload={"decision":"normalize punctuation before palindrome comparison"})
            with OperationalMemory(db) as m:
                found=m.search("T","punctuation normalization",kinds=["DECISION"])
                self.assertTrue(m.verify_chain("T")); self.assertEqual(found[0]["kind"],"DECISION")


class PersistentSessionEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value=os.environ.get("NORTHSTAR_CP_RUNTIME")
        if not value: raise unittest.SkipTest("NORTHSTAR_CP_RUNTIME not set")
        cls.runtime=Path(value)

    def test_all_objectives_completed_without_human_relay(self):
        with sqlite3.connect(self.runtime/"control_plane.db") as c:
            rows=c.execute("SELECT objective_id,state,result_json FROM objectives").fetchall()
        self.assertGreaterEqual(len(rows),4)
        for oid,state,result in rows:
            self.assertEqual(state,"COMPLETED",oid)
            self.assertEqual(json.loads(result)["human_relay_count"],0)

    def test_transport_has_consumer_owned_ack_for_every_edge(self):
        with sqlite3.connect(self.runtime/"transport.db") as c:
            out=c.execute("SELECT count(*),count(DISTINCT message_id),sum(state='ACKED') FROM outbox").fetchone()
            inbox=c.execute("SELECT count(*),sum(state='ACKED') FROM inbox").fetchone()
        self.assertEqual(out[0],out[1]); self.assertEqual(out[0],out[2]); self.assertEqual(inbox,(out[0],out[0]))

    def test_restart_continued_same_objective_without_duplicate_edges(self):
        oid="SESSION1-CRASH-001"
        with sqlite3.connect(self.runtime/"control_plane.db") as c:
            c.row_factory=sqlite3.Row
            dispatch=list(c.execute("SELECT * FROM dispatches WHERE objective_id=? ORDER BY started_at",(oid,)))
            starts=c.execute("SELECT count(*) FROM events WHERE event_type='CONTROL_PLANE_STARTED'").fetchone()[0]
        self.assertGreaterEqual(starts,3); self.assertEqual(len(dispatch),7)
        self.assertEqual(len({r["message_id"] for r in dispatch}),7)
        self.assertTrue(all(r["state"]=="COMPLETED" and r["attempt"]==1 for r in dispatch))

    def test_invalid_then_legitimate_patch_controls(self):
        qa=self.runtime/"objectives"/"SESSION1-CRASH-001"/"qa"
        neg=json.loads((qa/"qa_round1.json").read_text(encoding="utf-8"))
        pos=json.loads((qa/"qa_round2.json").read_text(encoding="utf-8"))
        self.assertNotEqual(neg["exit_code"],0); self.assertEqual(pos["exit_code"],0)

    def test_memory_retrieval_after_real_restart(self):
        with OperationalMemory(self.runtime/"memory.db") as m:
            found=m.search("SESSION1-MEMORY-001","punctuation normalization decision")
            self.assertTrue(m.verify_chain("SESSION1-MEMORY-001")); self.assertGreater(found[0]["score"],0)


if __name__=="__main__": unittest.main()

