"""Offline rev-3 protocol harness. NEVER an accredited operational witness.

All keys, observations, ledgers and receipts in this module are TEST_ONLY.
A successful verification returns SIMULATED_PROTOCOL_VALID, not host authorization.
"""
import base64
import hashlib
import json
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

class Rejected(ValueError):
    pass

def require(condition, message):
    if not condition:
        raise Rejected(message)

def canonical(value):
    def check(v):
        if isinstance(v, dict):
            require(all(type(k) is str for k in v), "string keys required")
            for x in v.values():
                check(x)
        elif isinstance(v, list):
            for x in v:
                check(x)
        else:
            require(type(v) in (str, int, bool, type(None)), "noncanonical type")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

def parse(raw):
    def pairs(items):
        d = {}
        for k, v in items:
            require(k not in d, "duplicate key")
            d[k] = v
        return d
    try:
        obj = json.loads(raw, object_pairs_hook=pairs)
        require(canonical(obj) == raw, "noncanonical bytes")
        return obj
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise Rejected("invalid JSON") from exc

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def signed_bytes(body):
    return b"NS-HOSTWITNESS-REV3:" + body["kind"].encode("ascii") + b"\n" + canonical(body)

def openssl(args, data=None):
    p = subprocess.run(["openssl", *args], input=data, capture_output=True, timeout=10)
    require(p.returncode == 0, "OpenSSL rejected")
    return p.stdout

class TestKey:
    """Ephemeral fixture key only; caller must use a disposable directory."""
    def __init__(self, directory, name):
        self.private = Path(directory) / (name + ".key")
        self.public = Path(directory) / (name + ".pem")
        require(not self.private.exists() and not self.public.exists(), "key path exists")
        openssl(["genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256",
                 "-out", str(self.private)])
        openssl(["pkey", "-in", str(self.private), "-pubout", "-out", str(self.public)])
        self.key_id = hashlib.sha256(self.public.read_bytes()).hexdigest()

    def sign(self, body):
        require(body["simulation"] is True, "test keys cannot sign real evidence")
        sig = openssl(["dgst", "-sha256", "-sign", str(self.private)], signed_bytes(body))
        return canonical({"body": body, "sha256": digest(body),
                          "signature": base64.b64encode(sig).decode()})

def verify_signature(record, public):
    try:
        sig = base64.b64decode(record["signature"], validate=True)
    except (ValueError, TypeError) as exc:
        raise Rejected("invalid signature encoding") from exc
    # Temporary signature is public, not a key. No shell, provider or network.
    with tempfile.TemporaryDirectory(prefix="witness-verify-") as tmp:
        path = Path(tmp) / "signature.der"
        path.write_bytes(sig)
        openssl(["dgst", "-sha256", "-verify", str(public), "-signature", str(path)],
                signed_bytes(record["body"]))

STAGES = ("RESERVE", "BEGIN", "READY", "ARM", "EXEC_BIND", "EXEC_ACK", "CLOSE")
SIGNERS = ("D", "W", "W", "D", "D", "W", "W")
IDENTITY_FIELDS = {"run_id", "nonce", "objective", "runtime", "manifest", "session",
                   "host_boot", "distro_boot", "launch"}
LAUNCH_FIELDS = {"pid", "start_ticks", "uid", "exe_sha256", "argv_sha256", "unit"}
BODY_FIELDS = {"schema", "simulation", "kind", "signer", "key_id", "identity",
               "sequence", "previous", "time", "deadline", "payload"}

def identity_valid(identity):
    require(type(identity) is dict and set(identity) == IDENTITY_FIELDS, "identity schema")
    for field, length in (("run_id", 32), ("nonce", 64), ("manifest", 64)):
        require(type(identity[field]) is str and
                re.fullmatch("[0-9a-f]{" + str(length) + "}", identity[field]), field)
    for field in ("objective", "session", "host_boot", "distro_boot"):
        require(type(identity[field]) is str and bool(identity[field]), field)
    runtime = identity["runtime"]
    require(type(runtime) is str and
            re.fullmatch("/home/psm/northstar/state/NORTHSTAR_P2_[A-Z0-9_]+", runtime),
            "runtime path")
    launch = identity["launch"]
    require(type(launch) is dict and set(launch) == LAUNCH_FIELDS, "launch schema")
    for field in ("pid", "start_ticks", "uid"):
        require(type(launch[field]) is int and launch[field] >= 0, field)
    for field in ("exe_sha256", "argv_sha256"):
        require(type(launch[field]) is str and re.fullmatch("[0-9a-f]{64}", launch[field]), field)
    require(type(launch["unit"]) is str and bool(launch["unit"]), "unit")

