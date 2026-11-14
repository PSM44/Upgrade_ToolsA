# NORTHSTAR-P2-OFFLINE-PROTOCOL-COMPLETION — DELTA_ONLY

Date: 2026-11-13 (America/Santiago).
Scope: offline experimental protocol only. NORTHSTAR_FAIL; no P2_PASS.
Status: OFFLINE_PROTOCOL_COMPLETION_QA_PASS (separate exact-hash QA in qa.md).
Final independent run: 33 passed; unchanged legacy/runner regressions: 15 + 11 passed.
Additional reviewer adversarial probes are recorded separately in qa.md.

## 1. Recovered baseline — TOOL_OBSERVED

- Repository PSM44/Upgrade_ToolsA, PR #9 OPEN/DRAFT/unmerged.
- Remote and assigned local HEAD: c6dc2d86f3e1e7c008c34d4a2ce1ec8e64424f67.
- Existing worktree /home/psm/northstar/workspace/Upgrade_ToolsA-offline-completion,
  branch codex/northstar-offline-completion; initial tracked diff and untracked set empty.
- Existing worktree reused. No worktree rebuilt, removed or switched.
- Baseline host_witness_offline.py has seven fixture stages and static CLOSE flags,
  not HEALTH or a separate signed QUIESCENT. Baseline 15 tests reproduced.
- Read-only process inventory observed Codex Windows processes; Debian inspection
  did not establish host absence. No operational absence claim or E2E was attempted.
- Existing report/QA/design/resolution matrix read as REPO_DOCUMENTED historical
  evidence, not proof of current implementation or operational readiness.
- Canonical Windows ROADMAP, MR0, NTV, Effective Execution Skill/BEHAVIOR and local
  adapter read without modification. Effective Execution manually applied.
  Runtime automatic registration/enforcement is UNKNOWN (not listed as an active
  runtime Skill); DSHB discovery NOT VERIFIED. HO.1 was not found by targeted local
  path inventory, so its current content remains UNKNOWN, not reconstructed.

## 2. Exact implementation delta — TOOL_OBSERVED

