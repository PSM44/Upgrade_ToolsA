# NS-HOSTWITNESS-V1 — Windows Host Observer design (rev 3)

- TASK_ID: NORTHSTAR-P2-HOST-WITNESS-REV3
- Date: 2026-11-12
- Baseline: PR #9 e096b6935f7e0be34186e3de21def4b77ca6f845.
- Status: DESIGN ONLY; QA outcome is in the hash-bound companion qa_rev3.md.
- Starting rev 2 SHA256: 9ede222e391dd8239343d305988cccdb811517567e44b9b9543aa36989aef8a7.
- Rev 2, its historical QA_PASS and subsequent full QA_REJECTED remain immutable in history/.
- This revision addresses B1–B5 and their direct dependencies only. UWS, RORCH, account attestation and existing P2 source remain unchanged.
- Design acceptance is not accreditation, implementation, P2_PASS, P3_PASS or NORTHSTAR_PASS.
- All G1–G5 below remain OPEN until implementation evidence exists. The current always-blocking IndependentWitness remains in place.

## 0. Environment facts and evidence boundary

The original 2026-11-11 observations are preserved verbatim in history/host_witness_design.rev2.md, not silently refreshed.
Current read-only probes: PowerShell 7.6.9; Windows NT 10.0.26300.0; Python 3.13.5 and OpenSSL 3.5.6 in Debian.
The current Windows token is medium integrity, Administrators is deny-only and Performance Log Users is not listed in token groups. Debian psm is UID 1000, belongs to sudo, owns the state directory (0700); WSLInterop entry is present. No claim is made that sudo or Windows elevation was exercised.
A no-trigger Task Scheduler definition passed TASK_VALIDATE_ONLY (0x1), with demand start enabled and IgnoreNew (2); the test task did not exist afterwards.
No ETW session, key container, principal, task, ACL or security policy was created. No actual event completeness or principal isolation was tested.
See technical_evidence.md for commands, observations and untested claims.

## 1. Purpose, threat model and acceptance

Retain the fixed pre-runtime spool, four witness phases, P-256 signatures, fail-closed runner, external verifier and HUMAN accreditation.
Replace the claim that periodic samples prove continuous absence. Get-Process is diagnostic/reconciliation only and can NEVER authorize the strong host gate.

Target guarantee, conditional on G1–G5: for the declared coverage interval, all kernel process start/end events and initial live processes relevant to standalone Claude/Codex identity are accounted for, with no known loss, unknown identity or unverified interval. This is an OS-trusted event-observation claim, not proof against malicious kernel, administrator, hypervisor or compromised custodian. No blanket claim of all AI use is permitted.

Adversary: audited runtime R, including arbitrary code, spawned descendants and forged requests; writable replicas, stale logs, handshakes, replay, truncation, concurrent attempts and process/PID substitution are untrusted. Trusted computing base: Windows kernel/ETW, separately provisioned Windows witness W, Debian launch/verifier custodian D and its kernel, HUMAN-approved pinned keys/config/binaries and their protected ledgers.
R must have no administrative/elevation path to W or D. If the actual boundary cannot enforce this, G2 stays OPEN and no run is authorized. A same-user process split does not meet this model.

Excluded from the standalone Windows gate: assistants in browsers/extensions, renamed binaries outside the approved identity policy, WSL-internal assistants and hardware/OS compromise. These limitations remain explicit P2/P3 acceptance dependencies; they are not automatically accepted as NorthStar closure. Known install-path matches and process-name matches remain conservative forbidden signals. An unresolved process identity is UNKNOWN and blocks, not an empty result.
Account allowance and Extra Usage OFF remain a separate mandatory witness. This design cannot satisfy that gate.

## 2. Components and permission boundary (B1, B3, B5)

### 2.1 Event source and Windows witness W

