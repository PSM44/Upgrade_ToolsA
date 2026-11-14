"""TEST_ONLY completion of the rev3 offline protocol; no host accreditation.

Reuses host_witness_offline canonical bytes, P-256 signatures and identity schema.
The old seven-record harness remains historical compatibility, not completion.
All clocks and process observations here are explicitly simulated trusted inputs.
"""
import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from host_witness_offline import (
    BODY_FIELDS, TestKey, Rejected, canonical, digest, identity_valid, parse,
    require, verify_signature,
)

PREFIX = ("RESERVE", "BEGIN", "READY", "ARM", "EXEC_BIND", "EXEC_ACK")
ROLES = dict(zip(PREFIX, ("D", "W", "W", "D", "D", "W")))
ROLES.update(HEALTH="W", QUIESCENT="D", CLOSE="W", STATUS="W")
SCHEMA = "ns-hostwitness-offline-completion-v1"
ZERO = "0" * 64
HEARTBEAT_WINDOW = 15
CHALLENGE_WINDOW = 5


def blank(identity, now):
    return dict(identity=identity, phase="RESERVE", seq=0, head=ZERO, stamp=now,
                capture_start=now, clock=now, consumed=False, terminal_pending=None, deadline=now + 30, ack=None, begin=None, health=0,
                last_health=None, pending=None, used_challenges=[], quiescent=None,
                source_seq=0, source_head=ZERO, source_time=-1, initial=False,
                active={}, seen=[], descendants=[], drained=False, reason=None)


def process_key(process):
    require(type(process) is dict and set(process) ==
            {"pid", "start_ticks", "parent", "category"}, "process schema")
    require(type(process["pid"]) is int and process["pid"] > 0 and
            type(process["start_ticks"]) is int and process["start_ticks"] >= 0,
            "process identity")
    require(process["category"] == "OTHER", "forbidden/unknown process identity")
    parent = process["parent"]
    require(parent is None or (type(parent) is list and len(parent) == 2 and
            all(type(n) is int and n >= 0 for n in parent)), "parent identity")
    return str(process["pid"]) + ":" + str(process["start_ticks"])


