# Offline Host Witness — independent focused QA

Date: 2026-11-12
Reviewer: /root/witness_qa (separate agent with rejection authority)
Verdict: QA_PASS_OFFLINE_BOUNDED
Baseline checkout HEAD: 1f2c010cbf66742e38dbd4445d5c9464f083e77c

## Exact reviewed artifacts

Paths relative to EXPERIMENTS/NORTHSTAR-HERMES-E2E-001/v2:
- src/host_witness_offline.py — SHA256 2a7ec5d701338fa72a724326dd6678e5096b4930bb533d43545a095ff2bc6060
- tests/test_host_witness_offline.py — SHA256 75251c311eebeff1179a52f475fb306567715060c7836567a3556aa40cf3d436
- tools/p2_external.py — SHA256 879c79d523fd9cafde44a8e37fba73b315c899288f34dc8239c228488665e0ce (existing fail-closed runner retained)

This verdict covers a TEST_ONLY offline protocol harness, not implementation of the full accredited rev-3 observer.

## Independent execution

Final reviewed sources were read in full and then tested using Python -B with PYTHONDONTWRITEBYTECODE=1 and TMPDIR=/home/psm/northstar/evidence:

python3 -B -m unittest discover -s tests -p test_host_witness_offline.py -v
Result: 15 tests passed; no skips.

python3 -B -m unittest discover -s tests -p test_p2_external.py -v
Result: 11 tests passed; no skips.

The tests generated disposable TEST P-256 keys and simulated SQLite custody in temporary directories. Temporary fixture directories were cleaned by their context managers. No providers, real observer, ETW, scheduler registration, service, elevated probe or protected production object was used.

Additional reviewer-authored probes, executed without source edits, rejected:
- All six launch-field substitutions under a valid W fixture signature: PID, start ticks, UID, executable hash, argv hash and unit.
- UID values bool, float and null.
- Malformed and null signature encodings.
- A correctly signed D-principal BEGIN where W was required.
- Terminal STATUS substitutions of run ID, nonce, CLOSE digest and replaced old challenge.
- Stale and future STATUS timestamps.
- Reuse of a consumed terminal receipt after closing/reopening custody.

These are 19 additional negative observations, not 19 committed unittest methods. Repeatable audit after consumption still returned execution_authorized=False, as intended. The independent extra probes were run before the final narrow abort-lock repair; the affected abort/restart path was verified in the final suite after repair.

## Finding and repair verified

OFFLINE-QA-01: abort wrote stage=-1 while the active reservation query used stage<6. A retained aborted record therefore prevented all fresh reservations indefinitely. I reproduced abort followed by a new RUN_ID/nonce returning "concurrent reservation" and rejected that intermediate lifecycle behavior to the implementer.

The final query is stage>=0 AND stage<6. The regression now closes/reopens custody, proves the aborted run cannot resume, audit or reserve again, and accepts a fresh RUN_ID/nonce. The tombstone remains retained. This is an availability/lifecycle correction, not a weakened acceptance gate.

An implementer-reported UNC write interruption temporarily emptied the new source file during editing. No verdict is based on that intermediate state. The final restored module was read completely, its hash checked and both focused suites rerun successfully; the relevant semantic repair is present.

## Covered guarantees and interpretation

- Canonical JSON rejects duplicate keys, floats/nonfinite values and noncanonical bytes; integer values above 2^53 round-trip exactly. Strict field sets and domain-separated signatures protect the accepted envelope.
- Fixture keys are P-256, signatures verified by OpenSSL; altered signatures and wrong principal/key are rejected.
- Ordered RESERVE→BEGIN→READY→ARM→EXEC_BIND→EXEC_ACK→CLOSE transitions enforce retained identity, chronology, deadline, previous hash and sequential records. Replay/direct execute/concurrent reservation and failed-transition rollback are exercised.
- Retained simulated custody anchors the BEGIN and CLOSE. Initial/middle/suffix removals, fresh incomplete prefixes and altered replicas are rejected. Empty custody cannot audit a supplied transcript.
- A fresh protected challenge and W-signed STATUS bind one-use terminal consumption to run, nonce and CLOSE. Consumption is transactional and persists across reopen. audit is a repeatable integrity check, not one-use acceptance or host authorization.
- Simulated source faults/forbidden observations/no-drain are rejected; trusted-custodian abort is durable and irreversible for that run.
- The scheduler generator emits only TEST_ONLY no-trigger XML with IgnoreNew, demand start and inert exit arguments. Tests parse XML; they do not demonstrate Windows registration/activation or principal rights.
- OfflineRunnerAdapter rejects all four phases, and the current production-shaped IndependentWitness still blocks. Existing runner tests confirm absent witness cannot create runtime or start the workload.

## Mandatory scope limits

All G1–G5 remain OPEN. NORTHSTAR remains FAIL. No P2_PASS, operational NTV_ROOT PASS or independent security-principal accreditation follows from this result.

This is intentionally a bounded simulation: one 30-second fixture window, seven precomputed fixture stages, caller-supplied identity/time, one SQLite simulated custody authority and static coverage flags. It does not collect process events, measure OS credentials, implement a live heartbeat or trusted QUIESCENT source, reconcile ETL, provision split W/D principals, enforce key ACLs or reproduce two-custodian crash recovery. CLOSE uses the run nonce in the simplified fixture; the fresh independent challenge is exercised in terminal STATUS consumption. This must not be represented as the complete rev-3 control protocol.

The cryptographic tests do not implement the Windows/.NET serializer or production trust-anchor admission. Trusted key-path inputs and intact custody are fixture assumptions, not adversarial OS isolation. The renderer is not the operational Windows observer or provisioning script.

No new blocking defect remains for this explicitly offline scope. Further implementation must preserve simulation separation and complete the missing operational dependencies before any adapter can authorize.

## Independence and mutations

The reviewer was a separate agent with rejection authority, sharing OS/tool permissions with the implementer; this is not a separate security principal. The reviewer made no source edits. Writes were confined to disposable test fixtures and this external report. No Git mutation, network/provider call, ETW/task/service activation, privileged action or protected ROOT/BATON/Kali/UTA006 change was performed by the reviewer.