Proposed source: a dedicated system ETW logger for kernel process start/end and rundown (EVENT_TRACE_FLAG_PROCESS), QPC clock, real-time consumer plus bounded non-circular ETL on W-owned storage. No Security 4688, WMI polling or periodic scan fallback may authorize. Pin provider GUID, event versions, enable flags and decoder hash in the accreditation manifest. PID comes from event payload, not blindly from the event header; use boot identity + PID + creation identity to prevent PID reuse.

W starts capture BEFORE the initial inventory. Reconcile start/rundown and stop events with a full initial process inventory while buffering events. An unknown schema, incomplete inventory, inaccessible identity, ambiguous lifetime or unexplained disagreement blocks READY. Resolve races using creation identity and QPC event time, never polling cadence. The exact provider/rundown completion behavior on this Windows build must pass G1 tests before accreditation; documentation alone is insufficient.
Short-lived processes are handled from captured start events even if they exit before any scan. If their event data is insufficient to classify them, block rather than infer benignity.

W queries session loss statistics and tracks consumer/parser/write-queue failures. EventsLost, LogBuffersLost or RealTimeBuffersLost nonzero, missing/reset statistics, buffer/parser failures, consumer disconnect, session replacement, clock/boot change, ETL cap/disk exhaustion or inability to reconcile stream and ETL irrevocably ABORT the run. Zero counters alone do not establish completeness: require exact event decoding, capture readiness, final drain, reconciliation and adversarial drills.
No circular overwrite, session reuse after restart or automatic downgrade. Heartbeat is liveness, not evidence of process-event completeness.

ETW requires appropriate rights; NT Kernel Logger specifically requires administrative/LocalSystem authority. Candidate system-logger rights and full process identity access must be measured under W. Current medium token is NOT accredited. No group membership, audit policy or privilege changes are authorized by this design.
G1=EVENT_SOURCE_UNACCREDITED until authorized provisioning and exact-build tests demonstrate all required behavior. If unable, the host gate stays OPEN; implementation can still deliver offline verifier tests.

### 2.2 Principals and effective permission matrix

The names below are proposed roles, not existing accounts. Provisioning requires a separate exact-scope HUMAN authorization.

| Object/action | W: distinct Windows witness principal | D: distinct Debian custodian | R: audited runtime UID(s) | HUMAN administrator |
|---|---|---|---|---|
| Windows event session and private ETL | control/read/write | no direct access | none | provision/audit |
| W CNG signing key | sign, non-exportable | public key only | no read, export or signing use | provision ACL |
| W binary/config/task/anchor ledger | read; append ledger only | public attestations | no modification or ownership | install/pin |
| D key, config, launch ledger and verifier state | pinned D public key only | private use; append state | no access/modification | provision |
| D protected runtime executable/launch descriptors | attest receipts | open/hash/launch pinned code | read-only executable, no replacement | approve manifest |
| Application/runtime work area | no authority | create/observe | scoped read/write | inspect |
| Exchange/spool replica | signed output/request transport | verify before ingest | may corrupt/drop/replay | no trust inferred |
| Control channel | accept only D-signed typed commands | authenticate R OS peer and validate policy | request only; never sign W/D statements | approve objective |

W uses a distinct SID; key ACL denies R's Windows identity including use of the private key, not just export. W image, dependency directory, task definition, config and ledger are non-writable by that identity. Do not use the Codex-bundled pwsh as the accredited executable: choose a separately installed, pinned, protected pwsh path only after availability/ACL proof.
D owns a protected path outside R-owned /home/psm (proposed /var/lib/northstar-witness), with parent directories protected as well. D is the trusted launcher/verifier, not a child whose identity the runtime supplies. R has no sudo, capabilities, ptrace/proc-memory access to D, writable D dependencies, privileged socket, Windows interop, Windows drive access or distro-control path. Existing psm/sudo/WSLInterop does NOT establish these properties.

UNC/9P is an untrusted courier; its default-WSL-user semantics do not create a principal boundary. W retains authoritative records on protected Windows storage and D retains accepted receipts in its protected ledger. The fixed /home/psm/northstar/state/witness/{requests,replicas}/RUN_ID/ location is transport only, outside every runtime. Its 0700 mode is hygiene, not accreditation. Neither newest filename nor newest timestamp selects authority.

