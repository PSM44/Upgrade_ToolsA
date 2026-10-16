# Internal Skill — Technology Qualification & Cost Control
Scope: Upgrade_TA internal operations; not a transferable consumer Skill.
1. Identify user problem and Registry candidate IDs. Search alternatives without tunnel vision.
2. Evaluate Radar ring independently from lifecycle status; capture URL/commit/license/dependencies and potential vulnerability, pricing, telemetry and provider cost.
3. Run Arena tests in isolated, pinned environment. Verify actual invocation, persistence, failure and rollback. Prohibit mock-to-production state promotion.
4. Record expected marginal costs, token/network budgets, paid traffic and shutdown controls. Default **new financial commitments US$0**.
5. Crosscheck against current project gates and consumers. Produce DELTA_ONLY verdict: ADOPT/TRIAL/ASSESS/CAUTION, physical state, evidence, open risks, next checks.
6. Approval to install for local trial is not approval to commit, push, mutate other projects or incur paid fees.


## Extended Root Qualification Notes (Merged from UTA019)
# Upgrade_TA Skill — Tool Qualification, Costs, and Installation

## Trigger
Any request to discover, install, update or remove a tool/plugin or to change DSH/OmniRoute routing.

## Non-negotiables
1. Keep HUMAN > MORCH > RORCH > EXE authority intact when evaluating for NightShift; this repo cannot approve changes in another consumer repo.
2. Record exact installed product profile (`DSH_HOME` of `DSH2--`, not a shell default), executable, version, checksum and backup before mutation.
3. Use `GOALS → LOOPS → dependency DAG → MAX_SAFE_PARALLELISM/NTV → CROSS_CHECK → TERMINAL_CONVERGENCE`. Do not let one failed candidate block independent tests.
4. Cost first: source license, provider pricing, metered model tokens, host compute and free-tier limits. USD 0 incremental new commitments by default. Unknown price = no production activation. Prevent auto top-up.
5. Install version-pinned official package with verified provenance. Never use `--force`, unreviewed install pipe (`curl | bash`), global sudo, or automatic background updater in the production profile.
6. Test with synthetic data first, bounded filesystem/network permissions, negative tests and log redaction. Do not leak credentials, actual customer records, or project sources to an unapproved service.
7. `INSTALLED` means package present; `DSH_VISIBLE` means UI entry; `REAL_PASS` requires tool invoked and result checked; `PROVIDER_PASS` requires real authorized provider; `PERSISTENT_PASS` requires restart/recovery; never conflate these.
8. Enforce rollback: configuration snapshot, checksum, canary, teardown; preserve backups outside the repo.
9. Memory is *not authority*: auto-learned facts cannot modify Skills or authorize a HUMAN gate.
10. Separate downstream NightShift ToolsArena evaluation from transversal Upgrade_TA evidence. No cross-repo write without destination authorization.

## Cost guardrails for Task Board / Proactive / PUA
A task board may call a model multiple times for automatic acceptance; Proactive can schedule repeated model invocations; PUA can extend loops. Keep autonomous billable triggers disabled until an explicit per-run/month cap exists and costs are visible. Do not confuse plugin license-free with API-free.

## Output minimum
DELTA_ONLY summary: component/version/source/SHA, `PRE/POST`, actual functional proof, mode of test (mock/local/provider), net spend, security exceptions, rollback status, next dependency/gate. Do not proliferate docs or evidence files.