class CompletionLedger:
    """One logical W or D custodian. Creation is explicit, never recovery.

    Same-user SQLite and TEST keys do not establish operating-system separation.
    Each accepted receipt and each source event is committed before returning.
    """
    def __init__(self, path, keys, role, *, create=False):
        self.path = Path(path)
        require(role in ("W", "D"), "custodian role")
        require(not self.path.is_symlink(), "ledger symlink")
        require(not self.path.exists() if create else self.path.is_file(),
                "ledger exists" if create else "missing custody")
        self.role = role
        self.keys = {n: (Path(p), hashlib.sha256(Path(p).read_bytes()).hexdigest())
                     for n, p in keys.items()}
        require(set(self.keys) == {"W", "D"} and self.keys["W"][1] != self.keys["D"][1],
                "distinct custodians")
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=" +
                                  ("rwc" if create else "rw"), uri=True)
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            if create:
                self.db.executescript("""
                    CREATE TABLE meta(role TEXT NOT NULL);
                    CREATE TABLE runs(id TEXT PRIMARY KEY, nonce TEXT UNIQUE, state BLOB NOT NULL);
                    CREATE TABLE receipts(run TEXT, seq INTEGER, raw BLOB NOT NULL,
                                          PRIMARY KEY(run,seq));
                    CREATE TABLE observations(run TEXT, seq INTEGER, raw BLOB NOT NULL,
                                              PRIMARY KEY(run,seq));
                """)
                with self.transaction():
                    self.db.execute("INSERT INTO meta VALUES(?)", (role,))
            require(self.db.execute("SELECT role FROM meta").fetchall() == [(role,)],
                    "custodian identity")
            stat = self.path.stat()
            self.file_identity = (stat.st_dev, stat.st_ino)
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def present(self):
        require(self.path.is_file() and not self.path.is_symlink(), "missing custody")
        stat = self.path.stat()
        require((stat.st_dev, stat.st_ino) == self.file_identity, "custody path replaced")

    def close(self):
        self.db.close()

    def state(self, run):
        row = self.db.execute("SELECT state FROM runs WHERE id=?", (run,)).fetchone()
        require(row is not None, "missing reservation")
        return parse(row[0])

    def _save(self, run, state):
        self.db.execute("UPDATE runs SET state=? WHERE id=?", (canonical(state), run))

    def abort(self, run, reason):
        with self.transaction():
            s = self.state(run)
            s.update(phase="ABORT", reason=reason, pending=None)
            self._save(run, s)

    def tick(self, run, now):
        require(type(now) is int, "clock type")
        reason = None
        with self.transaction():
            s = self.state(run)
            require(s["phase"] != "ABORT", "durable ABORT")
            if now < s["clock"]:
                reason = "clock discontinuity"
            elif s["phase"] != "CLOSE":
                if s["ack"] is None and now > s["deadline"]:
                    reason = "launch deadline"
                elif s["ack"] is not None and now > s["last_health"] + HEARTBEAT_WINDOW:
                    reason = "heartbeat lost"
                elif s["pending"] and now > s["pending"]["expires"]:
                    reason = "challenge response lost"
            if reason:
                s.update(phase="ABORT", reason=reason, pending=None)
            else:
                s["clock"] = now
            self._save(run, s)
        if reason:
            raise Rejected(reason)

    def challenge(self, run, kind, now, token):
        self.tick(run, now)
        with self.transaction():
            s = self.state(run)
            require(kind in ("HEALTH", "QUIESCENT") and s["ack"] is not None and
                    s["phase"] in ("EXEC_ACK", "HEALTH"), "challenge phase")
            require(s["pending"] is None, "outstanding challenge")
            require(type(token) is str and len(token) == 64 and
                    all(c in "0123456789abcdef" for c in token) and
                    token not in s["used_challenges"] and token != s["identity"]["nonce"],
                    "fresh challenge")
            s["pending"] = dict(kind=kind, token=token, issued=now,
                                expires=now + CHALLENGE_WINDOW)
            s["used_challenges"].append(token)
            self._save(run, s)
        return token

    def observe(self, run, event, now):
        """Explicit SIMULATED event source, including initial and drain barriers."""
        try:
            self.tick(run, now)
            with self.transaction():
                s = self.state(run)
                require(s["phase"] not in ("ABORT", "CLOSE") and not s["drained"],
                        "capture already terminal")
                require(type(event) is dict and set(event) ==
                        {"source", "session", "seq", "time", "kind", "data"}, "event schema")
                require(event["source"] == "SIMULATED" and
                        event["session"] == s["identity"]["session"], "observer restart/source")
                require(type(event["seq"]) is int and event["seq"] == s["source_seq"] + 1,
                        "event loss/order/duplicate")
                require(type(event["time"]) is int and max(s["source_time"], s["capture_start"]) <= event["time"] <= now,
                        "source clock/order")
                kind, data = event["kind"], event["data"]
                require(kind in ("INITIAL", "START", "EXIT", "DRAIN"), "source/consumer/buffer fault")
                if kind == "INITIAL":
                    require(not s["initial"] and s["phase"] == "RESERVE" and
                            type(data) is list, "initial inventory")
                    s["initial"] = True
                    for p in data:
                        self._start(s, p)
                else:
                    require(s["initial"], "missing initial inventory")
                    if kind == "START":
                        self._start(s, data)
                    elif kind == "EXIT":
                        key = process_key(data)
                        require(s["active"].get(key) == data, "unknown/ambiguous exit")
                        del s["active"][key]
                    else:
                        require(s["phase"] == "QUIESCENT" and type(data) is dict and
                                set(data) == {"count", "active", "loss", "consumer_ok", "buffer_ok"},
                                "premature drain")
                        require(type(data["count"]) is int and data["count"] == s["source_seq"] and
                                type(data["loss"]) is int and data["loss"] == 0 and
                                data["consumer_ok"] is True and data["buffer_ok"] is True and
                                canonical(data["active"]) == canonical(s["active"]), "drain mismatch/fault")
                        require(not set(s["descendants"]) & set(s["active"]), "residual process")
                        s["drained"] = True
                raw = canonical(event)
                s["source_seq"] += 1
                s["source_head"] = digest({"previous": s["source_head"], "event": event})
                s["source_time"] = event["time"]
                self.db.execute("INSERT INTO observations VALUES(?,?,?)",
                                (run, s["source_seq"], raw))
                self._save(run, s)
        except Exception:
            self.abort(run, "observation lost or invalid")
            raise

    @staticmethod
    def _start(s, p):
        key = process_key(p)
        require(key not in s["seen"] and
                all(q["pid"] != p["pid"] for q in s["active"].values()), "PID reuse/duplicate")
        parent = p["parent"]
        parent_key = None if parent is None else str(parent[0]) + ":" + str(parent[1])
        require(parent_key is None or parent_key in s["active"], "unknown/inactive ancestry")
        launch = s["identity"]["launch"]
        if (p["pid"], p["start_ticks"]) == (launch["pid"], launch["start_ticks"]) or (
                parent_key in s["descendants"]):
            require(s["phase"] != "QUIESCENT", "descendant after quiescence")
            s["descendants"].append(key)
        s["seen"].append(key)
        s["active"][key] = p

    def _verify(self, raw):
        r = parse(raw)
        require(type(r) is dict and set(r) == {"body", "sha256", "signature"}, "envelope")
        b = r["body"]
        require(type(b) is dict and set(b) == BODY_FIELDS and
                b["schema"] == SCHEMA and b["simulation"] is True, "completion schema")
        require(b["kind"] in ROLES and b["signer"] == ROLES[b["kind"]], "kind/principal")
        public, key_id = self.keys[b["signer"]]
        require(b["key_id"] == key_id and r["sha256"] == digest(b), "hash/key")
        verify_signature(r, public)
        identity_valid(b["identity"])
        require(all(type(b[f]) is int for f in ("time", "deadline", "sequence")) and
                type(b["payload"]) is dict, "receipt types")
        return r, b

    def accept(self, raw, now):
        r, b = self._verify(raw)
        run, kind = b["identity"]["run_id"], b["kind"]
        require(kind != "STATUS", "STATUS requires terminal consumption")
        require(type(now) is int and now - 10 <= b["time"] <= now, "receipt freshness")
        if kind != "RESERVE":
            self.tick(run, now)
        with self.transaction():
            if kind == "RESERVE":
                require(not self.db.execute("SELECT 1 FROM runs WHERE id=? OR nonce=?",
                        (run, b["identity"]["nonce"])).fetchone(), "reservation replay")
                states = [parse(row[0]) for row in self.db.execute("SELECT state FROM runs")]
                require(all(s["phase"] in ("ABORT", "CLOSE") for s in states), "active reservation")
                require(b["deadline"] == b["time"] + 30 and now <= b["deadline"], "launch window")
                s = blank(b["identity"], b["time"])
                self.db.execute("INSERT INTO runs VALUES(?,?,?)",
                                (run, b["identity"]["nonce"], canonical(s)))
            else:
                s = self.state(run)
            require(s["phase"] not in ("ABORT", "CLOSE") and
                    s["identity"] == b["identity"], "identity/terminal")
            require(b["sequence"] == s["seq"] + 1 and b["previous"] == s["head"] and
                    b["time"] >= max(s["stamp"], s["source_time"]) and now >= s["clock"] and b["deadline"] == s["deadline"], "chain/clock")
            if kind in PREFIX:
                require(s["seq"] == PREFIX.index(kind), "phase/replay")
                expected = {}
                if kind == "BEGIN":
                    require(s["initial"], "initial source missing")
                    expected = {"source": "SIMULATED", "source_head": s["source_head"]}
                    s["begin"] = r["sha256"]
                if kind == "EXEC_ACK":
                    s["ack"] = r["sha256"]
                    s["last_health"] = now
            elif kind in ("HEALTH", "QUIESCENT"):
                pending = s["pending"]
                require(s["ack"] is not None and pending is not None and
                        pending["kind"] == kind and pending["issued"] <= b["time"] <=
                        now <= pending["expires"], "challenge freshness/phase")
                expected = dict(exec_ack=s["ack"], challenge=pending["token"],
                                source="SIMULATED", source_head=s["source_head"],
                                counter=s["health"] + 1)
                require(s["phase"] in ("EXEC_ACK", "HEALTH"), "post-quiescent heartbeat")
                if kind == "HEALTH":
                    s["health"] += 1
                    s["last_health"] = now
                else:
                    require(s["health"] > 0 and s["descendants"] and
                            not set(s["active"]) & set(s["descendants"]), "premature/residual quiescence")
                    expected["descendants"] = sorted(s["descendants"])
                    expected["active_descendants"] = []
                    s["quiescent"] = r["sha256"]
                s["pending"] = None
            else:
                require(s["phase"] == "QUIESCENT" and s["drained"], "quiescence/drain required")
                expected = dict(exec_ack=s["ack"], begin=s["begin"], quiescent=s["quiescent"],
                                source="SIMULATED", source_head=s["source_head"],
                                event_count=s["source_seq"], record_count=s["seq"] + 1)
            require(canonical(b["payload"]) == canonical(expected), "receipt payload")
            s.update(phase=kind, seq=b["sequence"], head=r["sha256"], stamp=b["time"])
            self.db.execute("INSERT INTO receipts VALUES(?,?,?)", (run, s["seq"], raw))
            self._save(run, s)
        return r["sha256"]

    def terminal_challenge(self, run, now, token):
        self.tick(run, now)
        with self.transaction():
            s = self.state(run)
            require(s["phase"] == "CLOSE" and not s["consumed"] and
                    s["terminal_pending"] is None, "terminal challenge phase")
            require(type(token) is str and len(token) == 64 and
                    all(c in "0123456789abcdef" for c in token) and
                    token not in s["used_challenges"] and token != s["identity"]["nonce"],
                    "fresh terminal challenge")
            s["used_challenges"].append(token)
            s["terminal_pending"] = dict(token=token, issued=now, expires=now+5)
            self._save(run, s)

    def consume_terminal(self, run, raw, now):
        self.tick(run, now)
        r, b = self._verify(raw)
        with self.transaction():
            s = self.state(run)
            pending = s["terminal_pending"]
            require(s["phase"] == "CLOSE" and not s["consumed"] and pending is not None,
                    "no unconsumed terminal")
            require(b["kind"] == "STATUS" and b["identity"] == s["identity"] and
                    b["previous"] == s["head"] and b["sequence"] == s["seq"]+1 and
                    b["deadline"] == s["deadline"] and
                    pending["issued"] <= b["time"] <= now <= pending["expires"],
                    "terminal status binding/freshness")
            expected = dict(close=s["head"], challenge=pending["token"])
            require(canonical(b["payload"]) == canonical(expected), "terminal status payload")
            s["consumed"] = True
            s["terminal_pending"] = None
            s["terminal_status"] = raw.decode()
            self._save(run, s)

    def records(self, run):
        return [r[0] for r in self.db.execute(
            "SELECT raw FROM receipts WHERE run=? ORDER BY seq", (run,))]

    def integrity(self, run):
        s = self.state(run)
        records = self.records(run)
        require(len(records) == s["seq"] and bool(records), "truncated custody")
        head = ZERO
        for seq, raw in enumerate(records, 1):
            r, b = self._verify(raw)
            require(b["identity"] == s["identity"] and b["sequence"] == seq and
                    b["previous"] == head, "retained receipt chain")
            head = r["sha256"]
        require(head == s["head"], "custody head")
        head = ZERO
        events = self.db.execute(
            "SELECT seq,raw FROM observations WHERE run=? ORDER BY seq", (run,)).fetchall()
        require(len(events) == s["source_seq"], "truncated observation custody")
        for seq, (stored_seq, raw) in enumerate(events, 1):
            e = parse(raw)
            require(seq == stored_seq == e["seq"], "observation gap")
            head = digest({"previous": head, "event": e})
        require(head == s["source_head"], "observation head")
        return s


