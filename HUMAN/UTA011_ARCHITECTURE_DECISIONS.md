# UTA011 Architecture Decisions

## Decision boundary

Keep one user-default Kali DSH runtime at pinned `0.2.0-rc.2`, preserve the
literal `DSH2--` launcher, and leave OmniRoute `3.8.50` unchanged. Recovery
archives are retained outside Git; root-owned and Windows-managed entrypoints
are not rewritten without a separate privilege/ownership gate.

## Adapter boundary

- EverOS `1.4.1`: `REAL_CODE_WITH_MOCK_LLM_PASS` on the direct episode-memory
  path, with deterministic local responses and restart persistence. Provider
  reasoning and full server search are not tested.
- Composio `@composio/core 0.24.0`:
  `REAL_CODE_WITH_LOCAL_ADAPTER_MOCK_PASS` using the actual SDK custom-tool
  path, validation, denial-before-side-effect, and fresh-process replay.
  OAuth, hosted Tool Router, and remote connector execution are not tested.

These results are `PREPROD_INTEGRATION_READY`, not production approval. Any
promotion must name a destination profile, credential boundary, rollback owner,
and separately prove live-provider and security behavior.
