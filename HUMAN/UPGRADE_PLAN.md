# Upgrade_TA Upgrade Plan

Status: bootstrap complete; runtime upgrades are not authorized.

Current gate: preserve `DSH2--`, keep DSH and OmniRoute unchanged, and obtain a
fresh HUMAN gate before any package-manager operation, shell startup change,
service restart, or rollback.

Required evidence before an upgrade: verified remote baseline, package
provenance, complete external backup, tested restore rehearsal, exact pinned
target, compatibility test plan, and a user-visible rollback command.
