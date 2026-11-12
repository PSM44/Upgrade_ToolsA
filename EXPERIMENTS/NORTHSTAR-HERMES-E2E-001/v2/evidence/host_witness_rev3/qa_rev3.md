# QA rev 3 — round 1

- TASK_ID: NORTHSTAR-P2-HOST-WITNESS-REV3
- Date: 2026-11-12
- Reviewer: /root/witness_qa, separate review agent with rejection authority.
- Verdict: **QA_PASS — STATIC DESIGN ONLY, conditional on mandatory G1–G5 operational evidence before accreditation or use.**
- Reviewed document: host_witness_design.md
- Exact SHA256: 176183ba7ee314a31c4e6b7e6df83144a0cd27d98725a9511543fe3261b3b38a
- P2 baseline: e096b6935f7e0be34186e3de21def4b77ca6f845.
- No operational blocker is declared empirically resolved by this verdict. G1, G2, G3, G4 and G5 remain OPEN. Existing always-blocking adapter must remain in place.
- NORTHSTAR remains FAIL. This report is not permission to provision principals, tasks, keys, ETW sessions or production settings.

## Scope and evidence examined

Read the complete 174-line rev 3, resolution_matrix.md, technical_evidence.md, crypto_probe_results.json, run_crypto_probe.py and historical QA_PASS; confirmed the retained rev 2 hash and the full original rejection hash. Compared the retained full rejection with the original recovered file using cmp: identical. The original rejection, not its shorter publication summary, remains the baseline of this review.

Historical artifacts:
- history/host_witness_design.rev2.md: 9ede222e391dd8239343d305988cccdb811517567e44b9b9543aa36989aef8a7.
- history/qa_rejected_full.md: fe1b862ac242d5e2ae712b512e7dd5fadd214ffa7853e6432329323c77367745.
- history/qa_pass_rev2.md: 19a735e423f45eb98c508756d3b3f973ceb65dc51b86c38f7efed52d9433ca4c.

The hash-bound historical QA_PASS is genuine historical evidence, not approval of rev 3. Its assertion that tail truncation necessarily leaves stale evidence was contradicted by the fresh-prefix counterexample in HW-QA-04; retain both records rather than erasing the earlier verdict.

## Per-finding disposition and attempted invalidation

### B1 / HW-QA-01 — continuous absence versus polling

**DESIGN CORRECTION ACCEPTED; G1 OPEN.** Lines 24–48 and 118–125 remove polling as an authorizing source, require initial inventory plus kernel events, fail on unknown identity/schema and event loss, and demand final closed-ETL drain/reconciliation. Lines 160 and 163 require short-lived and late-event tests.

Counterexample attempted: forbidden process starts and exits between 5-second diagnostic samples. Unlike rev 2, a clean sample pair cannot authorize. A captured event rejects; missing identity or demonstrated loss blocks; inability to establish completeness leaves G1 OPEN. Counterexample attempted at final drain: a late forbidden event before capture stop rejects under line 119 even if quiescence was already reported.

No claim that ETW counters alone establish completeness survives line 44. Exact provider versions, rundown races, event identity payloads, initial/final reconciliation, all-session access and bounded drain must still be demonstrated on the actual Windows build. This is a conditional design obligation, not a tested Windows observer.

### B2 / HW-QA-02 — durable cross-process run identity

**DESIGN CORRECTION ACCEPTED; G3 OPEN.** Lines 93–108 specify a custodian-created reservation before runtime creation, one RUN_ID/nonce, exclusive ownership at both parties and the digest-linked BEGIN→READY→ARM→EXEC_BIND→EXEC_ACK chain. Only reservation references cross arm/execute; execute cannot mint a second identity. Direct execute and substituted or stale launch identities block.

Counterexamples attempted: concurrent B displaces active A; execute generates a new nonce; old residual wins by timestamp; replayed ARM starts another runtime; PID reuse impersonates the original process. Exclusive reservation, replay registry, protected launch channel, one-use transition and PID/starttime/boot/executable/cgroup checks forbid these paths. Crash ambiguity causes abort, not reconstructed authority.

Existing P2 source does not implement these obligations. Future wiring must propagate the protected reservation and preserve fail-closed startup, with crash/ACK and concurrency tests at each transition. A trusted launcher is a direct dependency of the correction, not a claim that current psm is already suitable.

### B3 / HW-QA-03 — runtime access to witness authority

**DESIGN CORRECTION ACCEPTED; G2 OPEN.** Lines 50–71 distinguish W, D and R and explicitly reject same-user mode bits as security separation. Parent-directory protection, signing-use denial, immutable dependencies and R restrictions on sudo, ptrace, host interop and distro-control paths are mandatory. UNC transport is untrusted.

Counterexamples attempted: R replaces the transport stream; invokes the signing key without exporting it; replaces a protected parent; enters WSL/host administration; modifies the verifier's ledger. Replica tampering has no signing/ledger authority. Every privileged route must be denied by real actor tests; if the deployment cannot enforce that denial, line 71 rejects it. The current psm/sudo/WSLInterop observations are therefore evidence of an unestablished boundary, not a waived residual.