class DualCustody:
    """Fail-closed logical replication; deliberately no ambiguous ACK retry.

    Crash injection models interruption at durable boundaries. Recovery never
    resumes an active run; new RUN_ID/nonce required after retained ABORT.
    """
    def __init__(self, w, d):
        require(w.role == "W" and d.role == "D" and
                w.path.resolve() != d.path.resolve(), "separate logical ledgers")
        self.ledgers = (w, d)
        # Reopening cannot bypass recovery by calling challenge/accept directly.
        # Only mutually retained CLOSED state survives construction of a pair.
        runs = {row[0] for ledger in self.ledgers
                for row in ledger.db.execute("SELECT id FROM runs")}
        for run in runs:
            try:
                state = self.check(run)
                if state["phase"] != "CLOSE":
                    self.abort(run, "observer/custodian restart")
            except (Rejected, sqlite3.Error):
                self.abort(run, "ambiguous custody at startup")

    def abort(self, run, reason):
        for ledger in self.ledgers:
            try:
                ledger.abort(run, reason)
            except (Rejected, sqlite3.Error):
                pass  # No missing authority reconstructed. Other side stays revoked.

    def check(self, run):
        try:
            for ledger in self.ledgers:
                ledger.present()
            a, b = (l.integrity(run) for l in self.ledgers)
            require(a == b and self.ledgers[0].records(run) == self.ledgers[1].records(run),
                    "W/D divergence")
            require(a["phase"] != "ABORT", "durable ABORT")
            return a
        except Exception:
            self.abort(run, "custody missing/diverged/corrupt")
            raise

    def recover(self, run):
        s = self.check(run)
        if s["phase"] != "CLOSE":
            self.abort(run, "observer/custodian restart")
            raise Rejected("active recovery is ambiguous")
        return self.audit(run, self.ledgers[0].records(run))

    def accept(self, raw, now, crash=None):
        run = parse(raw)["body"]["identity"]["run_id"]
        require(crash in (None, "before_W", "after_W", "after_D"), "crash point")
        if crash == "before_W":
            raise InterruptedError(crash)
        try:
            if parse(raw)["body"]["kind"] != "RESERVE":
                self.check(run)
            self.ledgers[0].accept(raw, now)
            if crash == "after_W":
                raise InterruptedError(crash)
            self.ledgers[1].accept(raw, now)
            if crash == "after_D":
                raise InterruptedError(crash)
            self.check(run)
        except InterruptedError:
            raise
        except Exception:
            self.abort(run, "invalid receipt/replication")
            raise

    def challenge(self, run, kind, now):
        self.check(run)
        token = secrets.token_hex(32)
        try:
            for ledger in self.ledgers:
                ledger.challenge(run, kind, now, token)
            return token
        except Exception:
            self.abort(run, "challenge failure")
            raise

    def tick(self, run, now):
        self.check(run)
        try:
            for ledger in self.ledgers:
                ledger.tick(run, now)
        except Exception:
            self.abort(run, "heartbeat/clock failure")
            raise

    def observe(self, run, event, now):
        self.check(run)
        try:
            for ledger in self.ledgers:
                ledger.observe(run, event, now)
        except Exception:
            self.abort(run, "observation failure")
            raise

    def terminal_challenge(self, run, now):
        self.check(run)
        token = secrets.token_hex(32)
        try:
            for ledger in self.ledgers:
                ledger.terminal_challenge(run, now, token)
            return token
        except Exception:
            self.abort(run, "terminal challenge failure")
            raise

    def consume_terminal(self, run, replica, raw, now):
        result = self.audit(run, replica)
        try:
            for ledger in self.ledgers:
                ledger.consume_terminal(run, raw, now)
            self.check(run)
            return result
        except Exception:
            self.abort(run, "terminal consumption failure")
            raise

    def audit(self, run, replica):
        s = self.check(run)
        require(s["phase"] == "CLOSE" and s["drained"] and
                replica == self.ledgers[0].records(run), "terminal completeness")
        return dict(status="SIMULATED_PROTOCOL_VALID", simulation=True,
                    execution_authorized=False, gates={g: "OPEN" for g in
                    ("G1", "G2", "G3", "G4", "G5")})


