# Offline protocol completion — independent bounded QA

TASK_ID: NORTHSTAR-P2-OFFLINE-PROTOCOL-COMPLETION
Date: 2026-11-13
Reviewer: /root/offline_qa, separate agent with rejection authority
Baseline: c6dc2d86f3e1e7c008c34d4a2ce1ec8e64424f67
Verdict: **OFFLINE_PROTOCOL_COMPLETION_QA_PASS / QA_PASS_OFFLINE**

This verdict applies only to the TEST_ONLY protocol and blocking adapter boundary reviewed below. All G1–G5 remain OPEN; P3 NTV_ROOT and P4 allowance/cost evidence remain OPEN. NORTHSTAR remains FAIL. No P2_PASS or operational accreditation is asserted.

## Exact reviewed artifacts

Paths relative to EXPERIMENTS/NORTHSTAR-HERMES-E2E-001/v2:

| Artifact | SHA-256 |
|---|---|
| src/host_witness_completion.py | c3e43f3fc2c8908aa782ec2fd43ab06e3a236bffcd62c66206264d455f7195a2 |
| tests/test_host_witness_completion.py | 28a3a2d2247e8e7b55b3fd767e5ced8947fc2efc01bedaa2b17f88e8cf084e82 |
| tools/p2_external.py | 6a32fea93bf27c93039651e934b2d7720115fced89fc1415d38200d9b13988bb |
| src/host_witness_offline.py (unchanged dependency) | 2a7ec5d701338fa72a724326dd6678e5096b4930bb533d43545a095ff2bc6060 |

The tested source/test hashes were respectively aebc62c80435e58cea83abe2bacb1713ee7d6fdf1f31a5daa3e5c40cb6ee76f8 and b17efe79d8bef0c925fd53ed295c3d2714367542e57701aaf123e0d7a48ec960, observed unchanged before and after the final completion suite. Precommit whitespace review subsequently removed exactly one extra terminal newline byte from each new Python file. The reviewer independently compared the staged tested bytes with the final working bytes, asserted final_bytes == tested_bytes.rstrip() + newline, and verified identical ast.dump(ast.parse(...)) for both files. The table above records those final normalized bytes. No executable statement or test changed; the final test evidence remains applicable without a redundant suite rerun. The runner hash did not change. The new schema and module extend the existing crypto/canonical-identity implementation. The historical seven-stage harness remains unchanged and is not represented as the completed protocol.

## Independent execution — TOOL_OBSERVED

Commands ran in the assigned Debian worktree's v2 directory with Python bytecode disabled, disposable TEST P-256 keys and SQLite fixtures.

- Final completion suite: `PYTHONDONTWRITEBYTECODE=1 python3 -B -W error::ResourceWarning -m unittest discover -s tests -p test_host_witness_completion.py -v`: **33 tests, 82.239 s, OK, no skips or warnings**.
- Existing offline harness suite: `python3 -B -m unittest discover -s tests -p test_host_witness_offline.py -v`: **15 tests, 1.335 s, OK**.
- Existing runner boundary suite: `python3 -B -m unittest discover -s tests -p test_p2_external.py -v`: **11 tests, 0.013 s, OK**.
- A separate reviewer-authored probe exercised 14 negative cases: clock 110→106; INITIAL timestamps -1, 99 and 1000 against reservation/observation time 100; child start after parent exit; STATUS close/challenge/run/nonce/session/objective substitution; wrong STATUS signing key; double terminal consumption; expired STATUS. All rejected. Valid terminal consumption returned execution_authorized=false.
- After the final recovery fix, a separate direct-reopen probe called prefix→reopen→HEALTH without recover(). HEALTH rejected and both retained phases were ABORT.

The exact final completion output was retained from tool-output chunks at `/home/psm/northstar/evidence/offline_completion/qa-completion-tests.txt` for publication. This is a transcript of observed output, not a new test execution. The additional probes ran from stdin and are described here; their relevant guarantees also have committed regression tests.

## Rejection findings and verified corrections

The reviewer rejected intermediate versions rather than approving developer assertions.

