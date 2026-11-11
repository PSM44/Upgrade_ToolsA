# P1-RO-SQLITE-VERIFIER — Evidence

- TASK_ID: P1-RO-SQLITE-VERIFIER
- Date: 2026-11-11
- Base commit: 2ea0e47241830558eb6c97402c84ed2f0f411975 (PR #9 head, Draft)
- Executor: Claude Desktop (Fable) under MORCH governance, transitional implementer
- Scope: fix NTV read-only SQLite auditor defect in `EXPERIMENTS/NORTHSTAR-HERMES-E2E-001/v2`

## 1. Defect (reproduced before fix)

`OperationalMemory.__init__` always opened `memory.db` read-write and executed
`executescript(SCHEMA)` (DDL), so the NTV acceptance auditor
(`ntv/ntv_opencode_go_acceptance.py`) mutated the runtime it was auditing.

Reproduction on an isolated copy of runtime `NORTHSTAR_OPENCODE_GO_E2E_004` at base 2ea0e47:

1. Opening `OperationalMemory(copy/memory.db)` succeeded, and on close the WAL
   checkpoint **deleted `memory.db-wal` and `memory.db-shm`** from the copy
   (runtime file set mutated by a supposed auditor).
2. With the copy write-protected (`chmod -w`), the constructor failed:
   `sqlite3.OperationalError: attempt to write a readonly database` —
   the auditor could not audit a write-protected runtime at all.

## 2. Patches applied

Recovered from Codex visualizations directory 2026-11-11
(`.codex/visualizations/2026/11/11/01a1caac-ca4e-7093-942e-cf0fcd403749/`), SHA256:

```
8279b7d812130aaae093d835252f76ca6e363e257096a9a6a0e9b3857b40d575  northstar_readonly_verifier.patch
ce7c1dbb318bc2c6ae552c404a999052ec658107ad67e170b84630643dafa296  northstar_readonly_immutable.patch
```

Effect (3 files, +26/−5):
- `src/operational_memory.py`: new keyword-only `read_only` flag; read-only mode
  opens SQLite via URI `file:<path>?mode=ro&immutable=1` with `uri=True` and
  skips `executescript(SCHEMA)`. Write path unchanged.
- `ntv/ntv_opencode_go_acceptance.py`: auditor opens `memory.db` with `read_only=True`.
- `tests/test_operational_memory.py`: new `test_read_only_mode_verifies_without_writing`.

`immutable=1` is required because `memory.db` is in WAL journal mode: a plain
`mode=ro` connection still creates/updates `-shm`/`-wal` index files. With
`immutable=1` SQLite touches nothing. Constraint recorded: the auditor must only
run against quiescent runtimes (empty/checkpointed WAL), which holds for
post-hoc NTV verification; the verified runtime's `memory.db-wal` is 0 bytes.

## 3. Verification (Debian, isolated copies only; real runtime untouched)

- Focused tests: `tests.test_operational_memory` — **11/11 OK** (includes new test).
- Full suite: `python3 -m unittest discover -s tests` — **56 OK, 2 pre-existing skips**
  (env-gated `NORTHSTAR_SESSION2_RUNTIME` tests).
- Immutability: SHA256 of all 10 runtime-copy files (`control_plane.db`,
  `delegate.db`, `endpoint.json`, `memory.db`, `memory.db-shm`, `memory.db-wal`,
  `model_calls.db`, `transport.db`, `transport.db-shm`, `transport.db-wal`)
  identical pre/post running the patched auditor against the **write-protected**
  copy (`chmod -R a-w`). Key hashes:
  `memory.db f3142b01…d205b78`, `memory.db-wal e3b0c442…b7852b855` (empty),
  full lists in `pre.sha256`/`post.sha256` under
  `/home/psm/northstar/incoming_patches_20261111/ntv_verify_jp1t/`.
- Write rejection: on the read-only handle, writes to all four real tables
  (`operational_memory`, `sqlite_sequence`, `consumer_cursors`,
  `morch_activations`) fail with `OperationalError: attempt to write a readonly
  database`. `verify_chain(NORTHSTAR-GO-E2E-004)` returns `True` read-only.
- Auditor verdict unchanged by this fix: `10/13` checks, `status NORTHSTAR_FAIL`,
  exit 1. The three failing gates are pre-existing and out of P1 scope:
  `authoritative_allowance_and_extra_usage` (BILLING/P4), `full_ntv_root`
  (ROOT/P3), `independent_reviewer_authority` (INDEPENDENCE/QA front).

## 4. Independent QA

Verdict: **QA_PASS** (independent fresh agent session with rejection authority;
re-ran diff inspection, full suite, dual immutability runs and adversarial edge
cases itself; QA artifacts under
`/home/psm/northstar/incoming_patches_20261111/qa_*`).

QA findings (none blocking):
1. **[MEDIUM]** Stale reads with a non-checkpointed WAL, empirically confirmed:
   on a snapshot with a partially checkpointed WAL, `immutable=1` reads the
   pre-WAL state and `verify_chain` returns `True` silently over stale data
   (fully un-checkpointed WAL fails loudly instead). Mitigants: plain `mode=ro`
   was tested and **mutates `memory.db-shm`** in writable directories, so
   `immutable=1` is the only zero-mutation option — the trade-off is inherent
   to the requirement; the real runtime's WAL is 0 bytes (clean close).
   **Recommended follow-up:** in read-only mode, fail or warn when `<db>-wal`
   exists with size > 0 before opening.
2. **[LOW]** URI path lacks percent-encoding: paths containing `?`/`#` silently
   open a different (truncated) database path; `%` fails to open. Project paths
   are controlled. Suggested fix: `urllib.parse.quote(path.as_posix())`.
3. **[INFO]** Nonexistent db in read-only mode fails loudly at open (rw mode
   would have created it) — correct behavior.
4. **[INFO]** No write-mode regression: `read_only=False` path is semantically
   identical to the previous code; full suite green.
5. **[INFO, outside diff]** The auditor's own `rows()` helper uses `mode=ro`
   without `immutable` for `control_plane.db`/`transport.db`: same trade-off;
   candidate to unify with the WAL guard of finding 1.

Independence limitation recorded: QA was executed by a separate fresh agent
session with rejection authority, but on the same account/model family and the
same machine as the implementer. No separately authorized human reviewer
principal signed acceptance; the `independent_reviewer_authority` gate remains
FAIL by design until such a principal exists.

## 5. Delta summary

- Code: minimal fix, 3 files, +26/−5. No architecture, Roadmap, MR0, HO.1 or
  SKILLS changes. No merge; PR #9 remains Draft.
- `C:\01. GitHub\Upgrade_TA`, BATON, Kali and UTA006 untouched.
- Known observation: a diagnostic `pragma journal_mode` probe with plain
  `mode=ro` (no immutable) against the real runtime touched `memory.db-shm`/`-wal`
  mtimes (content unchanged, wal empty) — exactly the defect class this patch
  closes; recorded for transparency.

## 6. Follow-up: non-empty-WAL guard (same day, HUMAN-approved)

QA finding 1 (MEDIUM) addressed: in read-only mode, `OperationalMemory.__init__`
now raises `IntegrityError` before connecting when `<db>-wal` exists with
size > 0, closing the silent-stale-read case (`immutable=1` reading pre-WAL
state). Delta: +8 lines in `src/operational_memory.py`, +16 in
`tests/test_operational_memory.py` (new `test_read_only_refuses_non_empty_wal`),
pure additions.

Verification: suite 57 OK + 2 pre-existing skips; immutability re-verified by
SHA256 pre/post on writable and write-protected runtime copies (identical,
auditor verdict unchanged at 10/13 NORTHSTAR_FAIL); real runtime's 0-byte
`memory.db-wal` exercises the guard's allow path.

Independent QA re-review: **QA_PASS**. QA confirmed by its own probes that the
previously demonstrated stale scenario is now rejected loudly, 0-byte/absent
WAL opens fine, write mode is unaffected, and `stat` on an unreadable WAL still
guards. Non-blocking notes: conservative false positive on a fully
checkpointed but non-truncated WAL (safe direction for an auditor); TOCTOU
check-then-connect race is irrelevant because `immutable=1` already assumes a
quiescent runtime. Still open (unchanged): LOW URI percent-encoding; INFO
auditor `rows()` uses plain `mode=ro` for the other dbs.