Future permission tests must execute as each real R actor (broker, runner and guests), attempting writes/replacement/truncation of all parents/objects, key use, trace control, ptrace and interop/elevation; compare W/D protected hashes and ledgers before/after. Tests require separately authorized principals/fixtures, not protected production files.
G2=PRINCIPAL_BOUNDARY_UNVERIFIED today. If the WSL owner can cross into host/distro administration from R, reject this deployment profile. Do not hide it as same-machine residual or silently replace the platform.

### 2.3 Supported activation

Use a Task Scheduler v2 XML definition with NO triggers, AllowStartOnDemand=true, MultipleInstancesPolicy=IgnoreNew, bounded ExecutionTimeLimit and a protected pinned action. W also enforces a protected global single-run lock; scheduler instance policy is not run ownership.
After separate authorization only: register the reviewed XML via schtasks /Create /TN NorthStar-HostWitness-V1 /XML <approved-xml>, then request schtasks /Run /TN NorthStar-HostWitness-V1. No /SC ONDEMAND. Do not use /F to overwrite a task. Actual principal/logon type, key accessibility and ETW rights must be validated together; S4U/no-password modes must not be assumed to have network access.
The current syntax probe used an interactive current-user definition solely for validation. It does NOT prove operation as W, witness independence or successful on-demand execution.
G5=SCHEDULER_ACTIVATION_UNTESTED; no task may be registered/launched under the present mandate. Scheduled Task parentage alone never authenticates the observer.

## 3. Records, signatures and trust anchors (B2, B4)

Keep canonical JSON: sorted keys, compact separators, ASCII escapes, integers only for times/counters. Reject duplicate keys, floats, unknown required schema/version, extra unsigned fields used by decisions, malformed base64, invalid types and noncanonical bytes. Hash body without sha256/signature; sign domain-separated bytes: ASCII "NS-HOSTWITNESS-REV3:" + record kind + newline + canonical body.
ECDSA P-256/SHA256 uses explicit DSASignatureFormat.Rfc3279DerSequence, SPKI PEM public key; verification via openssl dgst -sha256 -verify. The byte-identical JSON vector includes int64 > 2^53 and a UTF-8/non-ASCII input escaped to ASCII; add positive/negative .NET→OpenSSL signature vectors. Test keys are never accredited keys.

Every signed record binds schema, record_kind, signer/key_id, host/distro/boot identities, W session_id, RUN_ID (128-bit random unique), nonce (256-bit CSPRNG), objective_id, canonical absolute runtime, authorization_manifest_sha256, policy/decoder/executable digests, UTC and QPC monotonic times, sequence and previous_sha256.
Per-run records sequence from 1 exactly; control receipts have their own kind and reference record digests. Distinguish source ETW events (not inherently an application gapless sequence) from W's assigned sequence; assigned numbers alone cannot prove event-source completeness.

The authorization manifest pins allowed executable and script/dependency hashes, UID/SID, argv as an exact non-secret parameter array/hash, working directory, permitted environment keys, route-config digest, maximum duration, one-run policy, event identity rules and W/D keys. It contains no provider credential or raw environment. All paths must be canonical/no symlink or reparse substitution. At launch D opens protected files, validates immutable identity, and prevents TOCTOU replacement; unknown identity blocks.
W CNG non-exportability is defense in depth only; ACLs and G2 tests provide key-use separation.

## 4. Durable lifecycle and complete evidence (B2, B4)

### 4.1 Identity before any runtime exists

D authenticates the HUMAN-authorized objective through the existing approved ingress; a mere file from R is not a grant. D creates RUN_ID/nonce and a signed RESERVATION in its protected durable ledger BEFORE any runtime directory. The manifest and runtime target are bound here. Reject existing runtime, duplicate RUN_ID/nonce, expired objective, inconsistent keys or a second active reservation. Reserve exclusively at both D and W, independent of filenames.
D sends signed RESERVE(RUN_ID, nonce, manifest, challenge, expiration) to W over the untrusted exchange. W verifies D's pinned key, authorization limits and its own ledger, acquires the single-active-run lock, and starts a fresh capture/session. Old requests do not acquire a lock; terminal IDs/nonces remain in a replay registry. Unknown/ambiguous crash state is ABORT, never resumed as healthy.

