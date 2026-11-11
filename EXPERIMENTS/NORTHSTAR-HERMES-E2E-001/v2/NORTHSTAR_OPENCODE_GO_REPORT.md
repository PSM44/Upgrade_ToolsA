# NORTHSTAR OpenCode Go — Debian execution evidence

Task: `NORTHSTAR-HERMES-E2E-001 / NORTHSTAR-GO-E2E-004`

Binary verdict: **NORTHSTAR_FAIL**. The OpenCode Go functional integration passes, but full Windows-host NTV_ROOT, authoritative Go allowance/billing evidence, and separately authorized comprehensive reviewer acceptance remain unproven.

## Preserved baseline

- The original Windows ROOT, BATON, production UTA006 HOLD, Kali global OmniRoute configuration, and Windows global configuration were not modified.
- UWS V4, durable inbox/outbox, authenticated actor capabilities, memory, `MORCH_LOCAL_DELEGATE`, and Session 4 direct OmniRoute/OpenRouter files were extended rather than rebuilt.
- The calculator pilot was not repeated. Its preserved negative/edit/PASS hashes are recorded in `evidence/opencode_calculator_pilot.json`.

## OpenCode Go authentication and canaries

- OpenCode v2.2.7 reports `OpenCode Go ... stored`; no `auth export` was used.
- State files inspected by metadata only are owned by uid 1000 with mode `0600`.
- The catalog exposed 32 `opencode-go/*` model IDs.
- Real headless canaries passed with nonempty JSON outputs:
  - `opencode-go/glm-5.3-flash`: 587 input, 13 output, 35 reasoning tokens.
  - `opencode-go/kimi-k2.7-code`: 2,115 input, 14 output, 69 reasoning tokens.
- Both canary `step_finish` events reported cost 0. OpenCode 2.2.7 JSONL did not expose `opencode.allowance`; this is not treated as an authoritative billing ledger.
- Extra Usage OFF and available credit USD 0 are retained as HUMAN policy evidence, not independently re-read billing state.

## Secret-zero integration

`model_broker.py` now supports an `opencode` runtime. Only the trusted Debian broker can access the user-scoped OpenCode account. RORCH and QA execute in separate bubblewrap guests with cleared environments, one actor capability, role-scoped filesystem mounts, and the authenticated model socket. They do not receive the OpenCode database, API key, OpenCode home, or provider environment variables.

Configured bounds:

- maximum six model calls;
- maximum 12,000 prompt characters;
- RORCH: `opencode-go/glm-5.3-flash`, timeout 180 s;
- QA: `opencode-go/mimo-v2.6-flash`, timeout 300 s;
- transport claim lease: 600 s;
- no Extra Usage activation or external fallback used.

## Real autonomous E2E

The completed objective traversed:

`TASK_HANDOFF → DELIVERY → QA_REJECTED → REWORK_RESPONSE → QA_PASS → ACCEPTED → RORCH_ACK`

Results:

- seven distinct outbox and inbox messages ended `ACKED` with consumer receipts;
- QA round 1 deterministic probe failed and the independent Go QA returned `REJECTED`;
- Go RORCH corrected the code and tests;
- QA round 2 deterministic probe passed;
- an out-of-contract QA verdict was rejected fail-closed;
- restart plus lease recovery incremented `rework-2` to attempt 3 without repeating BUILD or FIX;
- one bounded QA correction call returned `PASS`;
- `MORCH_LOCAL_DELEGATE` returned terminal `ACCEPTED`;
- `HUMAN_RELAY_COUNT=0`;
- memory chain and recovery checkpoint verify.

Five completed Go calls consumed 37,996 measured input/output tokens in aggregate. Every call has a distinct request/session receipt and provider-reported `cost_value=0`; authoritative allowance consumption remains unavailable from this CLI surface.

## Verification

- Full repository experiment suite: 55 tests passed, 2 environment-gated tests skipped.
- Independent read-only verifier: functional gates PASS, 10/13 total checks PASS.
- Intentionally failing product gates:
  - authoritative allowance/Extra Usage state not returned by CLI;
  - Debian WSL plus bwrap does not prove full Windows NTV_ROOT;
  - no separately authorized reviewer principal signed comprehensive acceptance.

Evidence:

- `evidence/opencode_go_canaries.json`
- `evidence/opencode_go_acceptance.json`
- `evidence/opencode_go_verifier.log`
- `evidence/opencode_calculator_pilot.json`
- `NORTHSTAR_OPENCODE_GO_STATE.json`

No merge or production activation is authorized.
