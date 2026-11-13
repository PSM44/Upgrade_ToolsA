# NORTHSTAR-P2-HOST-WITNESS-OFFLINE-IMPLEMENTATION

Baseline: 1f2c010cbf66742e38dbd4445d5c9464f083e77c.
Status: OFFLINE_PROTOCOL_HARNESS; no operational accreditation. NORTHSTAR_FAIL.

## Delivered
src/host_witness_offline.py: canonical JSON with duplicate/type/byte rejection;
OpenSSL P-256 TEST-key DER signing/verification; fixed identity schema;
transactional SQLite simulation ledger; RESERVE/BEGIN/READY/ARM/EXEC_BIND/EXEC_ACK/CLOSE;
retained initial/terminal anchors, exact replica comparison, durable abort/replay
tombstones; fresh signed STATUS challenge and atomic one-use terminal consumption.
SimulatedWindowsWitness emits only SIMULATED records. OfflineRunnerAdapter has
the same verify(runtime,objective,phase) interface and always raises; existing
tools/p2_external.py is unchanged. scheduler_xml renders inert TEST-only demand-start
XML; it does not register or launch anything.

audit() is repeatable integrity checking; consume_terminal() is one-use simulated
acceptance. Both return execution_authorized=false. Losing the custody DB cannot
recover authority from a replica. The DB is a logical fixture, not an OS security boundary.

## Tests and resolution
15 offline unittest methods (multiple negative subcases) pass; 11 existing
test_p2_external regressions pass. Separate reviewer repeats checks and additional
adversarial probes; see qa.md for exact reviewed hashes/verdict.
No broad historical suite rerun because no existing runtime code changed.

QA found aborted runs permanently retained the active lock. Corrected query to
stage>=0 AND stage<6; regression checks abort, reopen, reject old run and accept
fresh RUN_ID/nonce. Replay tombstones remain.
During correction UNC timed out and a non-terminating PowerShell error emptied
the NEW module; restored from retained source through WSL stdin, then reran focused
tests and requested fresh reviewer hashes. No pre-existing file was affected.

T1: simulated forbidden/lost/unknown coverage and polling-only rejection.
T2: phase order, expired windows, identity fields, clock/sequence and concurrency.
T3: logical signer/key isolation and always-blocking runner; actual ACL tests NOT RUN.
T4: all truncated prefixes, missing initial/middle/tail, altered tail, absent custody,
replay, aborted continuity and fresh terminal challenge consumption.
T5: inert XML structure/no triggers/IgnoreNew; actual activation NOT RUN.
T6: DER signatures, altered/wrong-key signatures, canonical JSON and integer >2^53.

Reproduce from v2 with Python stdlib and installed OpenSSL:
TMPDIR=/home/psm/northstar/evidence/HOST_WITNESS_OFFLINE_TMP PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_host_witness_offline.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_p2_external.py -v

Test private keys exist only inside disposable TemporaryDirectory and are removed
at test teardown; never published or operational. Initial developer test invocations
used default Debian /tmp; final and reviewer runs use the experimental evidence TMPDIR.
Signature verification temporarily writes public signatures only.

## Honest implementation boundary
This increment implements a bounded offline protocol harness, not the entire
operational rev-3 observer. A fixture runs in one 30-second authorization window;
invalid/replayed transitions reject rather than implement transport retries.
No live HEALTH heartbeat, real QUIESCENT observation, ETW event decoder/drain,
.NET production serializer, protected launcher IPC, process measurement, real W/D
custodian separation or operating-system scheduler integration is implemented.
Signed simulated counters are test inputs, not proof of event completeness.
Two TEST keys are logically separate, but the executor can access both.
A full trusted two-custodian crash/ACK recovery and operational lease implementation
remain future work; this DB cannot demonstrate those guarantees.

| Gate | Offline result | Operational status |
|---|---|---|
| G1 | Simulated coverage failures rejected | OPEN: ETW rights, events, loss/drain unproved |
| G2 | Key/role and schema boundaries tested | OPEN: real principals/permissions absent |
| G3 | Ordered identity/receipt chain and replay tested | OPEN: real launcher/heartbeat/IPC absent |
| G4 | Anchors, truncation, challenge and one-use state tested | OPEN: independent custodians/drain absent |
| G5 | Inert no-trigger XML generated/tested | OPEN: no task registered or activated |

## Actions and authority
| Next action | HUMAN authorization |
|---|---|
| Further offline protocol/decoder fixtures with TEST keys | Within current experimental scope |
| Create W/D/R accounts; grant ETW rights; change ACL/sudo/interop | Separate exact-scope authorization required |
| Operational keys, task registration/activation, live ETW | Separate authorization required |
| Live provider/account E2E | Not authorized here; account/allowance gates mandatory |
| Merge/deploy/production changes | Not authorized |

Next executable node: offline event-stream model and HEALTH/QUIESCENT plus
two-custodian crash/ACK fixtures, then separately authorized integration.
P2 not passed; P3 full NTV_ROOT unverified; P4 authoritative account/billing gate open.
