# Upgrade_TA Upgrade Plan

Status: DSH 0.2.0-rc.2 upgraded and post-upgrade validation partially complete.

Current gate: preserve the literal `DSH2--` command and OmniRoute `3.8.50`.
The offline/fresh-shell checks passed; a real WSL shutdown/reopen remains a
HUMAN-controlled validation boundary.

Required evidence before an upgrade: verified remote baseline, package
provenance, complete external backup, tested restore rehearsal, exact pinned
target, compatibility test plan, and a user-visible rollback command.

UTA004 routing status: local OmniRoute configuration and dry-run route
selection verified without upstream calls; live model routing remains untested.
UTA004 plugin status: web profile bundles are present and compatible by
startup/profile smoke; no separate third-party plugin registry was observed.

UTA005 lab gate: benchmark functional completion, security/gates, restart and
recovery, memory provenance, interoperability, resource/cost, and traceability.
Negative fixtures cover prompt injection, unauthorized writes, and incorrect
memory. Promotion requires all security gates, zero unauthorized writes/calls,
and HUMAN approval; candidate review is not an installation or compatibility
PASS.

UTA011 measured outcome: the user-owned default `dsh` entrypoint now resolves
to the existing pinned DSH `0.2.0-rc.2`; the previous user symlink to
`0.1.5-rc.2` is protected and restore-rehearsed outside the repository.
The root-owned `/usr/local/bin/dsh` and Windows npm shim were not modified.
DSH2-- is unchanged, OmniRoute remains `3.8.50`, and no provider calls or
production adapter installs occurred. EverOS and Composio are preproduction
adapter results only: deterministic/mock boundary evidence, not live-provider
connectivity. Any production integration remains a separate HUMAN gate.