### 4.2 before_arm → arm → execute

W establishes event readiness and signs BEGIN (sequence=1, previous_sha256=64 zeroes), including initial inventory digest and capture/config identity. Before any READY authority is returned, W stores BEGIN digest, first sequence, RUN_ID/nonce, session identity and a monotonic ledger generation in a separate protected anchor ledger. D verifies and durably records this start anchor.
W then signs READY referencing BEGIN and D's fresh challenge. D's before_arm check requires unexpired READY, both pinned anchors, positive event health and the exact reservation. D signs ARM with READY hash, authorized launch deadline (at most 30 s after READY in W's clock), execution limit (30–1800 s), exact manifest and a fresh one-use launch challenge.

The arm process cannot mint the execute identity. D launches the prepared systemd unit using the existing P2 mechanism under its controlled launcher, passing ONLY reservation ID and ARM receipt reference over a protected FD/channel. Both process entries recover the same durable reservation; they do not create another nonce.
Before R creates runtime or any broker/worker starts, D obtains OS-authenticated process credentials and verifies boot ID, PID plus /proc starttime, UID, executable identity/hash, unit/cgroup identity, argv/config digest and ancestry against its own launch record. PID or self-reported JSON alone is rejected.
D sends EXEC_BIND with these measurements and ARM digest to W. W verifies the same reservation, unused challenge and launch window, signs EXEC_ACK and consumes the one-use transition atomically. D's before_create verifies EXEC_ACK against the same anchor and persists it before allowing runtime creation. Retried identical requests return the same receipt; changed replays abort. Direct execute without this chain blocks. Existing Python adapter wiring will need a reviewed change to carry this state; it is not implemented here.

Identity envelope is transitive: BEGIN→READY→ARM→EXEC_BIND→EXEC_ACK reference the prior digest plus identical RUN_ID/nonce/manifest. W cannot independently observe Linux PIDs, so D's identity observation is an explicit trust dependency, never falsely labeled Windows measurement.
D's launch monitor must retain authority over the protected supervisor image and track unexpected exec/unit/cgroup changes; divergence aborts. R descendants may do only the application edits already authorized by P2; they cannot replace launch components.

### 4.3 Execution, liveness and failure

Each heartbeat references EXEC_ACK and latest W sequence/head; W signs challenge-bound HEALTH carrying event-loss state and monotonically increasing ledger generation. D stores the highest accepted generation and sequence in protected durable state and rejects rollback. No unsigned counter or runtime-provided quiescence is authoritative.
Default heartbeat <=5 s; absence of a fresh signed response for 3 intervals or a reported source fault causes D to stop the controlled unit and retain ABORTED evidence. Provisional execution is revocable; a later event loss or final failure prevents acceptance even if earlier health receipts passed.
W reboot/restart/session replacement, D state loss, uncertain commit/ACK, sleep/resume or monotonic-clock discontinuity aborts the run. Restart requires a NEW RUN_ID/nonce and fresh initial inventory. Residual files are preserved and cannot be selected for new authority. No automatic log cleanup. D/W durable journal updates and receipts are fsynced before acknowledgement; crash between transitions fails closed. Expiry releases ownership only after terminal/abort state is durably recorded at both sides, otherwise administrative recovery is a separate gate.

### 4.4 Terminal closure, first anchor and fresh-prefix defense

D determines complete descendant/unit quiescence through trusted OS observation, not R's reported completion. It signs QUIESCENT with RUN_ID, EXEC_ACK, process-set result and a fresh terminal challenge. W receives it while capture is still running; if D cannot prove quiescence, abort.
W stops its dedicated capture after receiving QUIESCENT, obtains final statistics and fully drains real-time and ETL data. Offline parsing of the closed ETL reconciles all observed process lifetimes, event counts and classifications for the entire interval from capture start to capture stop. Any late-delivered forbidden event whose source time is in that interval rejects, including events after quiescence but before capture stop.
No CLOSE is emitted until the final ETL is stable, drain completion and zero loss are proven, and initial/final inventory reconciliation succeeds. If the selected ETW mode cannot demonstrate this on the target build, G1 remains OPEN. UTC skew <=10 s is only a bounded consistency check; use QPC and protocol causality for interval membership, not cross-host wall-clock equality.

W emits CLOSE as the final sequence N, previous=head(N-1), binding BEGIN digest, N, total record/event counts, final ETL digest, source counters, capture start/stop QPC, D's QUIESCENT/challenge and final verdict. W durably records CLOSE digest in its protected anchor ledger. D verifies the entire sequence 1..N, every signature/hash, original BEGIN anchor, immutable identity, event health and exact CLOSE. It then obtains a fresh signed W STATUS response to a new challenge that references that same terminal ledger entry. D stores an immutable terminal receipt in its independent protected ledger.
Acceptance consumes this receipt once for the reserved run. Offline later audits compare against the retained D receipt, not latest filenames. Lost/inaccessible anchor or receipt means UNVERIFIED, never rebuilding them from supplied logs.

Removing INITIAL records fails the independently pinned BEGIN and sequence=1 check. Removing a middle record fails sequence/hash validation. Removing a fresh SUFFIX or CLOSE fails expected N/CLOSE hash and terminal challenge/status. Substitution/replay fails RUN_ID/nonce/manifest/ledger generation. Even a valid fresh prefix after quiescence is NOT terminal evidence. Ignoring a single partial append is allowed only for provisional live reads; terminal verification permits no partial/trailing data and requires exact closed bytes.
This protects against mutable replica/log delivery and R, conditional on G2. It does not claim resistance to an administrator rolling back BOTH trusted ledgers or stealing W/D signing authority.

## 5. Accreditation procedure (separately authorized, not executed)

1. Approve the threat scope, W/D principals, exact paths, host rights, account gate and test footprint. Preserve all production configuration; no permission inferred from this document.
2. Provision reviewed W/D keys, protected binaries and ledger locations. Pin key fingerprints, policy/binary/decoder hashes and OS/build versions in an approved authority record (no MR0 write in this task).
3. Execute every permission negative as real R principals, proving denied signing and protected-object/control access. Require G2 PASS, not mode-bit inspection alone.
4. With disposable runs, test a known forbidden process present initially, short-lived between old polling ticks, across user sessions, and during final drain. Every relevant forbidden event must reject. Unknown identity must block.
5. Test event loss/disconnect/full-disk/restart/late delivery, complete terminal drain and rundown reconciliation. Require G1 PASS. Any unobservable condition remains a blocker.
6. Test the full identity and anchored closure state machine, including crashes and races (G3/G4). Test actual approved scheduler principal/ACL/action (G5).
7. Only then review the explicit P2 adapter wiring. Keep current always-blocking fallback if any gate or account attestation is missing. QA design approval is not permission to provision.

## 6. Requirement mapping and gate states

See resolution_matrix.md for finding→correction→test→evidence→verdict.
G1 event completeness/permissions, G2 principal isolation, G3 live identity lifecycle, G4 anchored terminal protocol and G5 activated scheduler are ALL OPEN, implementation/accreditation not run.
Design QA may PASS the fail-closed specification with these gates explicitly OPEN, as allowed by the mandate; it must not say operational blockers are empirically solved.
The original strong P2 host requirement is retained, not weakened to polling or same-user isolation. P3 full-system isolation and P4 authoritative account/billing evidence remain unresolved separate gates.

## 7. Residual risks and conservative failures

Administrator/kernel/hypervisor or W/D compromise is outside this profile. Windows/WSL ownership is not excluded casually: prove R cannot reach those powers or reject G2.
Browser/extension/renamed unknown-location and WSL-internal assistants remain outside the standalone Windows identity policy; separate independent controls are required for stronger acceptance. Unknown image/access-denied classification blocks, potentially causing false positives; it is never counted as complete coverage.
Regex false positives (including persistent Codex services) are intentional conservative blocks until policy revision is reviewed. Each accreditation drill uses a fresh run/session; closing the UI cannot erase earlier forbidden evidence.
UNC outage, exhausted spool, replay storm, unavailable custody, time skew or partial receipts block. DoS is allowed to prevent acceptance, never to create PASS.
Process image replacement/rapid PID reuse, event payload redaction and unsupported decoder versions require tests; unsupported cases block.
Private ETL may contain sensitive OS payloads. Keep it W-only, bounded and out of Git; publish sanitized record metadata, hashes and verdicts only. No command lines, raw provider logs, credentials or account keys in published evidence.
W/D authority provisioning is a new authorization gate, not a claimed existing capability. No always-on/logoff/reboot availability claim.

## 8. Implementation and adversarial test plan (no paid calls)

Retain planned tools/host_witness.ps1, tools/provision_host_witness.ps1, src/host_witness_verifier.py, tests/test_host_witness_verifier.py and a reviewed tools/p2_external.py wiring delta. Event collection and D protected ledger/launch mediation are direct B1–B4 dependencies; estimate must be re-evaluated, not the rev-2 "~2 scripts" promise.
No observer is implemented in this design task.

- T1/B1: start/end between 5-second scan ticks, initial existing process, other session, inaccessible identity, PID reuse, out-of-order delivery, decoder mismatch, deliberate queue overflow and counter loss. Expected block or detected forbidden event; polling-only must never authorize.
- T2/B2: fresh valid lifecycle, direct execute, substituted executable/UID/argv/config, recycled PID, expired ARM, duplicate challenge, mismatched nonce, two concurrent reservations, crash at each fsync/ACK boundary, stale residual request and restart. One identity across phases or abort.
- T3/B3: as each actual R principal, attempt all matrix writes, rename/reparse/parent replacement, trace stop, key signing/export, ptrace and interop/elevation. Every protected action denied; test allowed application writes positively. Current psm configuration cannot be treated as test proof.
- T4/B4: remove first/middle/last record, replace initial anchor, truncate to a still-fresh post-quiescence prefix, remove CLOSE, add partial tail, replay complete old run, replace replica, roll back high-water mark, cross-run closure, delay forbidden event until drain, lose either ledger. Every tampering case blocks, valid full closed chain passes with TEST keys.
- T5/B5: validate no-trigger XML without registration (done); after authorization test demand start, pinned identity/action, IgnoreNew plus W/D ownership lock, expiry and task ACL denial. Current syntax check is not an execution test.
- T6: cross-platform canonical bytes and DER-signature verification, int64 >2^53, duplicate keys, altered body/signature/domain/kind, invalid anchor, noncanonical JSON. Offline with ephemeral TEST key; no production keys.
- Retain rev-2 negative coverage for schema, future/stale timestamps, heartbeat gaps, forks, inconsistent forbidden counts, wrong runtime, restarts, missing anchor and after_quiescence short coverage.

Existing PowerShell/.NET, Python stdlib and OpenSSL suffice for offline protocol/crypto fixtures; actual ETW P/Invoke/decoder and protected D launcher are implementation work, not pre-existing tools assumed working. Windows integration/permission tests are gated separately.
Only tests of changed guarantees are required in the next implementation; do not repeat P1/UWS suites without affected code.

## 9. QA provenance

Full original findings: history/qa_rejected_full.md. Historical approval: history/qa_pass_rev2.md. Starting source: history/host_witness_design.rev2.md.
Current reviewer signs off by recording the exact SHA256 in qa_rev3.md. Do not append a verdict to this hashed document after review; publish the companion evidence and manifest. Any material edit requires new hash and focused re-review.
