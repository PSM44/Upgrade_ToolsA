# Integration Safety Contract

Adapters must expose retain/retrieve/provenance, idempotency, restart and
failure behavior without weakening DSH or OmniRoute boundaries. Test data is
synthetic; provider, OAuth, and paid calls are disabled by default.

Required checks are: explicit capability allowlist, denial before side effect,
invalid-input rejection, audit/correlation evidence, bounded resource use,
isolated writable paths, teardown/restore proof, and a separate HUMAN gate for
production installation. Mock LLM and local adapter results must never be
reported as live model or hosted connector success.
