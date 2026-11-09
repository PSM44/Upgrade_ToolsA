# NORTHSTAR DELIVERY — Session 3 final evidence

**Mission:** `NORTHSTAR-DELIVERY-20261209`  
**Objective:** `SESSION3-FINAL-001`  
**Published source commit:** `7f264bf55bcbe835314632f45048ab2cec86ff5b`  
**Binary result:** `NORTHSTAR_FAIL`  
**Production HOLD:** unchanged

## Implemented and executed

- Removed Hermes `--ignore-rules`; each broker call now loads a restrictive `AGENTS.md`, uses Hermes' supported explicit empty toolset, and forces the rules/yolo/hooks bypass environment flags off.
- Added authenticated `MORCH_LOCAL_DELEGATE` HUMAN ingress/egress with HMAC, nonce replay protection and durable SQLite request state.
- Integrated the delegate with the persistent UWS supervisor and used journal `DELETE` for the Windows/WSL-shared control DB.
- Separated role mounts: RORCH owns `app` and `dev` but cannot see/write `qa`; QA reads `app`/`dev` and alone writes `qa`; MORCH is read-only.
- Added authenticated UWS dead-letter redrive with reset retry budget and append-only audit event.
- Added full-root inventory and mechanical acceptance verifiers.

The one authenticated HUMAN objective completed:

`TASK_HANDOFF → DELIVERY → QA_REJECTED → REWORK_RESPONSE → QA_PASS → ACCEPTED → RORCH_ACK`

All seven messages are consumer-ACKed. The delegate returned `ACCEPTED` with `HUMAN_RELAY_COUNT=0`. A controlled supervisor restart retained the objective; a diagnosed retry exhaustion was recovered by `UWS_DEAD_REDRIVE` without a second HUMAN submission.

## Real model receipts

Four Hermes calls completed through provider `custom`, actual model `20261030_RORCH`, with distinct session IDs:

| Stage | Tokens | Cost |
|---|---:|---|
| RORCH build | 1,747 | UNKNOWN |
| QA review 1 | 1,756 | UNKNOWN |
| RORCH fix | 2,034 | UNKNOWN |
| QA review 2 | 1,851 | UNKNOWN |
| **Total** | **7,388** | **UNKNOWN, not USD 0** |

The OpenRouter Nemotron canary failed before inference with `invalid_params` through Hermes. A direct bounded OmniRoute Responses call timed out without response content. No second model identity, tokens or authoritative cost receipt was produced; it is not counted.

## Verification

- Full regression: `50/50 PASS`, two runtime-evidence suites skipped by design; exit `0`.
- Affected security/transport tests: `18/18 PASS`; exit `0`.
- Mechanical control-plane NTV: `12/12 PASS`; exit `0`.
- Session 3 functional acceptance: `5/5 PASS`.
- Full binary acceptance: `5/9`; verifier exit `1`; `NORTHSTAR_FAIL`.
- Original repository full SHA256 manifest: unchanged pre/post; original HEAD remains `e85a0de276cde4ad74bd8466c68f51d6274e689c`.
- Approved-root SQLite `PRAGMA quick_check`: PASS for all databases.
- `NTV_ROOT`: FAIL because Hermes/OmniRoute and the Windows supervisor remain host-scoped outside one verifiable boundary.
- Reviewer independence: UNVERIFIED. A separate verifier process ran, but no independent reviewer authority was available; no self-certification is claimed.

Evidence hashes:

- `session3_root_pre.json`: `74c6ba3f08afbf258d5ee5d1ea434651ee37eb819529ccd4dbd86ed6a601cc06`
- `session3_root_post.json`: `835cd5e06530d8c4f8c5333f8c88f43b01473e5d01c6f799d070943d4a239ede`
- `session3_ntv_root.json`: `7c247f3ecdfd5f10aad502851e5cad00640add3d671f9838ec4c0c1e76b7cd97`
- `session3_acceptance.json`: `d0b7e8435628af537951e5abdb082243adf82b2378ed0746a8d0b3720e36ad3e`
- Checkpoint revision 7: `43e1d85f7de2da43447b0d888ba5154484ee006ecdd8d14b1351c9aa6ab5f78e`

## Exact remaining acceptance failures

1. Two different real underlying models: only `20261030_RORCH` produced valid receipts.
2. Authoritative billing: all calls report `cost_status=unknown`, `cost_source=none`.
3. Full-system isolation: host-scoped Hermes/OmniRoute prevents `NTV_ROOT=PASS`.
4. Independent authority: mechanical evidence exists, but independent reviewer acceptance is not established.
5. Persistence after Codex Desktop termination/logoff was not demonstrated; the user-scoped dispatcher did run across controlled process restarts but no host service was installed.

All Session 3 experimental processes were stopped. The original worktree, BATON and UTA006 HOLD were not modified. Resume validation and dry-run both exit `0`; the exact continuation entrypoint is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\UTA_ISOLATION\NORTHSTAR-HERMES-E2E-001\v2\resume_northstar.ps1 -ValidateOnly
powershell -NoProfile -ExecutionPolicy Bypass -File C:\UTA_ISOLATION\NORTHSTAR-HERMES-E2E-001\v2\resume_northstar.ps1
```

