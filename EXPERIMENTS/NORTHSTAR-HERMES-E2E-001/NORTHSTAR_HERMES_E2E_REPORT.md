# NORTHSTAR-HERMES-E2E-001 — Evidence Report

**Status:** `NORTHSTAR_PARTIAL`  
**Task:** `NORTHSTAR-HERMES-E2E-001`  
**Correlation:** `northstar-a56425d7f513c4a20405e199`  
**Original base:** `review/UTA-ROADMAP-V07-BRIDGE-001` @ `e85a0de276cde4ad74bd8466c68f51d6274e689c`  
**Issue:** #3  

## Delivered vertical slice

Hermes v0.22.6 was invoked through its existing configured route (`custom`, model `20261030_RORCH`). A persisted UWS execution completed this real sequence with seven independent Hermes sessions:

`TASK_HANDOFF → RORCH delivery → local test → QA_REJECTED → controlled interruption → recovery → RORCH fix → QA_PASS → MORCH ACCEPTED → RORCH ACK → TERMINAL_COMPLETED`

The fixture application is a Python palindrome library. Round 1 deliberately failed the punctuation-normalization requirement. Independent QA reproduced the defect, RORCH corrected it, and round 2 passed all checks. The supervisor reused the existing UWS models, FSM and SQLite ledger, and added durable stage/message records.

Observed facts:

- 15 UWS events, 7 completed actor stages and 4 message records.
- Seven distinct Hermes session IDs.
- `HUMAN_RELAY_COUNT=0` after supervisor start.
- Controlled stop at `QA_REJECTED` returned exit 75; resume converged to `TERMINAL_COMPLETED`.
- A subsequent idempotent rerun retained exactly 15 events, 7 stages and 4 unique message keys.
- Local application tests: 7/7 pass after correction.
- Independent QA probe: 4/4 cases pass after correction.
- NTV adversarial verifier self-test: 15/15 pass.

## Isolation and preservation

Code materialization, application tests and QA probes ran in WSL2 `bubblewrap 0.11.2` with user/mount/network namespaces, system paths read-only, loopback-only networking, ephemeral `/tmp`, and only the fixture runtime/evidence mounted writable.

The original repository was inventoried before and after with the project NTV verifier. Both inventories have manifest SHA-256 `d7935f76603df520b7b4ec5aaa899b09abb2bba8da6027ef6eadb78c8f63ce19`; serialized inventory SHA-256 is identical at `3df118947d40064a2d3a346a77a9bdd8153515c3917158a40ec935b1dad1d36d`. Added/removed/modified delta is empty; branch, HEAD and pre-existing status were preserved.

The negative guest probe confirmed that the original repository, personal home and `/run/WSL` were not visible, `/etc` was not writable, and only loopback existed.

## Independent decisions

- Fixture functionality: `PASS`.
- `NTV_FUNCTIONAL`: `FAIL`.
  - Message rows are outbox-only and are inserted directly as ACKED; no durable inbox/consumer ACK transition was demonstrated.
  - Transport identity is supervisor-supplied metadata, not independently authenticated.
  - QA used separate processes/sessions but not separately enforced permissions or model routes.
- `NTV_ROOT`: `NTV_ROOT_BOUNDED_PASS` for code/tests; system-wide `NTV_UNVERIFIED` because Hermes and gateway PID 1588 ran outside the bubblewrap guest.
- Multimodel: `UNVERIFIED`. OpenRouter is not configured, no fallback exists, and every real call used `20261030_RORCH` through the custom route.
- Cost: `UNKNOWN`. The runtime emitted 17 calls including auxiliary calls and 123,703 tokens across initial probe plus E2E. It reported estimated USD 0 with `cost_status=unknown`/no cost source, which is not proof of zero billing.

## Key hashes

- `src/northstar_supervisor.py`: `96958cd45b91ee89e167a128db43aa9ec3ab70d1908d2847bf0250a2bb9823b8`
- `src/guest_worker.py`: `800477027ccf2da952300ac30134aa15ab85e3fdd1957cd24d670c8aa88d1cb1`
- corrected `runtime/app/app.py`: `20155b70ce4640c9b4f4b7bc9914bbd1462b8d5a4d747f40450e7940d587bb16`
- functional result: `7a76cfceeee24c3267dea2959aaab1d2c3d064727f4be446376ae5af456eabd3`
- independent functional review: `cd1252666da65e9e09e75642fed5ebce418f3759badd6120f7f0391fff2c59b8`
- independent root review: `6047430ab13bf5ac78f5aa6d6a30decb73797c981d169b9a9c8130235b42a9b0`

## Exit-code evidence

- Hermes minimal correlated probe: `0`.
- Initial unsupported `--native` broker attempt: `2` (`Worker execution is unknown`; no model result accepted).
- First guest local-test attempt: `1` (empty `sys.executable` under clear environment; fixed to `/usr/bin/python3`).
- Controlled interruption after QA rejection: `75`.
- Resumed E2E: `0`.
- Idempotent terminal rerun: `0`.
- NTV self-test first invalid evidence placement: `2`; corrected external placement: `0` with 15/15.

## Readiness

This proves a real, persistent Hermes-driven rejection/correction/acceptance vertical slice and bounded guest isolation. It does **not** satisfy the full NorthStar contract. Production readiness remains `NO` until durable inbox/outbox ACK semantics, transport-authenticated actor identity, independently enforced QA permissions, a verified second model/provider route, and full-system NTV_ROOT are demonstrated.

No original worktree files, BATON content, global configuration, providers, services, commits, merges or production gates were changed by this fixture.

