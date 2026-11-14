# NORTHSTAR DELIVERY — Session 1

**Product verdict:** `NORTHSTAR_FAIL`  
**Control-plane milestone:** running and restart-verified  
**Mission:** `NORTHSTAR-DELIVERY-20261209`

## Implemented

Session 1 extends UWS V4 with a persistent event-driven dispatcher. An authenticated local MORCH client submits `TASK_HANDOFF` through the existing transport. The daemon detects pending durable messages and starts role-specific `bubblewrap` processes. Each role receives one capability, consumes and ACKs its own delivery, performs its work and emits the next message.

The implemented path is:

`MORCH objective -> RORCH build -> QA_REJECTED -> RORCH rework -> QA_PASS -> MORCH decision -> RORCH_ACK`

The coordinator identity is explicitly `MORCH_LOCAL_CONTROL_PLANE_NOT_CHATGPT`. No claim is made that this ChatGPT conversation was externally activated.

## Runtime evidence

- Four objectives reached `COMPLETED`.
- 28 isolated worker dispatches completed.
- 28 outbox and 28 inbox records are `ACKED`; transport audit has 116 rows.
- `SESSION1-CRASH-001` was stopped after its first RORCH handoff. A new control-plane PID resumed the pending QA delivery and completed all remaining edges with no duplicate message ID.
- `SESSION1-MEMORY-001` wrote objective/checkpoint/result memory. After another real process restart, query `punctuation normalization decision` returned the relevant objective from the reopened database with a valid hash chain.
- The process remained independently running at checkpoint as PID 68312 with a live Unix socket. It is user-scoped and disposable, not a Windows service or production scheduler.

## Verification

- New control-plane tests: 6/6 PASS.
- Complete V2 suite: 50/50 PASS.
- Adversarial verifier: 12/12 PASS covering forged identity, replay, duplicate, invalid ACK, lease expiry, false QA acceptance, database restart/corruption, invalid/valid patch controls and QA filesystem escape.
- Initial absolute-path `unittest` invocation failed with exit 1 and was repaired by using discovery; the implementation tests then passed.
- Model route probe: `MULTIMODEL_UNVERIFIED`; configured evidence contains only the prior single baseline model identity.
- Original protected repository remains at `e85a0de276cde4ad74bd8466c68f51d6274e689c`; no BATON or production UTA006 state was changed.

The adversarial verifier was authored in a separate reviewer context, but its harness required compatibility fixes by the implementation lead after that reviewer exhausted its quota. Its 12/12 result is useful evidence, not a fresh fully independent acceptance decision.

## Checkpoint and recovery

`NORTHSTAR_STATE.json` revision 2 pins source hashes, tests, runtime databases, next action, external dependencies and security restrictions. The canonical entrypoint is:

`powershell -NoProfile -ExecutionPolicy Bypass -File C:\UTA_ISOLATION\NORTHSTAR-HERMES-E2E-001\v2\resume_northstar.ps1 -ValidateOnly`

The guard rejects tampered hashes, reparse escapes, relaxed HUMAN_HOLD, shell commands and duplicate resume while PID 68312 is active. A later session must reconcile the recorded process, update the checkpoint, then use the same entrypoint without `-ValidateOnly` if foreground continuation is required.

## Failed NorthStar gates

The binary product verdict remains FAIL because:

1. the new dispatcher currently drives a deterministic fixture adapter rather than a newly isolated real Hermes RORCH/QA model execution;
2. two real underlying model identities and authoritative provider cost receipts remain unverified;
3. external ChatGPT MORCH activation/authority is unsupported and distinct from the local coordinator;
4. Hermes/OmniRoute, the Windows supervisor and WSL host subprocesses are not covered by a full-system NTV_ROOT boundary; and
5. Session 1 changes have not received a fresh independent acceptance verdict.

No merge, production activation, provider purchase, global service installation or UTA006 gate change was performed.

