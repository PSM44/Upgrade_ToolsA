# Continuity State

- Task: `UTA_TASK004_POST_UPGRADE_COLD_START_ROUTING_PLUGINS_CONTINUITY`
- State: `POST_UPGRADE_VALIDATED_FULL_WSL_COLD_START_AWAITING_HUMAN_REOPEN`
- Required command: literal `DSH2--` resolves and reuses the active DSH runtime.
- Current launcher: `/home/aazcl/.local/bin/DSH2--`, SHA-256 `e0afd95902e9bc7b184e23a23361e4f7765da48d05e36ac690a7a0ee62c1cfde`.
- Required literal directory spelling: `BOOSTRAP/`.
- Local Windows and WSL roots map to the same empty physical root at preflight.
- Git baseline: local and `origin/main` are `bd907230580c76907e5a855f4d65fb4c6b7f1b4f`; tracked worktree clean.
- Runtime: DSH `0.2.0-rc.2` on `3080`; OmniRoute `3.8.50` on `20128`, `20131`, `20132`.
- Validation: DSH HTTP `401`; OmniRoute `/api/health` HTTP `200`; local route simulation only; live model route not tested.
- Profile: web bundles `@deepseek-ai/dsh-base` and `@deepseek-ai/dsh-web-app`; no separate third-party plugin registry observed.
- Full WSL shutdown/reopen: not performed from this session; requires HUMAN-controlled reopen and resume check.
- Do not install, uninstall, upgrade, restart, edit shell startup, or mutate Git without a new HUMAN gate.
- UTA005: external isolated lab contract and benchmark fixtures prepared; no production install, provider call, or Git publication.
- Cold-start record: `HUMAN_ATTESTED_REAL_COLD_START`; this is a HUMAN report, not independently instrumented PASS.
- Lab root: `/home/aazcl/.local/state/upgrade_ta/labs/UTA005`; sanitized evidence: `/home/aazcl/.local/state/upgrade_ta/evidence/UTA005`.
- Candidate shortlist is research-only. Promotion requires pinned source, license/dependency review, isolated rollback, negative tests, and a separate HUMAN_GATE.

## UTA011 measured delta

- The user-owned default `/home/aazcl/.local/bin/dsh` was reversibly retargeted from the legacy `0.1.5-rc.2` symlink to the existing user-owned `0.2.0-rc.2` executable. `dsh --version` now reports `0.2.0-rc.2`.
- The former link was quarantined under `/home/aazcl/.local/state/upgrade_ta/backups/UTA_TASK011/`; a scratch restore reproduced its target and SHA-256 before the retarget. No live process referenced the legacy target.
- `/usr/local/bin/dsh` remains a root-owned `0.1.1-rc.2` residual entrypoint and the Windows npm shim remains unchanged. They are not counted as the default active DSH, and no privileged mutation was performed.
- DSH2-- remains byte-identical at SHA-256 `e0afd95902e9bc7b184e23a23361e4f7765da48d05e36ac690a7a0ee62c1cfde`; OmniRoute remains `3.8.50` and healthy on its existing loopback service.
- EverOS is `REAL_CODE_WITH_MOCK_LLM_PASS` for the direct memory path; full provider-backed reasoning/search remains untested. Composio is `REAL_CODE_WITH_LOCAL_ADAPTER_MOCK_PASS`; authenticated remote connectors remain untested. Neither was installed into production DSH.
- UTA011 Git publication is limited to the explicitly allowlisted continuity and safety documents; no evidence, secrets, logs, or private runtime state belong in Git.
