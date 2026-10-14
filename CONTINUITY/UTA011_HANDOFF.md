# UTA011 Handoff

Current state: the default user-owned Kali `dsh` resolves to DSH
`0.2.0-rc.2`; `DSH2--` remains unchanged; OmniRoute is `3.8.50` and was not
modified.

Rollback for the default link is external and protected at
`/home/aazcl/.local/state/upgrade_ta/backups/UTA_TASK011/`. The quarantined
legacy link and scratch restore matched SHA-256 before the retarget. Do not
delete the archive. `/usr/local/bin/dsh` is root-owned legacy residue and the
Windows npm shim is outside this task.

Preproduction adapters: EverOS direct memory with a deterministic mock LLM
passed actual code and restart persistence; Composio actual SDK custom-tool
execution passed against a loopback local adapter with denial and validation
checks. No production installation, provider call, OAuth, or paid action was
performed. Next gate is a separately approved destination-specific integration
and live-provider security test.