No real principal or ACL negative has been executed in this review. Do not convert the proposed matrix into an operational PASS. Protect W/D dependencies and all parent paths, not only leaf files, in future tests.

### B4 / HW-QA-04 — fresh prefix and missing suffix

**DESIGN CORRECTION ACCEPTED; G4 OPEN.** Lines 100–125 pin BEGIN independently, retain durable high-water state, require a fully drained CLOSE with final N/hash/counts and obtain fresh challenge-bound STATUS plus a protected D terminal receipt.

Counterexample attempted: retain the clean post-quiescence prefix and delete a later forbidden record. The prefix cannot supply the required CLOSE/terminal status. A later genuine forbidden event prevents a PASS CLOSE during drain. Forged CLOSE fails signature; cross-run CLOSE fails reservation/nonce/manifest identity; removed initial records fail the pinned BEGIN; rollback fails the protected retained receipt or yields UNVERIFIED.

The protocol explicitly limits partial-tail tolerance to provisional reads and rejects partial/trailing terminal bytes. This closes the original logical false-PASS path conditional on G2. Simultaneous compromise/rollback of both trusted custodians is explicitly outside the model; runtime access to those capabilities is not excluded casually and must fail G2.

No complete protocol implementation or adversarial sequence harness exists in this review. Signed terminal status must never be synthesized from a supplied replica during later audit.

### B5 / HW-QA-05 — unsupported scheduler argument

**DESIGN CORRECTION ACCEPTED; syntax evidence available; G5 OPEN.** Lines 73–78 replace /SC ONDEMAND with supported XML/no-trigger/AllowStartOnDemand behavior, IgnoreNew and an independent run lock. No forced overwrite, registration or execution is permitted now.

The recorded TASK_VALIDATE_ONLY result supports syntax validation. It does not prove W principal/logon/key/network rights, runtime activation or independence. This reviewer did not independently rerun that COM probe. Reviewed documentation and prior local schtasks help corroborate that XML is supported and ONDEMAND is not a schedule type. Actual activation and ACL tests remain mandatory.

## Cryptographic evidence independently checked

Read-only OpenSSL invocations against the supplied public test artifacts produced:
- DER signature, original body: return code 0, Verified OK.
- DER signature, altered domain/body: return code 1, Verification failure.
- P1363 signature passed to the same verifier: return code 1.

All five input-file hashes independently matched crypto_probe_results.json. The body includes domain separation, integer 9007199254740993 and the ASCII escape for ñ. Explicit Rfc3279DerSequence corrects rev 2's encoding ambiguity. The canonical byte check was also reproduced in-memory against Python canonical JSON.

The earlier shell exit-label problem is not used as evidence; actual subprocess return codes are authoritative. These observations prove limited primitive interoperability only. They do not prove authorization, event coverage, the complete JSON parser, state machine, key ACLs or absence of production-key exposure in a deployed system. Only public test-key material/signatures were consumed here; no private key was generated, requested or written by this reviewer.

## Direct dependencies and remaining implementation questions

No new blocking contradiction was found in the static protocol. Detailed implementation must still resolve provider/rundown completion semantics and reliable drain, protected launcher IPC and OS-peer checks, durable journal ordering, one-use acceptance, per-record schema variants and exact failure handling. The design requires these to be tested or fail closed; this review does not assume they already work.

Unknown/inaccessible process identity now blocks rather than implying complete enumeration. Persistent Codex-service regex matches remain deliberate conservative failures. Each drill starts a fresh session, so closing an application cannot erase earlier violations. Browser/extension/renamed-location and WSL-internal assistant exclusions remain explicit stronger-acceptance dependencies, not NorthStar closure.

T1–T6 are concrete test obligations and include the five original counterexamples. Offline verifier/crypto fixtures can be implemented without provider calls; event/principal/scheduler integration remains separately authorized. Future tests must exercise real R actor credentials and a real complete close, not merely construct a valid-looking signed record.

## Verification and independence limits

This was a separate-agent, read-only design review with authority to reject. The reviewer shares OS/tool authority with the implementation agent and is not an independent security principal. Source reads, hashes, byte comparisons and OpenSSL verification were performed; no observer, task, ETW session, principal, ACL, service, model/provider call or broad P1/P2 test suite was started.

Only this report is written by this review. The reviewed design is unmodified and its SHA256 is pinned above. Publication as reviewed design/evidence is appropriate; publication must retain G1–G5 OPEN, the historical rejection and the current operational limitations. Do not describe this as accredited, implemented, NTV_ROOT PASS, P2_PASS or NORTHSTAR_PASS.

**Final disposition: QA_PASS for the rev 3 static fail-closed design at the exact hash above. Proceed only to the authorized design/evidence publication. Accreditation and runtime authorization remain blocked by G1–G5 and the separate account gate.**