1. **Q1 — Clock rollback:** independently reproduced tick(110) followed by tick(106) succeeding after EXEC_ACK at 105. The correction persists a high-water clock and durably aborts regression. Final suite and separate probe reject it.
2. **Q2 — Impossible capture chronology:** independently produced INITIAL/START/EXIT/DRAIN at source times 1000–1003 followed by a valid CLOSE at protocol time 108. The correction requires an explicit observation receipt time, bounds source times to capture start and receipt time, and prevents receipts preceding accepted source events. Future and stale INITIAL regressions pass.
3. **Q3 — Inactive ancestry / stale quiescence:** source review found starts could name an already-exited parent, and descendant starts could occur after QUIESCENT. Parent identity must now be currently active and post-quiescence descendant creation rejects. Final targeted test and the separate inactive-parent probe pass.
4. **Q4 — Concurrent reservations:** independently synchronized two SQLite connections immediately before reservation INSERT after their reads. Both distinct reservations originally succeeded, violating single-active-run custody. BEGIN IMMEDIATE now covers read-modify-write operations; the deterministic concurrency regression passes.
5. **Q5 — Voluntary recovery bypass:** independently reproduced prefix→reopen→HEALTH accepting HEALTH when recover() was omitted. Pair construction now reconciles retained runs immediately and revokes every active/ambiguous run. Matching CLOSED custody can still be audited. The final direct-reopen probe and regression pass.

Additional review preserved the original fresh signed STATUS/one-use terminal guarantee in the completion path; it is stored in both ledgers and tested for expiry, replay and partial consumption. A constructor-failure SQLite resource warning was observed, reported and corrected by closing the connection on failed initialization; the final strict-warning suite was clean. Implementer-identified ledger-path replacement is now checked using retained device/inode identity and symlink rejection; its final regression passed.

An initial truncation test attempted to delete nonexistent observation sequence 7. This test bug was corrected to delete each table's actual terminal sequence. The truncation intent was preserved rather than weakened.

## Acceptance coverage — TOOL_OBSERVED and code review

- F1: W-signed HEALTH binds full execution identity, nonce, EXEC_ACK, a fresh one-use challenge, monotonically increasing counter and current simulated source head. Challenge expiry, heartbeat loss, identity/key/signature changes, replay and clock rollback reject. Observation/liveness failure leaves durable ABORT.
- F2: D-signed QUIESCENT binds the same execution and source state, observed descendant identities and empty active-descendant set. No HEALTH, residual descendants, premature CLOSE, absent drain, mismatched final inventory or late events block. CLOSE binds BEGIN, EXEC_ACK, QUIESCENT, final source head and exact counts.
- F3: deterministic SIMULATED initial/start/exit/drain inputs cover initial forbidden/unknown/ambiguous identities, sub-polling forbidden starts, reused PID with creation identity, stale exit, dropped/out-of-order/duplicate events, source restart/interruption, consumer/buffer failures and late forbidden delivery. Prefix/middle/terminal transcript and retained-custody truncation fail.
- F4: separate logical W/D files retain signed receipts, observation chains and replay tombstones. Before-W/after-W/after-D interruptions at RESERVE, ARM and EXEC_ACK, ACK loss/duplication/replay, divergent state, missing/replaced/partially truncated custody and reopening active runs fail closed. Matching terminal custody survives reopening; one-use STATUS consumption remains durable.
- Integration: the default IndependentWitness still always blocks. The new verify_witness boundary rejects every dictionary verdict, including a valid offline result relabeled execution_authorized=true. Tests prove no runtime creation or Popen from these results. No CLI admission path for injected test witnesses was added.

## Independence, assumptions and limits

The reviewer independently inspected code, authored distinct probes, reproduced defects, rejected intermediate versions and reran the final suite. Reviewer and implementer share OS/tool permissions; this is logical agent independence, **not** a separate security principal or independent host observer.

All times, process identity/classification, initial inventory and source events are simulated trusted inputs. Source sequences and explicit loss/fault markers test protocol handling; they do not establish ETW completeness. The harness uses conservative rejection for event reordering and uncertain ancestry. SQLite FULL synchronization and injected durable-boundary interruptions test logical crash recovery; no power-cut, real observer termination or filesystem durability accreditation occurred. Simultaneous malicious rollback of both trusted ledgers, compromised custody and administrative/kernel control are outside this offline trust model.

The retained injected-object runner seam still supports existing mocked lifecycle tests; it is not an operational verifier admission path. No real account allowance, provider spending, OS permission separation, key ACLs, ETW capture, Windows Scheduler activation or isolated runtime was tested.

QA writes were limited to disposable test fixtures, the external output transcript and this report. No source/test edits, Git mutation, provider calls, real E2E, host configuration, protected ROOT/BATON/Kali/UTA006 changes were performed by this reviewer. NTV_ROOT/global operational safety and G1–G5 require their separately authorized gates.

No known blocking defect remains for the explicitly bounded offline protocol at the exact hashes above. This report does not certify operational readiness.
