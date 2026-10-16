# HO.1 — Operational Continuity Contract
## Handoff essentials
Canonical repository name + Windows/WSL paths; branch and observed HEAD; worktree state; runtime launchers and exact profile; versions; evidence provenance; tasks and DAG frontier; risk and cost gates; changes since last verified state; next callable action; restore points.
## Reconstruct in a blank AI
1. Read `BATON/START_HERE.md`, then `BATON/BATON.md` and `BATON/BATON.json`.
2. Verify repo filesystem/HEAD/live runtime rather than trusting the handoff as live truth.
3. Read `HUMAN/NORTH_STAR.md`, `HUMAN/UPGRADE_PLAN.md`, `HUMAN/MR0/MR0.md`, `HUMAN/DICTIONARY/DICTIONARY.md`.
4. Reconcile Registry/Radar/Arena state and current tasks, identify independent fronts.
5. Before mutation: least privilege, backup, allowlist, conflict detection; respect HUMAN gates.
6. Report DELTA_ONLY, halt on evidence conflict rather than inventing PASS.
## Status vocabulary
HUMAN_REPORTED vs TOOL_OBSERVED vs RUNTIME_VERIFIED vs UNVERIFIED; LAB_MOCK_PASS != REAL_PROVIDER_PASS != PRODUCTION_VERIFIED. Never upgrade confidence implicitly.
## BATON update gate
After every material task closure and before a session handoff regenerate BATON status, include UTC timestamp, task lineage, concrete facts and unresolved unknowns. Never embed secrets or credential material.
