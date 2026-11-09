# NORTHSTAR-HERMES-CONVERGENCE-002 — Convergence evidence

**Mission status:** `BLOCKED_EXTERNAL_DEPENDENCY`  
**Functional slice:** `NTV_FUNCTIONAL_PASS`  
**Full NorthStar:** not accepted  
**Human relay count:** `0`

## Delivered

The V4 fixture extends the prior real Hermes baseline without changing the original repository or its BATON. It implements a durable authenticated transport, operational memory, a local durable MORCH activation adapter and role-specific execution boundaries while preserving the existing UWS FSM and evidence.

The reproduced flow is:

`TASK_HANDOFF -> DEVELOPMENT -> QA_REJECTED -> REWORK -> QA_PASS -> MORCH_DECISION -> RORCH_ACK -> TERMINAL_COMPLETED`

Implemented controls:

- SQLite outbox/inbox with separate enqueue, claim, consumer ACK/NACK and UWS reconciliation.
- Transactional idempotency, nonce replay protection, leases, expiry recovery, bounded retries, dead-lettering and concurrent single-winner claims.
- HMAC actor capabilities and role-route authorization. Payload text cannot grant authority.
- One actor capability mounted per isolated RPC client; QA receives application code read-only and no state/transport database mount.
- Append-only transport audit chain and verified producer/consumer receipts.
- Hash-chained operational memory for objectives, decisions, development context, results and checkpoints.
- Durable QA_PASS activation consumed by `MORCH_LOCAL_ADAPTER`; this component is explicitly not ChatGPT.
- Config-driven model router with persistent usage/cost records and unknown cost represented as `NULL`, never as zero.

## Reproduction and measured results

- Controlled stop after first `QA_REJECTED`: exit `75`.
- Resume to `TERMINAL_COMPLETED`: exit `0`.
- Terminal idempotent rerun: exit `0`; counts unchanged.
- UWS events: `15`, all event IDs and idempotency keys unique.
- Transport: `7` outbox, `7` inbox, `7` consumer ACKs, `32` append-only audit rows.
- Memory: `10` hash-chained records, `3` checkpoints, chain valid.
- Unit suite: `29/29` pass; final focused transport rerun: `12/12` pass.
- Negative identity probe:
  - QA key presented as MORCH: rejected with `actor capability mismatch`.
  - authenticated QA requesting UWS snapshot: rejected with `operation requires UWS`.
- QA mount probe: evidence write allowed; application write denied; state path absent.
- `HUMAN_RELAY_COUNT=0` after supervisor launch.

## Independent verdicts

- `NTV_FUNCTIONAL = PASS`: independent reviewer reproduced 29/29 tests, verified the exact FSM sequence, HMAC/receipt/audit chains, consumer-owned ACKs, memory recovery, role mounts and both negative identity probes.
- `NTV_ROOT = BOUNDED_PASS`; global `NTV_UNVERIFIED`: actor/QA guests and V4 transport boundaries are verified, but the Windows supervisor, direct WSL transport daemon, Hermes gateway and OmniRoute are outside one full-system guest boundary.
- Multimodel runtime: `BLOCKED_EXTERNAL_DEPENDENCY`. Hermes v0.22.6 has one configured custom route/model (`20261030_RORCH`), OpenRouter is not configured, no fallback is configured and the current guest has no authorized secret-zero credential injection boundary.

The four synthetic actor capability files were hardened after the initial root review: inheritance was removed and each Windows ACL now grants full control only to `PABLOKUMQUAT\\aazcl` and `NT AUTHORITY\\SYSTEM`.

## Model and cost evidence

V4 intentionally replays seven SHA-pinned outputs from the prior real Hermes execution; it does not mislabel replay as new inference. The measured imported baseline contains three model invocations, 38,845 input tokens and 1,009 output tokens. Provider cost remains `UNKNOWN` because no authoritative cost source was returned; the router persists a null cost status.

No second authenticated real model call was available within the existing authorized resources. No credential was copied into the fixture and no new paid provider was activated.

## Exact external dependency

Acceptance requires an already-authorized credential-injection boundary outside the isolated guest which:

1. maps opaque guest credentials to the existing custom OmniRoute route without exposing host `.env`, config, `HERMES_HOME` or gateway socket;
2. exposes a second already-authorized distinct real provider/model route;
3. returns redacted receipts with provider, model, correlation, token usage, cost status and exit status; and
4. prevents direct guest access to the host gateway and provider endpoints.

Without that boundary, two real isolated model routes, real cost receipts and full Hermes/gateway NTV_ROOT cannot be verified. This is why the mission is `BLOCKED_EXTERNAL_DEPENDENCY`, not `NORTHSTAR_E2E_PASS`.

## Key SHA-256

- `src/secure_transport.py`: `055fdbe263d07360cc3e10e1c7e9e5ecf8c911ed782da1077951755d095fb232`
- `src/operational_memory.py`: `2ac024f9f3d1c58737a68aab651f41104bcddfd66fc68c907eb142aee6ab49ba`
- `src/model_router.py`: `afc600f52b6159d68ea8ca609bc328d4f1c12913c60474b7a648d0ce223ed031`
- `src/transport_rpc.py`: `cbc3fe166772afa5d6b8b11b8e7c65ede4e2833e9e3c4842eceaea1980d61b76`
- `src/supervisor_v2.py`: `6a11cb729c8e340022dfb9afebe781e80c26c3ba5393bf9fa4bbeeb83c85de04`
- `evidence/v4_functional_result.json`: `83cb53baf966b54799e2af39a1be8e44e028c5de1226c82269acf37a037ed83f`
- `evidence/identity_boundary_probe.json`: `ac4e35178affa31da8fb68e82ffcff680c381b5152db39ac631a00bae2c689c8`
- `evidence/independent_functional_v4.json`: `451534334c77887d806ab0772aac95b97e717e1d027d6f6b6fb9ec4c69751e45`
- `evidence/independent_root_v4.json`: `3575fb4c7abd5bb1446ac8ec61b369b0da18a7c257ecd7277d6b45e72aeade38`
- `evidence/independent_model_runtime_v2.json`: `cb0ba2e27ed7593ae8dc5ffbe4d2f1f393f69dd88d584897673dd39a8b292953`

The original repository remains on `review/UTA-ROADMAP-V07-BRIDGE-001` at `e85a0de276cde4ad74bd8466c68f51d6274e689c`; its pre-existing dirty state was preserved. The independent original-root manifest remains `d7935f76603df520b7b4ec5aaa899b09abb2bba8da6027ef6eadb78c8f63ce19`.

