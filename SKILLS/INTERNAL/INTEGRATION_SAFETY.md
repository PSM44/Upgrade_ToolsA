# Internal Skill — Integration Safety
Every adapter must test identity, bounded side effects, capability allowlist, fail-closed denial before effects, invalid-input rejection, correlation/audit, restart/idempotency, isolated writable root, auth secrecy, version compatibility, rollback rehearsal and negative fixtures. Synthetic data by default. Preserve DSH2-- and OmniRoute unless separately authorized. Mock outcomes NEVER count as real provider or host-originated E2E. Evidence under durable excluded external runtime state; publish sanitized summaries only.


## Extended Integration Safety Notes (Merged from UTA011/UTA019)
# Integration Safety Contract

Adapters must expose retain/retrieve/provenance, idempotency, restart and
failure behavior without weakening DSH or OmniRoute boundaries. Test data is
synthetic; provider, OAuth, and paid calls are disabled by default.

Required checks are: explicit capability allowlist, denial before side effect,
invalid-input rejection, audit/correlation evidence, bounded resource use,
isolated writable paths, teardown/restore proof, and a separate HUMAN gate for
production installation. Mock LLM and local adapter results must never be
reported as live model or hosted connector success.