class CompletionFixture:
    """Receipt builder, never operational authority. Event inputs are explicit."""
    def __init__(self, custody, keys, identity):
        self.custody, self.keys, self.identity = custody, keys, identity

    def record(self, kind, now, **payload_changes):
        try:
            s = self.custody.ledgers[0].state(self.identity["run_id"])
        except Rejected:
            s = blank(self.identity, now)
        payload = {}
        if kind == "BEGIN":
            payload = dict(source="SIMULATED", source_head=s["source_head"])
        elif kind in ("HEALTH", "QUIESCENT"):
            payload = dict(exec_ack=s["ack"], challenge=s["pending"]["token"],
                           source="SIMULATED", source_head=s["source_head"],
                           counter=s["health"] + 1)
            if kind == "QUIESCENT":
                payload.update(descendants=sorted(s["descendants"]), active_descendants=[])
        elif kind == "CLOSE":
            payload = dict(exec_ack=s["ack"], begin=s["begin"], quiescent=s["quiescent"],
                           source="SIMULATED", source_head=s["source_head"],
                           event_count=s["source_seq"], record_count=s["seq"] + 1)
        elif kind == "STATUS":
            payload = dict(close=s["head"], challenge=s["terminal_pending"]["token"])
        payload.update(payload_changes)
        signer = ROLES[kind]
        return self.keys[signer].sign(dict(schema=SCHEMA, simulation=True, kind=kind,
            signer=signer, key_id=self.keys[signer].key_id, identity=self.identity,
            sequence=s["seq"] + 1, previous=s["head"], time=now,
            deadline=s["deadline"], payload=payload))

    def event(self, kind, time, data):
        s = self.custody.ledgers[0].state(self.identity["run_id"])
        return dict(source="SIMULATED", session=self.identity["session"],
                    seq=s["source_seq"] + 1, time=time, kind=kind, data=data)
