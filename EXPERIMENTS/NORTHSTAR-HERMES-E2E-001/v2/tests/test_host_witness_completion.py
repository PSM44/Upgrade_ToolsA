"""Deterministic TEST_ONLY event and crash scenarios. No ETW/provider/runtime."""
import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from host_witness_offline import TestKey, Rejected, parse
from host_witness_completion import CompletionLedger, DualCustody, CompletionFixture, PREFIX


class CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keytmp = tempfile.TemporaryDirectory(prefix="completion-TEST-keys-")
        cls.keys = {n: TestKey(cls.keytmp.name, n) for n in ("W", "D")}
        cls.public = {n: k.public for n, k in cls.keys.items()}

    @classmethod
    def tearDownClass(cls):
        cls.keytmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="completion-TEST-ledgers-")
        self.addCleanup(self.tmp.cleanup)
        self.n = 0
        self.ledgers = []
        self.addCleanup(self.close_ledgers)
        self.fresh()

    def close_ledgers(self):
        for l in self.ledgers:
            l.close()
        self.ledgers = []

    def fresh(self):
        self.close_ledgers()
        self.n += 1
        self.paths = {n: Path(self.tmp.name) / (str(self.n) + n + ".db") for n in ("W", "D")}
        self.ledgers = [CompletionLedger(self.paths[n], self.public, n, create=True)
                        for n in ("W", "D")]
        self.c = DualCustody(*self.ledgers)
        self.i = dict(run_id="a"*32, nonce="b"*64, objective="offline-completion",
            runtime="/home/psm/northstar/state/NORTHSTAR_P2_TEST", manifest="c"*64,
            session="simulated-session", host_boot="simulated-W", distro_boot="simulated-D",
            launch=dict(pid=100, start_ticks=123, uid=1000, exe_sha256="d"*64,
                        argv_sha256="e"*64, unit="TEST_ONLY"))
        self.run = self.i["run_id"]
        self.f = CompletionFixture(self.c, self.keys, self.i)
        self.root = dict(pid=100, start_ticks=123, parent=None, category="OTHER")

    def reopen(self):
        self.close_ledgers()
        self.ledgers = [CompletionLedger(self.paths[n], self.public, n) for n in ("W", "D")]
        self.c = DualCustody(*self.ledgers)
        self.f = CompletionFixture(self.c, self.keys, self.i)

    def send(self, kind, now, **payload):
        raw = self.f.record(kind, now, **payload)
        self.c.accept(raw, now)
        return raw

    def observe(self, kind, time, data):
        self.c.observe(self.run, self.f.event(kind, time, data), time)

    def reserve(self):
        self.send("RESERVE", 100)
        self.observe("INITIAL", 100, [])

    def prefix(self):
        self.reserve()
        for n, kind in enumerate(PREFIX[1:], 101):
            self.send(kind, n)
        self.observe("START", 105, self.root)

    def health(self, now=106):
        self.c.challenge(self.run, "HEALTH", now)
        return self.send("HEALTH", now)

    def quiescent(self):
        self.prefix()
        self.health()
        child = dict(pid=101, start_ticks=124, parent=[100,123], category="OTHER")
        self.observe("START", 106, child)
        self.observe("EXIT", 107, self.root)
        self.observe("EXIT", 108, child)
        self.c.challenge(self.run, "QUIESCENT", 108)
        self.send("QUIESCENT", 108)

    def drain(self):
        s = self.ledgers[0].state(self.run)
        self.observe("DRAIN", 109, dict(count=s["source_seq"], active=s["active"],
                     loss=0, consumer_ok=True, buffer_ok=True))

    def complete(self):
        self.quiescent()
        self.drain()
        self.send("CLOSE", 110)
        return self.ledgers[0].records(self.run)

    def assert_aborted(self):
        self.assertTrue(all(l.state(self.run)["phase"] == "ABORT" for l in self.ledgers))
        self.reopen()
        with self.assertRaises(Rejected):
            self.c.recover(self.run)

    def test_complete_signed_health_quiescence_drain_restart_audit(self):
        records = self.complete()
        self.reopen()
        result = self.c.recover(self.run)
        self.assertEqual(result["status"], "SIMULATED_PROTOCOL_VALID")
        self.assertFalse(result["execution_authorized"])
        self.assertTrue(result["simulation"])
        self.assertEqual(set(result["gates"].values()), {"OPEN"})
        self.assertEqual(self.c.audit(self.run, records), result)

    def test_repeated_health_monotonic_counter_and_fresh_challenges(self):
        self.prefix()
        a = parse(self.health(106))["body"]
        b = parse(self.health(110))["body"]
        self.assertEqual((a["payload"]["counter"], b["payload"]["counter"]), (1,2))
        self.assertNotEqual(a["payload"]["challenge"], b["payload"]["challenge"])
        self.assertEqual(a["payload"]["exec_ack"], b["payload"]["exec_ack"])

    def test_health_binding_counter_and_replay_rejected_durably(self):
        for field, value in (("counter", 0), ("counter", True), ("exec_ack", "0"*64),
                             ("challenge", "0"*64), ("source", "ETW"), ("source_head", "0"*64)):
            with self.subTest(field=field):
                self.fresh(); self.prefix()
                self.c.challenge(self.run, "HEALTH", 106)
                with self.assertRaises(Rejected):
                    self.send("HEALTH", 106, **{field:value})
                self.assert_aborted()
        self.fresh(); self.prefix()
        raw = self.health()
        with self.assertRaises(Rejected):
            self.c.accept(raw, 106)
        self.assert_aborted()

    def test_health_identity_signature_and_principal(self):
        for change in ("nonce", "run_id", "exec_identity", "signer", "signature"):
            with self.subTest(change=change):
                self.fresh(); self.prefix(); self.c.challenge(self.run, "HEALTH", 106)
                r = parse(self.f.record("HEALTH", 106)); b = r["body"]
                if change in ("nonce", "run_id"):
                    b["identity"][change] = "f"*len(b["identity"][change])
                elif change == "exec_identity":
                    b["identity"]["launch"]["start_ticks"] += 1
                elif change == "signer":
                    b["signer"] = "D"; b["key_id"] = self.keys["D"].key_id
                raw = self.keys["D" if change in ("signer","signature") else "W"].sign(b)
                with self.assertRaises(Rejected): self.c.accept(raw, 106)

    def test_heartbeat_loss_expired_challenge_and_clock_abort(self):
        for time in (121, 104):
            self.fresh(); self.prefix()
            with self.assertRaises(Rejected): self.c.tick(self.run, time)
            self.assert_aborted()
        self.fresh(); self.prefix(); self.c.challenge(self.run, "HEALTH", 106)
        with self.assertRaises(Rejected): self.send("HEALTH", 112)
        self.assert_aborted()

    def test_no_health_or_residual_process_or_premature_close(self):
        self.prefix()
        with self.assertRaises(Rejected): self.send("CLOSE", 106)
        self.assert_aborted()
        for residual in (True, False):
            self.fresh(); self.prefix()
            if residual: self.health()
            else: self.observe("EXIT", 106, self.root)
            self.c.challenge(self.run, "QUIESCENT", 107)
            with self.assertRaises(Rejected): self.send("QUIESCENT", 107)
            self.assert_aborted()

    def test_quiescent_payload_signed_D_and_binding(self):
        for field, value in (("exec_ack", "0"*64), ("descendants", []),
                             ("active_descendants", ["100:123"]), ("challenge", "0"*64)):
            self.fresh(); self.prefix(); self.health()
            self.observe("EXIT", 107, self.root)
            self.c.challenge(self.run, "QUIESCENT", 108)
            with self.assertRaises(Rejected): self.send("QUIESCENT", 108, **{field:value})
            self.assert_aborted()

    def test_initial_forbidden_unknown_ambiguous_inventory(self):
        for category in ("FORBIDDEN_ASSISTANT", "UNKNOWN", "AMBIGUOUS"):
            self.fresh(); self.send("RESERVE",100)
            p = dict(self.root, category=category)
            with self.assertRaises(Rejected): self.observe("INITIAL",100,[p])
            self.assert_aborted()

    def test_subpolling_forbidden_lifetime_is_detected(self):
        self.prefix()
        # START/EXIT would share a simulated tick shorter than 5-second polling.
        p = dict(pid=200,start_ticks=150,parent=None,category="FORBIDDEN_ASSISTANT")
        with self.assertRaises(Rejected): self.observe("START",106,p)
        self.assert_aborted()

    def test_pid_reuse_tracks_start_identity_and_rejects_stale_exit(self):
        self.prefix()
        p = dict(pid=200,start_ticks=150,parent=None,category="OTHER")
        self.observe("START",106,p); self.observe("EXIT",106,p)
        reused = dict(p,start_ticks=151)
        self.observe("START",107,reused)
        self.assertIn("200:151",self.ledgers[0].state(self.run)["active"])
        with self.assertRaises(Rejected): self.observe("EXIT",108,p)
        self.assert_aborted()

    def test_event_gap_duplicate_out_of_order_and_restart(self):
        for field, value in (("seq",1),("seq",4),("seq",True),("time",99),
                             ("session","restarted"),("source","ETW")):
            self.fresh(); self.prefix()
            event = self.f.event("EXIT",106,self.root); event[field] = value
            with self.assertRaises(Rejected): self.c.observe(self.run,event,106)
            self.assert_aborted()

    def test_consumer_buffer_interrupt_and_unknown_parent_fail_closed(self):
        for kind in ("CONSUMER_FAILURE","BUFFER_OVERFLOW","INTERRUPTED","RESTART","LOST"):
            self.fresh(); self.prefix()
            with self.assertRaises(Rejected): self.observe(kind,106,{})
            self.assert_aborted()
        self.fresh(); self.prefix()
        with self.assertRaises(Rejected):
            self.observe("START",106,dict(pid=200,start_ticks=151,parent=[999,1],category="OTHER"))
        self.assert_aborted()

    def test_late_forbidden_between_quiescent_and_close(self):
        self.quiescent()
        with self.assertRaises(Rejected):
            self.observe("START",108,dict(pid=200,start_ticks=151,parent=None,
                                        category="FORBIDDEN_ASSISTANT"))
        self.assert_aborted()

    def test_close_requires_drain_and_late_post_drain_revokes(self):
        self.quiescent()
        with self.assertRaises(Rejected): self.send("CLOSE",109)
        self.assert_aborted()
        self.fresh(); self.quiescent(); self.drain()
        with self.assertRaises(Rejected): self.observe("EXIT",110,self.root)
        self.assert_aborted()
        self.fresh(); self.complete()
        with self.assertRaises(Rejected): self.observe("EXIT",111,self.root)
        self.assert_aborted()

    def test_drain_faults_and_final_inventory_reconciliation(self):
        for field, value in (("count",0),("loss",1),("consumer_ok",False),
                             ("buffer_ok",False),("active",{"999:1":{}})):
            self.fresh(); self.quiescent()
            s = self.ledgers[0].state(self.run)
            data = dict(count=s["source_seq"],loss=0,consumer_ok=True,buffer_ok=True,active={})
            data[field] = value
            with self.assertRaises(Rejected): self.observe("DRAIN",109,data)
            self.assert_aborted()

    def test_replica_initial_middle_terminal_truncation(self):
        records = self.complete()
        variants = [records[:n] for n in range(len(records))]
        variants += [records[1:],records[:4]+records[5:],records+[b"{"]]
        for replica in variants:
            with self.assertRaises(Rejected): self.c.audit(self.run,replica)

    def test_custody_receipt_and_observation_truncation(self):
        for table in ("receipts","observations"):
            for position in ("initial","middle","terminal"):
                self.fresh(); self.complete()
                last=self.ledgers[1].db.execute("SELECT max(seq) FROM "+table).fetchone()[0]
                seq={"initial":1,"middle":3,"terminal":last}[position]
                # Direct TEST corruption, not a repair path.
                with self.ledgers[1].db:
                    self.ledgers[1].db.execute("DELETE FROM " + table + " WHERE run=? AND seq=?",
                                             (self.run,seq))
                with self.assertRaises(Rejected): self.c.recover(self.run)
                self.assert_aborted()

    def test_crash_reserve_arm_exec_ack_and_lost_ack_boundaries(self):
        for target in ("RESERVE","ARM","EXEC_ACK"):
            for crash in ("before_W","after_W","after_D"):
                with self.subTest(target=target,crash=crash):
                    self.fresh()
                    if target != "RESERVE":
                        self.reserve()
                        for now, kind in enumerate(PREFIX[1:],101):
                            if kind == target: break
                            self.send(kind,now)
                    now = 100 + PREFIX.index(target)
                    raw = self.f.record(target,now)
                    with self.assertRaises(InterruptedError): self.c.accept(raw,now,crash)
                    self.reopen()
                    with self.assertRaises(Rejected): self.c.recover(self.run)
                    with self.assertRaises(Rejected): self.c.audit(self.run,[raw])
                    if crash != "before_W" or target != "RESERVE":
                        for l in self.ledgers:
                            try: self.assertEqual(l.state(self.run)["phase"],"ABORT")
                            except Rejected: pass

    def test_duplicate_ack_resend_and_replay_abort(self):
        self.prefix()
        ack = self.ledgers[0].records(self.run)[-1]
        with self.assertRaises(Rejected): self.c.accept(ack,105)
        self.assert_aborted()
        with self.assertRaises(Rejected): self.c.accept(ack,106)
        reserve = self.ledgers[0].records(self.run)[0]
        with self.assertRaises(Rejected): self.c.accept(reserve,100)

    def test_missing_custody_not_recreated(self):
        self.complete(); self.close_ledgers()
        missing = Path(self.tmp.name)/"never-existed.db"
        with self.assertRaises(Rejected): CompletionLedger(missing,self.public,"D")
        self.assertFalse(missing.exists())

    def test_missing_ledger_after_open_revokes_other(self):
        self.complete()
        self.paths["D"].rename(self.paths["D"].with_suffix(".lost"))
        with self.assertRaises(Rejected): self.c.audit(self.run,self.ledgers[0].records(self.run))
        self.assertEqual(self.ledgers[0].state(self.run)["phase"],"ABORT")

    def test_diverged_state_and_aborted_restart(self):
        self.prefix()
        self.ledgers[0].challenge(self.run,"HEALTH",106,"f"*64)
        with self.assertRaises(Rejected): self.c.recover(self.run)
        self.assert_aborted()

    def test_simulated_result_cannot_unlock_runner(self):
        spec=importlib.util.spec_from_file_location("completion_runner",
            Path(__file__).resolve().parents[1]/"tools/p2_external.py")
        runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        records=self.complete()
        result=self.c.audit(self.run,records)
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Path(tmp)/"NORTHSTAR_P2_OFFLINE"
            for verdict in (result,dict(result,execution_authorized=True),
                            {"execution_authorized":False}):
                witness=Mock(); witness.verify.return_value=verdict
                with patch.object(runner,"STATE_ROOT",Path(tmp)), patch.object(
                        runner.subprocess,"Popen") as popen:
                    with self.assertRaises(runner.GateBlocked):
                        runner.execute(runtime,30,witness)
                    popen.assert_not_called()
                    self.assertFalse(runtime.exists())

    def test_clock_high_water_future_source_and_receipt_regressions(self):
        self.prefix()
        self.c.tick(self.run,110)
        with self.assertRaises(Rejected): self.c.tick(self.run,106)
        self.assert_aborted()
        self.fresh(); self.send("RESERVE",100)
        with self.assertRaises(Rejected):
            self.c.observe(self.run,self.f.event("INITIAL",1000,[]),100)
        self.assert_aborted()
        self.fresh(); self.prefix()
        self.c.observe(self.run,self.f.event("EXIT",108,self.root),108)
        self.c.challenge(self.run,"HEALTH",108)
        with self.assertRaises(Rejected): self.send("HEALTH",107)
        self.assert_aborted()

    def test_parent_exited_and_post_quiescent_descendants(self):
        self.prefix()
        self.observe("EXIT",106,self.root)
        child=dict(pid=101,start_ticks=124,parent=[100,123],category="OTHER")
        with self.assertRaises(Rejected): self.observe("START",107,child)
        self.assert_aborted()
        self.fresh(); self.quiescent()
        with self.assertRaises(Rejected): self.observe("START",109,child)
        self.assert_aborted()

    def test_terminal_fresh_status_single_use_persists(self):
        records=self.complete()
        token=self.c.terminal_challenge(self.run,111)
        raw=self.f.record("STATUS",111)
        self.assertEqual(parse(raw)["body"]["payload"]["challenge"],token)
        result=self.c.consume_terminal(self.run,records,raw,111)
        self.assertFalse(result["execution_authorized"])
        self.reopen()
        self.assertFalse(self.c.audit(self.run,records)["execution_authorized"])
        with self.assertRaises(Rejected):
            self.c.consume_terminal(self.run,records,raw,112)
        self.assert_aborted()

    def test_terminal_status_wrong_binding_expiry_and_partial_commit(self):
        for field,value in (("close","0"*64),("challenge","0"*64)):
            self.fresh(); records=self.complete(); self.c.terminal_challenge(self.run,111)
            raw=self.f.record("STATUS",111,**{field:value})
            with self.assertRaises(Rejected):
                self.c.consume_terminal(self.run,records,raw,111)
            self.assert_aborted()
        self.fresh(); records=self.complete(); self.c.terminal_challenge(self.run,111)
        raw=self.f.record("STATUS",111)
        with self.assertRaises(Rejected): self.c.consume_terminal(self.run,records,raw,117)
        self.assert_aborted()
        self.fresh(); records=self.complete(); self.c.terminal_challenge(self.run,111)
        raw=self.f.record("STATUS",111)
        self.ledgers[0].consume_terminal(self.run,raw,111)
        with self.assertRaises(Rejected): self.c.recover(self.run)
        self.assert_aborted()

    def test_stale_initial_inventory_before_capture_rejected(self):
        self.send("RESERVE",100)
        with self.assertRaises(Rejected):
            self.c.observe(self.run,self.f.event("INITIAL",99,[]),100)
        self.assert_aborted()

    def test_partial_physical_ledger_is_not_reconstructed(self):
        import sqlite3
        self.complete(); self.close_ledgers()
        data=self.paths["D"].read_bytes()
        self.paths["D"].write_bytes(data[:len(data)//2])
        with self.assertRaises((Rejected,sqlite3.DatabaseError)):
            CompletionLedger(self.paths["D"],self.public,"D")
        self.assertEqual(self.paths["D"].read_bytes(),data[:len(data)//2])

    def test_custody_path_replacement_revokes_detached_connection(self):
        self.complete()
        path=self.paths["D"]
        path.rename(path.with_suffix(".retained"))
        path.write_bytes(b"replacement is not custody")
        with self.assertRaises(Rejected):
            self.c.audit(self.run,self.ledgers[0].records(self.run))
        self.assertEqual(self.ledgers[0].state(self.run)["phase"],"ABORT")

    def test_abort_tombstone_allows_only_fresh_run_nonce(self):
        self.prefix(); self.c.abort(self.run,"TEST interruption")
        self.reopen()
        with self.assertRaises(Rejected): self.c.recover(self.run)
        other=copy.deepcopy(self.i)
        other.update(run_id="f"*32,nonce="e"*64,session="new-session")
        raw=CompletionFixture(self.c,self.keys,other).record("RESERVE",200)
        self.c.accept(raw,200)
        self.assertEqual(self.ledgers[0].state(other["run_id"])["phase"],"RESERVE")
        self.assertEqual(self.ledgers[0].state(self.run)["phase"],"ABORT")

    def test_reopen_cannot_bypass_recovery_with_direct_health(self):
        self.prefix()
        self.reopen()
        with self.assertRaises(Rejected): self.health(106)
        self.assert_aborted()

    def test_concurrent_reservations_serialize_before_reads(self):
        import threading
        raws=[]
        for n in ("1","2"):
            identity=copy.deepcopy(self.i)
            identity.update(run_id=n*32,nonce=n*64)
            raws.append(CompletionFixture(self.c,self.keys,identity).record("RESERVE",100))
        barrier=threading.Barrier(2)
        outcomes=[]
        def submit(raw):
            l=CompletionLedger(self.paths["W"],self.public,"W")
            def trace(sql):
                if sql.startswith("INSERT INTO runs"):
                    try: barrier.wait(timeout=0.2)
                    except threading.BrokenBarrierError: pass
            l.db.set_trace_callback(trace)
            try:
                l.accept(raw,100); outcomes.append("accepted")
            except Rejected: outcomes.append("rejected")
            finally: l.close()
        threads=[threading.Thread(target=submit,args=(raw,)) for raw in raws]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertCountEqual(outcomes,["accepted","rejected"])
        self.assertEqual(self.ledgers[0].db.execute("SELECT count(*) FROM runs").fetchone()[0],1)


if __name__ == "__main__":
    unittest.main()
