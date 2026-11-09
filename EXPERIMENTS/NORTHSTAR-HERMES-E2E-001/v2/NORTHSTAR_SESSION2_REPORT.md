# NORTHSTAR delivery — session 2 evidence

Status: **FAIL** for binary NorthStar acceptance. The persistent real-agent slice passed; full multimodel and full-system NTV_ROOT did not.

## Implemented

- Split transport consumption into authenticated `claim` and consumer-owned `ack`/`nack` operations. Actors ACK only after work and successor-message persistence.
- Added an authenticated, idempotent, secret-zero model broker. RORCH may call `BUILD/FIX`; QA may call `REVIEW1/REVIEW2`; role/stage escalation and wrong capabilities fail closed.
- Wired the persistent UWS dispatcher to real Hermes one-shot calls from separately sandboxed RORCH and QA workers. QA receives the application read-only and its evidence directory writable.
- Added cross-objective memory retrieval without adding another canonical state owner.
- Added configurable real-model backend and durable model-call receipts with actual provider/model/session/tokens. Unknown cost stays null/`unknown`.
- Changed only the model broker journal from WAL to DELETE after a reproduced DrvFS reopen failure. The failed runtime was preserved at `runtime/control_plane_session2`.

## Real execution evidence

- Runtime: `runtime/control_plane_session2b`.
- Objectives: `SESSION2-REAL-HERMES-002` and `SESSION2-RESTART-HERMES-003`; both `COMPLETED/ACCEPTED`, `human_relay_count=0`.
- Each objective executed `TASK_HANDOFF -> DELIVERY -> QA_REJECTED -> REWORK_RESPONSE -> QA_PASS -> ACCEPTED -> RORCH_ACK`.
- Transport: 14 outbox rows, 14 unique message IDs, 14 consumer ACKs; inbox also 14/14 ACKed.
- Model ledger: 8 completed calls, 8 distinct Hermes session IDs, 160,761 recorded tokens. Every actual receipt reports `custom/20261030_RORCH`; distinct underlying models = 1. Every cost is null with `cost_status=unknown`.
- Controlled interruption: supervisor PID 71956 was stopped during `SESSION2-RESTART-HERMES-003:qa-review-1`, together with its exact transport/broker/worker child processes. The same runtime restarted as PID 23888, reclaimed the unacknowledged delivery, completed it on attempt 2 and produced exactly seven unique edges.
- Timeout recovery: long QA calls also caused worker retry; broker request IDs prevented duplicate inference completion and successor messages.
- Closed databases: `control_plane.db`, `transport.db`, `memory.db`, and `model_calls.db` all returned `quick_check=ok`; no WAL/SHM/journal sidecars remained after graceful shutdown.

## Verification

- Session 2 runtime assertions: 5/5 PASS.
- General regression: 47/47 executed PASS; two evidence-only suites skip without their runtime environment variables.
- Existing independent control-plane verifier rerun: 12/12 PASS at `evidence/session2_ntv_controlplane.json`.
- Original repository remained at `e85a0de276cde4ad74bd8466c68f51d6274e689c`; no original-worktree write was performed.
- Checkpoint revision 5 validates with digest `09b5429c579306d771f689b3a8db77a1c5d3525578ea264bb7bb09f6b7cbcbf2`.

## Unmet binary acceptance gates

- Multimodel is **UNVERIFIED**: the only successful real route resolved to one underlying model. Additional authorized OmniRoute attempts failed with provider 502/402 and zero tokens; labels were not counted as model evidence.
- Cost is **UNKNOWN**, not USD 0: Hermes supplied token usage but no authoritative billing value.
- MORCH is the authenticated persistent local coordinator, not this ChatGPT conversation. No supported external ChatGPT activation/listener interface was demonstrated.
- NTV_ROOT remains **UNVERIFIED** for the full system. Actor guests are bounded, but the Windows supervisor, WSL transport/model broker and existing Hermes/OmniRoute gateway execute outside a single independently verified root boundary.
- Session 2 code and runtime evidence are implementer-verified; the existing independent verifier covers primitives, not a new full-system independent acceptance of the live Hermes run.

## Continuation

Safe validation: `powershell -NoProfile -ExecutionPolicy Bypass -File .\resume_northstar.ps1 -ValidateOnly`

Safe foreground continuation after validation: `powershell -NoProfile -ExecutionPolicy Bypass -File .\resume_northstar.ps1`

The resumed command starts only the isolated session 2 control plane with the Hermes backend. It does not start production services or lift HUMAN gates.

