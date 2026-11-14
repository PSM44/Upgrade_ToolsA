# B1–B5 resolution matrix — rev 3

This is a design-resolution matrix, not operational certification. Full findings are preserved in history/qa_rejected_full.md.
Current review verdict is recorded separately in qa_rev3.md against exact document SHA256.

| Finding / requirement | Correction and dependencies | Adversarial test / expected result | Existing evidence | Operational verdict |
|---|---|---|---|---|
| B1 / continuous process observation | §§1,2.1,4.4: kernel event source, initial reconciliation, loss/drain rules; polling never authorizes | T1: short-lived process, unknown identity, event loss, late delivery → detected or blocked | Official ETW source/counter docs; current token probe only | OPEN G1; capture not started, privileges not provisioned, exact-build completeness unproved |
| B2 / authenticated phase identity | §§3,4.1–4.3: protected reservation, RUN_ID+nonce, pinned manifest, linked receipts and OS-authenticated launch | T2: changed PID/starttime/UID/hash/parameters, expired/replayed ARM, concurrency/residual/crash → block | Actual P2 arm/execute contract inspected; lifecycle defined, no implementation test | OPEN G3 |
| B3 / runtime cannot impersonate witness | §2.2: W/D/R principals, protected parent directories and keys; courier never authority | T3: actual R denies signing, trace control, writes, ptrace/elevation; positive allowed write | Current UID/groups/modes and WSLInterop show no established separation | OPEN G2; no principal/ACL changes authorized |
| B4 / prefix and suffix completeness | §§3,4.4: independent BEGIN anchor, sequence 1..N, signed CLOSE and fresh terminal status, protected D receipt | T4: initial deletion, fresh-tail truncation, substitution/restart/replay/ledger rollback → block | Explicit counterexamples addressed by protocol; complete verifier not implemented | OPEN G4 |
| B5 / supported task activation | §2.3: no-trigger XML, demand start, IgnoreNew, reviewed principal | T5: TASK_VALIDATE_ONLY; later authorized actual activation and identity/ACL negatives | Local COM validation passed, GetTask returned not-found; schtasks help supports XML | Syntax verified; OPEN G5 for operational activation |

No open gate can be waived by QA_PASS on the design. Future provisioning requires exact HUMAN authority; current P2 fallback stays blocking.