class OfflineLedger:
    """Transactional simulated custodian, NOT an OS permission boundary.

    Caller retains this DB outside mutable replicas. Retaining/reopening it
    exercises replay/high-water behavior; loss of it cannot reconstruct authority.
    """
    def __init__(self, path, keys):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(
          id TEXT PRIMARY KEY, nonce TEXT UNIQUE, identity TEXT NOT NULL,
          stage INTEGER NOT NULL, head TEXT NOT NULL, stamp INTEGER NOT NULL,
          deadline INTEGER NOT NULL, begin_anchor TEXT, close_anchor TEXT,
          terminal_challenge TEXT, consumed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events(
          run TEXT, seq INTEGER, raw BLOB NOT NULL, PRIMARY KEY(run,seq));
        """)
        self.keys = {k: (Path(v), hashlib.sha256(Path(v).read_bytes()).hexdigest())
                     for k, v in keys.items()}
        require(set(self.keys) == {"W", "D"}, "two principals required")
        require(self.keys["W"][1] != self.keys["D"][1], "distinct test keys required")

    def close(self):
        self.db.close()

    def accept(self, raw, now):
        r = parse(raw)
        require(type(r) is dict and set(r) == {"body", "sha256", "signature"}, "envelope")
        b = r["body"]
        require(type(b) is dict and set(b) == BODY_FIELDS, "body schema")
        require(b["schema"] == "ns-hostwitness-offline-rev3" and
                b["simulation"] is True, "simulation only")
        require(b["kind"] in STAGES, "stage")
        idx = STAGES.index(b["kind"])
        require(b["signer"] == SIGNERS[idx], "wrong principal")
        key, key_id = self.keys[b["signer"]]
        require(b["key_id"] == key_id and r["sha256"] == digest(b), "hash/key mismatch")
        verify_signature(r, key)
        identity_valid(b["identity"])
        require(type(now) is int and type(b["time"]) is int and
                type(b["deadline"]) is int and type(b["sequence"]) is int, "time/sequence type")
        require(now - 10 <= b["time"] <= now and now <= b["deadline"], "stale/expired")
        require(type(b["payload"]) is dict, "payload")
        i = b["identity"]
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM runs WHERE id=?", (i["run_id"],)).fetchone()
            if idx == 0:
                require(row is None, "replayed reservation")
                require(not self.db.execute("SELECT 1 FROM runs WHERE stage>=0 AND stage<6").fetchone(),
                        "concurrent reservation")
                require(b["sequence"] == 1 and b["previous"] == "0"*64, "genesis")
                require(b["time"] < b["deadline"] <= b["time"] + 30, "launch window")
                require(b["payload"] == {}, "reserve payload")
                self.db.execute("INSERT INTO runs(id,nonce,identity,stage,head,stamp,deadline) "
                                "VALUES(?,?,?,?,?,?,?)",
                                (i["run_id"], i["nonce"], canonical(i).decode(), idx,
                                 r["sha256"], b["time"], b["deadline"]))
            else:
                require(row is not None and row[3] == idx - 1, "phase/replay")
                require(row[2] == canonical(i).decode(), "identity substitution")
                require(b["sequence"] == idx+1 and b["previous"] == row[4], "chain")
                require(b["time"] >= row[5], "clock regression")
                require(b["deadline"] == row[6], "deadline substitution")
                # Offline fixture must finish within the one reserved window.
                # Longer operational leases are intentionally not implemented.
                expected = {}
                if b["kind"] == "BEGIN":
                    expected = {"source": "SIMULATED", "loss": 0, "unknown": 0,
                                "forbidden": 0, "continuous": True}
                elif b["kind"] == "CLOSE":
                    expected = {"begin": row[7], "drained": True, "loss": 0,
                                "unknown": 0, "forbidden": 0, "count": 7,
                                "quiescent": True, "challenge": i["nonce"]}
                require(canonical(b["payload"]) == canonical(expected), "coverage/payload")
                self.db.execute("UPDATE runs SET stage=?,head=?,stamp=? WHERE id=?",
                                (idx,r["sha256"],b["time"],i["run_id"]))
                if b["kind"] == "BEGIN":
                    self.db.execute("UPDATE runs SET begin_anchor=? WHERE id=?",
                                    (r["sha256"],i["run_id"]))
                if b["kind"] == "CLOSE":
                    self.db.execute("UPDATE runs SET close_anchor=? WHERE id=?",
                                    (r["sha256"],i["run_id"]))
            self.db.execute("INSERT INTO events VALUES(?,?,?)",
                            (i["run_id"],b["sequence"],raw))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return r["sha256"]

    def audit(self, run_id, replica):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        require(row is not None and row[3] == 6, "missing terminal custody")
        require(len(replica) == 7, "incomplete sequence")
        stored = [r[0] for r in self.db.execute(
            "SELECT raw FROM events WHERE run=? ORDER BY seq", (run_id,))]
        require(replica == stored, "replica differs from retained anchors/events")
        require(parse(replica[1])["sha256"] == row[7] and
                parse(replica[-1])["sha256"] == row[8], "anchor mismatch")
        return {"status": "SIMULATED_PROTOCOL_VALID", "execution_authorized": False,
                "gates": {g:"OPEN" for g in ("G1","G2","G3","G4","G5")}}


    def abort(self, run_id):
        """Simulated trusted-custodian revocation: durable, never resumable."""
        with self.db:
            row=self.db.execute("SELECT stage FROM runs WHERE id=?",(run_id,)).fetchone()
            require(row is not None and row[0]!=6, "no active run")
            self.db.execute("UPDATE runs SET stage=-1 WHERE id=?",(run_id,))

    def challenge_terminal(self, run_id):
        import secrets
        token = secrets.token_hex(32)
        with self.db:
            row = self.db.execute("SELECT stage,consumed FROM runs WHERE id=?", (run_id,)).fetchone()
            require(row is not None and row[0] == 6 and not row[1], "no unconsumed terminal")
            self.db.execute("UPDATE runs SET terminal_challenge=? WHERE id=?", (token,run_id))
        return token

    def consume_terminal(self, run_id, replica, status_raw, now):
        result = self.audit(run_id, replica)
        r = parse(status_raw)
        require(type(r) is dict and set(r)=={"body","sha256","signature"}, "status envelope")
        b = r["body"]
        require(type(b) is dict and set(b)=={"kind","simulation","run_id","nonce",
                "close","challenge","time","key_id"}, "status schema")
        require(b["kind"]=="STATUS" and b["simulation"] is True, "test status only")
        require(b["key_id"]==self.keys["W"][1] and r["sha256"]==digest(b), "status key/hash")
        verify_signature(r,self.keys["W"][0])
        require(type(now) is int and type(b["time"]) is int and
                now-10<=b["time"]<=now, "status freshness")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row=self.db.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
            require(row is not None and row[3]==6 and not row[10] and row[9] is not None,
                    "terminal consumed or no challenge")
            require(b["run_id"]==run_id and b["nonce"]==row[1] and
                    b["close"]==row[8] and b["challenge"]==row[9], "terminal binding")
            self.db.execute("UPDATE runs SET consumed=1 WHERE id=?",(run_id,))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return result

class SimulatedWindowsWitness:
    """Controlled fixtures, no ETW, process enumeration or Scheduler activation."""
    def __init__(self, keys):
        self.keys = keys

    def transcript(self, identity, start=100):
        identity_valid(identity)
        records, previous, begin = [], "0"*64, None
        for idx, kind in enumerate(STAGES):
            signer = SIGNERS[idx]
            payload = {}
            if kind == "BEGIN":
                payload = dict(source="SIMULATED", loss=0, unknown=0, forbidden=0, continuous=True)
            elif kind == "CLOSE":
                payload = dict(begin=begin, drained=True, loss=0, unknown=0,
                               forbidden=0, count=7, quiescent=True, challenge=identity["nonce"])
            body = dict(schema="ns-hostwitness-offline-rev3", simulation=True, kind=kind,
                        signer=signer,key_id=self.keys[signer].key_id,identity=identity,
                        sequence=idx+1,previous=previous,time=start+idx,deadline=start+30,
                        payload=payload)
            raw = self.keys[signer].sign(body)
            previous = parse(raw)["sha256"]
            if kind == "BEGIN":
                begin = previous
            records.append(raw)
        return records

class OfflineRunnerAdapter:
    """Same verify(runtime, objective, phase) shape; no operational unlock."""
    def __init__(self, blocked_exception=Rejected):
        self.blocked_exception = blocked_exception

    def verify(self, runtime, objective, phase):
        raise self.blocked_exception("SIMULATION_NOT_ACCREDITED;HOST_OBSERVER_NOT_CONFIGURED;"
                                    "ACCOUNT_ATTESTATION_UNVERIFIED")

def scheduler_xml(executable, principal):
    """Render a TEST_ONLY no-trigger task; never register or execute it."""
    import xml.etree.ElementTree as ET
    require(type(executable) is str and bool(executable), "executable required")
    require(type(principal) is str and bool(principal), "principal required")
    task=ET.Element("Task",version="1.2",xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task")
    ET.SubElement(task,"Triggers")
    principals=ET.SubElement(task,"Principals")
    p=ET.SubElement(principals,"Principal",id="TestOnly")
    ET.SubElement(p,"UserId").text=principal
    ET.SubElement(p,"LogonType").text="InteractiveToken"
    ET.SubElement(p,"RunLevel").text="LeastPrivilege"
    settings=ET.SubElement(task,"Settings")
    ET.SubElement(settings,"MultipleInstancesPolicy").text="IgnoreNew"
    ET.SubElement(settings,"AllowStartOnDemand").text="true"
    ET.SubElement(settings,"ExecutionTimeLimit").text="PT30M"
    action=ET.SubElement(ET.SubElement(task,"Actions",Context="TestOnly"),"Exec")
    ET.SubElement(action,"Command").text=executable
    ET.SubElement(action,"Arguments").text="-NoProfile -NonInteractive -Command exit"
    return ET.tostring(task,encoding="unicode")
