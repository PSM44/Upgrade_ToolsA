# NORTHSTAR-P2-CODEX-CLOSED-E2E — Codex recovery and preparation evidence

- TASK_ID: NORTHSTAR-P2-CODEX-CLOSED-E2E
- Date: 2026-11-11
- Base commit: 0caf01408286e4e023f3ee175f5b5b816043dc08 (PR #9 head, Draft)
- Executor: Claude Desktop (Fable) under MORCH governance, transitional implementer
- Origin of the work: Codex Desktop session interrupted 2026-11-11 by quota
  exhaustion; recovered verbatim from its working tree at
  `/home/psm/northstar/workspace/Upgrade_ToolsA-p2-20261111`
  (branch `codex/northstar-p2-20261111`, same base 0caf014). Nothing was
  discarded; no reset/clean/checkout destructive operations were used.
  The pre-existing untracked `src/opencode_omniroute_proxy.mjs` in the pr9
  checkout was preserved untouched.

## 1. Recovered baseline (TOOL_OBSERVED)

Preflight compared both checkouts: pr9 clean at 0caf014 (only pre-existing
untracked files); p2 checkout at 0caf014 with 4 modified + 7 untracked files,
no relevant processes running, no git worktree links between them.

`config/model_routes_p2.json` exists and matches the Codex report
(schema northstar-model-routes-p2-v1, max_calls=6, max_prompt_chars=24000,
retry_failed_requests=false, fixture retry_schedule_v1, max_rounds=2, the three
external-gate flags). **Discrepancy vs the relayed report:** the file does NOT
contain `max_output_tokens=4096`; it has per-invocation step caps instead
(`max_model_steps_per_editor_invocation=8`, `..._qa_invocation=2`). Classified
REPORTED_ONLY for that one field; the file as found is authoritative.

## 2. Classification of recovered changes

SHA256 of every recovered file as committed:

```
20fd6825738004784ebe627f20bf18abe51dfbab8483751e9bcc90896a767c70  config/model_routes_p2.json
3151d1b5a82341d56d7cec058294ba3843d34ab49979b610196cecb159b59a61  src/p2_editor.py
4a2e8e40d34118be7890ac84bb65ece83a2f9e8021408a66a1bf88759fe3b0fd  src/p2_task.py
c0b246d1e59e38c623baeeb306f9ebdd1d867208d8bad06f6de7ed182a53616d  src/control_actor.py
98d9fe24584d4c02a71e081c3023246eed8bf3c9113238fe92a8d465e385ad39  src/model_broker.py
3e94cac94c40bc3976ecf434b8fd7210104e32e42b0f6887ead74f29b20deb39  src/morch_delegate.py
bdb8e3f168e3ac856efa611b8478596e7d2000ac4337e0c3b27ddc92028d37f8  src/persistent_control_plane.py
f4461b9f04baad6bb2f642e61348b492fed662caebdc7583d2be48b3480f9010  tests/test_p2_external.py
1e91dcc0004a6404b04af6c47ebf15bc50ae8e02629ebb90fa8c5f53be0f1941  tests/test_p2_task.py
879c79d523fd9cafde44a8e37fba73b315c899288f34dc8239c228488665e0ce  tools/p2_external.py
c32b7ad25be7255501db3023ababb48ff92c07ce988246fb5b7f5245a97d1631  tools/p2_external.sh
```

Per-item status:

| Item | Status |
|---|---|
| P2-F1 objective generality (delegate `--fixture-spec`, pinned `fixture_sha256`, replay identity includes fixture; control plane FAILED terminal states) | IMPLEMENTED_VERIFIED (unit + flow tests) |
| P2-F2 real tool execution boundary (`p2_editor.edit`: bwrap namespace, two-file scope, tool-event attestation, before/after hashes, concurrent-drift check) | IMPLEMENTED_UNTESTED against the real OpenCode binary (by design: no billable calls); protocol and policy logic IMPLEMENTED_VERIFIED via mocked tests |
| `p2_task` bounded fixture retry_schedule_v1 (immutable probe, bwrap isolated checks, QA rejection authority, 2-round terminal failure) | IMPLEMENTED_VERIFIED |
| Broker P2 hardening (QA-only call op, no-retry, cost unknown fail-honest, tool-free QA) | IMPLEMENTED_VERIFIED |
| External runner `tools/p2_external.py` (fail-closed witness gates, evidence hash chain, process snapshots, exclusive runtime, quiescence) | IMPLEMENTED_VERIFIED locally; real E2E NOT_STARTED (blocked by design gates) |
| `max_output_tokens=4096` from the Codex report | REPORTED_ONLY (absent from the file) |

## 3. Local validation without billable calls (TOOL_OBSERVED)

- Full suite in the p2 checkout: **79 OK, 2 pre-existing skips** (env-gated).
  Includes all 21 new P2 tests (contract, delegate, flow, external runner,
  isolation negatives). No network or model calls: model RPC is mocked, the
  OpenCode binary is never invoked by tests.
- External runner preflight (read-only): `execution_authorized=false`,
  blockers `HOST_OBSERVER_NOT_CONFIGURED`,
  `EXTRA_USAGE_OFF_NOT_INDEPENDENTLY_VERIFIED`, `REMAINING_ALLOWANCE_UNKNOWN`
  (+ `USER_SYSTEMD_BUS_UNAVAILABLE` in the invoking shell), runtime_created=false.
  The runner refuses to execute or arm without an authenticated independent
  witness; arbitrary JSON cannot stand in for one (tested).
- Secret scan over the 11 files: no credentials, HMAC keys or tokens; the only
  matches are test fixtures that assert secret REDACTION and token-count code.

## 4. Gate status (honest, not simulated)

- Windows host observer: **OPEN**. No accredited boundary observes Windows from
  Debian; Linux /proc snapshots are supplementary only. Recorded as blocker,
  not simulated. (REPO_DOCUMENTED + TOOL_OBSERVED via preflight.)
- Extra Usage OFF / allowance: HUMAN-declared policy; CLI cost=0 is NOT
  treated as certification (broker marks cost unknown when not provider-typed).
  Account attestation remains an open gate. (REPORTED_ONLY → gate OPEN.)
- P3 NTV_ROOT and independent QA authority: unchanged, separate fronts;
  `independent_reviewer_authority` remains FAIL by design.

## 5. Independent QA of the recovered work

Verdict: **QA_PASS** — fit to publish on PR #9 as verifiable P2 preparation.
Independent fresh agent session with rejection authority; it re-ran the full
suite **inside a no-network bwrap namespace (`--unshare-net`): 79 OK**, proving
no billable or network calls are possible from tests; read all 7 new files and
the full diff of the 4 modified ones; re-ran the runner preflight live
(exit 2, `execution_authorized=false`, state root untouched); re-scanned for
secrets.

QA findings (none blocking):
- **[LOW]** `p2_task.handle` falls back to a generic objective when the payload
  omits `objective` (the delegate always includes it): a malformed payload
  would degrade silently instead of failing.
- **[LOW]** `control_actor` applies `safe_objective_id` to ALL objectives,
  including legacy flows — a global hardening beyond P2 scope (existing ids
  conform; suite green).
- **[INFO]** The QA-model prompt reviews against the pinned contract but does
  not include the HUMAN objective text.
- **[INFO]** Enforcement depends on the `opencode run --format json` event
  schema; schema drift fails closed (empty edit events → failure), never open.
- **[INFO]** A denied tool attempt aborts the whole round (aggressive
  fail-closed; with max 2 rounds this can burn a round — acceptable by design).
- **[INFO]** Unused module-level `import math` in `p2_task.py` (cosmetic).

QA confirmed empirically: no bypass found for the two-file tool-target
allowlist (odd relative paths, symlinks, extra files, patch headers including
"Move to" all rejected); `edited:True` requires a completed edit event AND
byte-level before≠after AND exact workspace file set AND no concurrent
delivery drift; replays re-verify artifact hashes; `copy_account` cannot
mutate the real account (byte copies, quiescent-WAL requirement, pre/post
digests); QA-model calls are tool-free by policy and broker enforcement;
receipts stay honest (`actual_provider/model=NULL`, `cost_status=unknown`
when the CLI does not report typed costs).

Independence limitation (unchanged): QA runs as a separate fresh agent session
with rejection authority, same account/model family/machine as the publisher.

## 6. What this commit is and is not

This publishes PREPARATION evidence: recovered implementation, local tests and
fail-closed gating. It is NOT a P2_PASS, NOT acceptance evidence, and no real
E2E has run. Next executable node: configure an accredited external host
observer + account attestation, then run
`tools/p2_external.sh preflight|arm --runtime /home/psm/northstar/state/NORTHSTAR_P2_<fresh>`
from a dedicated systemd user service, with Codex and Claude Desktop closed.