| File | Change |
|---|---|
| src/host_witness_completion.py | CompletionLedger, DualCustody and explicit CompletionFixture |
| tests/test_host_witness_completion.py | Offline functional, adversarial, crash and integration regressions |
| tools/p2_external.py | verify_witness boundary rejects dictionary verdicts before runtime creation and during subsequent checks |
| evidence/offline_completion/* | This delta, separate QA, test logs, NTV inventory/delta, operational matrix and hashes |

The extension imports the existing canonical JSON, P-256 TEST signature, parser,
identity validation and rejection helpers. Historical seven-stage fixtures and
their tests remain unchanged. Their old SIMULATED_PROTOCOL_VALID result is NOT
completion evidence; completion uses a distinct signed schema.

## 3. HEALTH / QUIESCENT — TOOL_OBSERVED through tests

RESERVE -> BEGIN -> READY -> ARM -> EXEC_BIND -> EXEC_ACK -> HEALTH (repeatable)
-> D-signed QUIESCENT -> source DRAIN -> W-signed CLOSE -> fresh W STATUS consumed once.

Every signed receipt binds the same identity (including RUN_ID, nonce, launch and
session), sequence, prior digest and deadline. HEALTH binds EXEC_ACK, a fresh
one-use challenge, strictly increasing counter and the retained simulated source
head. Challenges expire after 5 simulated seconds; absent HEALTH beyond 15
seconds, clock rollback or source failure persists ABORT in both logical ledgers.
The reservation launch window remains 30 seconds; HEALTH permits a longer
simulated observation interval without changing that immutable launch deadline.

QUIESCENT requires at least one accepted HEALTH and an observed launch/descendant
set with no active descendants. It is signed by D, bound to EXEC_ACK, challenge
and source head. CLOSE requires the subsequent DRAIN barrier, exact final active
inventory, event count, zero loss and healthy consumer/buffer. It references
BEGIN, EXEC_ACK and the signed QUIESCENT. Events delivered after drain/close revoke
the fixture; no synthetic event is labeled ETW.

STATUS preserves the historical one-use terminal guarantee: fresh challenge,
W signature, identity and CLOSE binding, bounded freshness, durable consumption
at both custodians. audit() remains repeatable and never authorizes execution.

## 4. Event model — TOOL_OBSERVED through tests

Explicit SIMULATED INITIAL/START/EXIT/DRAIN events carry session, contiguous
sequence and source time checked against capture start, receive time and prior
source time. The model retains process identity as PID + start_ticks, ancestry,
active set, historical descendant set and an observation hash chain.

Negative cases cover forbidden initial presence, sub-polling lifetime, PID reuse
with stale EXIT, unknown/ambiguous identity, missing/duplicate/out-of-order events,
consumer/buffer failure, observer restart/interruption, late forbidden event
between QUIESCENT and CLOSE, post-drain events, stale/future source time, stale
initial inventory and initial/middle/terminal truncation. A child of an exited
parent is conservatively rejected. No Windows process collector is implemented.
Source time, receive clock and process identities are simulated trusted inputs,
not an operational source of authenticated OS truth.

## 5. W/D recovery — TOOL_OBSERVED through tests

Separate role-pinned SQLite files with FULL synchronous commits and explicit
BEGIN IMMEDIATE before state reads. Both logical custodians retain signed
receipts, source events, high-water state and ABORT tombstones. Files are pinned
by device/inode for the lifetime of open connections; path loss/substitution
cannot authorize from a detached connection.

Crash injection covers before W, after W and after D durable commits at RESERVE,
ARM and EXEC_ACK, including a lost acknowledgment after both commits. Duplicate
ACK, resend/replay, one-sided writes, missing custody, partial SQLite truncation
and W/D divergence fail closed. Constructing a pair with any active or ambiguous
retained state revokes it automatically; callers cannot bypass recovery. Only
matching terminal custody survives reopen. A fresh RUN_ID/nonce may follow ABORT;
the old identity cannot be reused. No authority is rebuilt from replicas.

This is logical failure injection, not actual power-loss/fsync reliability testing.
A process death between two commits is modeled by an interruption at that boundary.
Both ledgers and TEST keys remain accessible to the same OS user. Coordinated
rollback/compromise of both trusted ledgers is outside this offline model.

## 6. Tests and QA — TOOL_OBSERVED / bounded independence

Exact final commands, counts, source hashes and separate reviewer verdict are in
qa.md and tests.txt. Tests use only stdlib, installed OpenSSL, disposable TEST keys
and temporary fixture files. No provider, ETW, Scheduler or autonomous run occurs.
Existing host_witness_offline (15) and p2_external (11) regressions remain intact.

QA rejected intermediate candidates for:
1. Missing persistent clock high-water and future/stale capture chronology.
2. Parent identity accepted after exit / stale quiescence descendant set.
3. Read-before-transaction allowing two concurrent reservations.
4. Reopen followed directly by HEALTH bypassing optional recover().
All were corrected with focused regression evidence. Constructor failure now
closes its SQLite connection. Custody-path replacement is also tested.
An initial test deleted nonexistent observation sequence 7; it was corrected to
delete the actual terminal sequence separately for receipts and observations.
No negative acceptance requirement was weakened.

QA is a distinct agent with rejection authority and independent adversarial
execution, sharing the executor's OS/tool permissions. It is not an independent
security principal, host witness or product accreditation.

## 7. NTV and preserved boundaries

NTV_FUNCTIONAL: bounded offline tests plus separate QA (see exact verdict).
NTV_ROOT: scoped experimental worktree inventory and exact allowlist comparison
in ntv_delta.json; baseline inventory retained for reproduction. Evidence package
content is covered by SHA256SUMS separately to avoid self-referential inventory.
Global P3 NTV_ROOT remains OPEN/UNVERIFIED.

No task writes targeted C:\01. GitHub\Upgrade_TA, CONTINUITY/BATON, Kali,
production Hermes/DSH/OmniRoute, UTA006, provider credentials/accounts or global
Windows/WSL settings. No users, ACLs, operational keys, privileged ETW, services,
Scheduler tasks, provider calls, purchase, Extra Usage change or merge performed.

## 8. Gates — REPO_DOCUMENTED / explicitly not accredited

| Gate | Status | Missing operational evidence |
|---|---|---|
| G1 | OPEN | Real ETW rights, source coverage/loss and final drain |
| G2 | OPEN | Actual W/D/R principals, keys, ACL and access-denial tests |
| G3 | OPEN | Authenticated live launcher, process identity and heartbeat |
| G4 | OPEN | Independently protected real custody, transport and terminal recovery |
| G5 | OPEN | Authorized Scheduler provisioning and activation |
| P3 NTV_ROOT | OPEN | Global independent containment verification |
| P4 | OPEN | Authoritative allowance, Extra Usage OFF and costs |

P2 and NORTHSTAR remain FAIL. Offline QA cannot close any operational gate.

## 9. Publication and next node

Publication is authorized only after QA_PASS_OFFLINE, exact allowlist staging,
sensitive-data review and fresh remote-head check. The resulting commit and PR
head are verifiable in Git/PR #9; they are not predicted inside the commit itself.
SHA256SUMS binds this package; qa.md binds the reviewed implementation.

Next executable node: HUMAN review of operational_authorization.md and exact
provisioning/rollback scope. No host action or provider run follows automatically.
Further operational adapter admission remains blocked until separately authorized,
implemented and independently accredited. No continuity iteration or human relay
is requested.

## 10. Final staging recovery — TOOL_OBSERVED

TASK_ID: NORTHSTAR-P2-OFFLINE-PROTOCOL-COMPLETION-CLOSURE.
Recovered the same ten-file staged increment at the original baseline; no
implementation or test was rebuilt. Final QA already independently bound the
EOF-normalized Python bytes, while SHA256SUMS still described the pre-normalized
files and was stale. Refreshed that manifest only after checking the final QA
hash table, exact EOF-only staged/working difference, unchanged Python AST,
all scoped NTV file hashes, the baseline inventory digest and ten-file allowlist.

Fresh focused checks on these normalized bytes: TEST_ONLY result cannot unlock
runner, 1 test PASS (1.433 s); existing runner regression suite, 11 tests PASS
(0.007 s). These supplement, not retroactively replace, the independent 33-test
run and 26 regression results recorded in qa.md. No executable code changed
during closure. Secret-pattern scan found no private-key blocks, provider-token
patterns or bearer credentials in the allowlisted files.

Publication remains conditional on a matching remote baseline, explicit staging,
limited commit and non-force fast-forward. Exact resulting commit and remotely
verified PR head are delivered in the terminal response.
