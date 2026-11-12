# NS-HOSTWITNESS-V1 — Accredited Windows Host Observer design (rev 2)

- TASK_ID: NORTHSTAR-P2-CODEX-CLOSED-E2E (next-node design, HUMAN-approved)
- Date: 2026-11-11 (rev 2, after QA_REJECTED of rev 1: bootstrap defect fixed,
  `before_arm` phase added, residual risks completed, spec ambiguities pinned,
  negative-test plan extended)
- Status: DESIGN — nothing here is implemented, registered or accredited yet.
- Satisfies: the `host` witness requirements declared by `tools/p2_external.py`
  (`WITNESS_REQUIREMENTS["host"]`). The `account` attestation gate is a
  SEPARATE design (only its adapter interface is referenced here).

## 0. Environment facts this design stands on (TOOL_OBSERVED 2026-11-11)

- Windows 11 host, PowerShell 7.6.9 at
  `%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe` (Store alias), `schtasks.exe`
  present, no existing `northstar*`/`witness*` scheduled tasks.
- .NET ECDSA P-256 with `ExportSubjectPublicKeyInfo` works in pwsh 7
  (91-byte SPKI observed). CNG non-exportable key containers are available.
- Live forbidden-assistant process names observed while both apps were open:
  `claude` (12 processes), `codex` (2), `codex-computer-use-swift` (1),
  `codex-windows-sandbox-service` (1). All match `(?i)claude|codex`.
- Windows can write into the Debian filesystem via
  `\\wsl.localhost\Debian\...` (9P; already used for authenticated git pushes).
  Debian does NOT mount `/mnt/c`, so Windows→Debian UNC append is the only
  no-global-change channel; enabling automount would be a forbidden global
  WSL change.
- Debian: OpenSSL 3.5.6 CLI available; Python 3.13.5 WITHOUT the
  `cryptography` package. Verification therefore uses the `openssl` CLI;
  no package installs are required on either side.

## 1. Purpose and honest scope

Give `IndependentWitness.verify()` a reviewable, HUMAN-accredited source of
truth for: "no standalone Codex/Claude desktop process ran on the Windows host
during the P2 run, with continuous signed coverage" (detection limits in §7).
The observer is same-machine and HUMAN-provisioned: it defends against
implicit assistant participation, post-hoc fabrication, runner-authored JSON
and coverage gaps. It does NOT defend against a hostile Windows administrator,
kernel/hypervisor compromise, deliberately renamed assistant binaries outside
known install paths, assistants hosted inside third-party processes, or
assistants inside the WSL VM itself (§7); those belong to P3 NTV_ROOT and are
recorded as residual risks, not simulated away.

## 2. Components

1. **Witness task (Windows)** — `host_witness.ps1`, run by a Windows Scheduled
   Task (`NorthStar-HostWitness-V1`, single-instance policy: `schtasks`
   `/SC ONDEMAND` with multiple-instance behavior "do not start a new
   instance", plus a named mutex inside the script) registered by HUMAN,
   executing pwsh 7 with `-NoProfile -ExecutionPolicy Bypass -File <pinned
   path>`. Its parent chain is the task scheduler (svchost), never
   Claude/Codex. HUMAN starts it before a run (`schtasks /run`); it loops
   until a stop file appears.
2. **Trust anchor** — ECDSA P-256 keypair generated during provisioning in a
   CNG key container with `ExportPolicy=None` (non-exportable private key,
   bound to the Windows user). The public key (SPKI PEM) is copied by HUMAN to
   Debian `~/.config/northstar/witness_trust_anchor.pem` (0600). Its SHA256
   fingerprint and the script's SHA256 are recorded by HUMAN in the MR0
   decision log; that record IS the accreditation (§5).
3. **Fixed spool (runtime-independent; fixes the rev-1 bootstrap defect)** —
   HUMAN provisioning creates `/home/psm/northstar/state/witness/` (0700) with
   `streams/` and `handshake/` inside. The witness appends JSONL to
   `\\wsl.localhost\Debian\home\psm\northstar\state\witness\streams\host_witness_<utc-start>.jsonl`
   — one stream file per witness session, NEVER inside a run's runtime
   directory, so coverage exists before any runtime is created. When the UNC
   path is unavailable the witness buffers under `%LOCALAPPDATA%\NorthStar\witness\`
   (forensics only); undelivered records are a coverage gap and the gate
   blocks (fail closed).
4. **Verification adapter (Debian)** — `src/host_witness_verifier.py`
   implementing the `IndependentWitness` protocol for FOUR phases:
   `before_arm`, `before_create`, `heartbeat`, `after_quiescence` (the code
   calls `before_arm` in `main()` before arm/execute; rev 1 omitted it). It is
   wired into `tools/p2_external.py` ONLY by a reviewed code change after
   accreditation; the current always-blocking class stays until then. No
   runtime plugin, CLI flag, env var or caller-chosen verifier is ever honored.

## 3. Record format and canonical serialization

Each line is canonical JSON — **sorted keys, compact separators `(",", ":")`,
ASCII-only escapes, integers verbatim** — of:

```
{
  "schema": "ns-hostwitness-v1",
  "stream_id": <uuid4 chosen at witness start>,
  "sequence": <int, starts 1 PER STREAM, strictly +1>,
  "utc_ns": <Windows clock, ns, int64>,
  "qpc_ns": <Stopwatch/QueryPerformanceCounter, ns, int64, strictly increasing per stream>,
  "run_binding": {"objective_id": ..., "runtime": ..., "nonce": ...} | null,
  "task_identity": {"task_name": "NorthStar-HostWitness-V1",
                     "script_sha256": ..., "pid": ..., "parent_chain": [...]},
  "sample": {"forbidden": [{"name":..., "pid":..., "path":...}],
              "forbidden_count": <int>, "total_count": <int>,
              "inaccessible_mainmodule_count": <int>,
              "match_rule": "name_or_mainmodule_path",
              "scan_paths": ["<known Claude/Codex install dirs>"]},
  "interval_s": <configured heartbeat, <= 5>,
  "previous_sha256": <hash chain PER STREAM, genesis 64x"0">,
  "sha256": <sha256 of the record body without sha256/signature>,
  "signature": <base64 ECDSA-P256-SHA256 over the same body>
}
```

pwsh does NOT use `ConvertTo-Json` for the signed body (not canonical); it
serializes with an explicit ordered writer. A **cross-platform golden vector**
(fixed record → exact bytes → exact sha256) ships with the tests and MUST be
byte-identical between the pwsh serializer and the Python verifier, including
int64 values above 2^53 (Python parses arbitrary-precision ints; no floats
appear in signed fields). Divergence can only cause false BLOCKING, never
false acceptance.

Forbidden detection is two-layered: (a) process-name regex `(?i)claude|codex`
against `Get-Process` (covers every name observed live in §0); (b)
`MainModule.FileName` prefix match against the pinned install directories of
Claude Desktop and Codex, catching a renamed binary launched from a known
install footprint. A process whose `MainModule` is inaccessible (Access
Denied) still passes through layer (a) by name and is counted in
`inaccessible_mainmodule_count` (never silently dropped). Only name, pid and
image path leave the sample — never command lines, window titles or
environment (same redaction stance as the Linux snapshots).

## 4. Run binding, handshake bootstrap and verification rules

**Handshake (outside any runtime, fixing the rev-1 defect):** before calling
`verify("before_arm"/"before_create")` and before `validate_runtime` can ever
`mkdir`, the external runner writes
`/home/psm/northstar/state/witness/handshake/<nonce>.json` =
`{objective_id, runtime, nonce, requested_at_ns}` (the `runtime` field is the
intended path as data; the directory itself still must not exist). The witness
polls `handshake/`, adopts the newest valid handshake, and from then on echoes
it as `run_binding` in every record; records before any handshake carry
`run_binding: null` within the same stream and chain. The wiring change in
`tools/p2_external.py` (§8.4) therefore writes the handshake FIRST, then calls
the verifier — a reviewed, explicit reordering, not a caller option.

The verifier reads the NEWEST stream file by signed `utc_ns` of its first
record (not by filename), and enforces per phase:

- **Common (every phase):** every line parses (tolerance: exactly ONE
  unterminated final line is treated as pending-append and ignored; freshness
  and coverage are computed from the last COMPLETE record; any other garbage
  blocks); signature verifies against the pinned anchor via `openssl dgst
  -sha256 -verify` on the canonical body; `previous_sha256`/`sha256` chain
  links with no forks; one `stream_id` per file; `sequence` strictly +1 from
  1; `qpc_ns` strictly increasing; `utc_ns` non-decreasing and never more than
  the skew budget (±10 s) ahead of the verifier clock; `interval_s <= 5`;
  `task_identity.script_sha256` equals the accredited hash;
  `sample.forbidden_count == 0`, `forbidden == []`, and `forbidden_count`
  consistent with the list in EVERY record; inter-record `utc_ns` gaps
  `< 3 * interval_s`.
- **before_arm / before_create:** at least one record bound to this exact
  `{objective_id, runtime, nonce}` with `utc_ns` within `2 * interval_s` of
  the verifier's own clock — live, bound coverage while the runtime directory
  does not yet exist. The runner may wait up to `4 * interval_s` after writing
  the handshake for the first bound record before failing.
- **heartbeat:** bound, gapless coverage in ONE stream from the first bound
  record through a record newer than `now - 3 * interval_s`. A witness restart
  mid-run starts a new stream (new `stream_id`, sequence reset): continuity is
  broken by definition and the gate BLOCKS — expected, declared behavior.
- **after_quiescence:** bound coverage in that same stream extending PAST the
  runner's recorded quiescence timestamp, plus one fresh record after it.

Any failure, missing file, unreadable anchor, unknown schema, stream fork,
sequence/clock regression or binding change to a different nonce mid-stream
raises `GateBlocked`. A forger without the CNG private key cannot produce a
verifying record; replaying an entire old stream fails freshness in all four
phases because `utc_ns` is inside the signed body.

## 5. Accreditation procedure (HUMAN-executed, one time)

1. HUMAN reviews this design, `host_witness.ps1` and
   `host_witness_verifier.py` sources; records their SHA256 in the MR0
   decision log.
2. HUMAN runs the provisioning script in their own pwsh session (not via
   Claude/Codex): it creates the CNG key, exports the public SPKI PEM, prints
   both fingerprints, writes the anchor to the Debian path, creates the fixed
   spool directories, and registers the single-instance scheduled task
   pointing at the pinned script path.
3. HUMAN records in MR0: anchor fingerprint, task name, script hash,
   provisioning date. These four values are what the verifier pins.
4. **Accreditation drill (mandatory before first real use):** with Claude
   Desktop open, run the verifier self-test — it MUST block with forbidden
   processes present; close Claude/Codex, re-run — it MUST pass
   `before_create` freshness; kill the witness mid-stream — heartbeat MUST
   block. Drill transcript is filed as evidence. A witness that cannot detect
   the assistants §0 observed live is not accredited.

## 6. Requirement mapping

| Requirement (tools/p2_external.py) | Mechanism |
|---|---|
| independent authenticated monitor identity, preapproved trust anchor | CNG non-exportable key; HUMAN-pinned SPKI anchor + script hash in MR0 |
| Windows host process enumeration with zero Codex and Claude processes | two-layer detection of standalone desktop processes; `forbidden_count==0` in every record — subject to the declared §7 limits (sampling granularity, third-party-hosted assistants, WSL-internal processes) |
| objective and exact runtime binding | handshake file nonce echoed in `run_binding`, verified exactly, mid-stream binding change blocks |
| continuous coverage before supervisor start through complete quiescence | runtime-independent spool + per-phase freshness + gapless single-stream rules across all four phases |
| trusted UTC timestamps, monotonic sequence, heartbeat, signed hash chain | signed `utc_ns` + `qpc_ns` + per-stream `sequence` + SHA256 chain + ECDSA per record |
| no gaps, replay, stale reports or unauthenticated self-authored JSON | gap bound, chain/stream fork detection, freshness windows in every phase, signature no one without the CNG key can produce |

## 7. Residual risks (recorded, not simulated away)

- Same-machine trust: a hostile Windows admin or same-user malware could use
  (not export) the CNG key or kill the witness; a killed witness only ever
  FAILS the gate (heartbeat gap), never passes it.
- **Sampling granularity:** detection is polling (`Get-Process` every
  ≤5 s), not ETW/process-creation auditing; an assistant process living only
  ~2–4 s between samples is invisible. Future hardening: ETW or Security
  event 4688 consumption, out of scope for V1 and declared here.
- **WSL-internal assistants:** processes inside the WSL VM (e.g. Claude Code
  CLI in Debian) are invisible to the Windows witness; they are covered only
  by the runner's own Linux /proc snapshots, which are self-authored by
  design and carry no independent authority. Declared, not resolved, in V1.
- **Third-party-hosted assistants:** Codex as a VS Code extension (inside
  `Code.exe`/`node`) or claude.ai in a browser evade name+install-path
  detection; the Windows witness lacks the argv layer the Linux snapshot has.
  Declared V1 limit; candidate hardening (command-line sampling via
  Win32_Process) trades against the no-argv redaction stance and needs its own
  review.
- Renamed assistant binaries outside known install paths evade layer (b);
  P3 NTV_ROOT concern. The gate, even accredited, is labeled
  HUMAN-accredited-local, not externally rooted.
- Clock trust: both clocks live on one machine; the ±10 s skew budget bounds
  but does not eliminate coordinated clock tampering.
- 9P/UNC availability: an outage stops record flow → gate blocks (fail
  closed); local Windows buffering preserves forensics but never substitutes
  for delivered, verified records. A partial final line during live append is
  tolerated per §4 without weakening freshness.
- The scheduled task runs in the user session; logoff stops it — acceptable
  because runs are HUMAN-initiated and the gate blocks without it.

## 8. Implementation and test plan (no billable calls anywhere)

1. `tools/host_witness.ps1` + `tools/provision_host_witness.ps1` (Windows,
   pwsh 7, explicit canonical serializer, named mutex).
2. `src/host_witness_verifier.py` (stdlib + `openssl` CLI only; four phases).
3. `tests/test_host_witness_verifier.py` — every case raises `GateBlocked`:
   forged signature; broken chain; stream fork (duplicate sequence / two
   stream_ids in one file); replay of an old segment AND of a complete old
   stream (stale in all four phases); sequence reset / witness restart
   mid-run; `utc_ns` or `qpc_ns` regression; record from the future (> skew
   budget); stale `before_create`/`before_arm`; heartbeat gap; binding change
   to another nonce mid-stream; wrong binding; `after_quiescence` with
   truncated tail (no post-quiescence record); `interval_s > 5`;
   non-accredited `script_sha256`; inconsistent record (`forbidden_count==0`
   with non-empty list); forbidden present; unknown schema; trailing garbage
   beyond the single pending line; missing anchor. Plus: golden-path fixture
   signed by a TEST key (never the accredited anchor); the pwsh↔python
   canonicalization golden vector (byte-identical, int64 > 2^53); and
   `inaccessible_mainmodule_count` handling (Access Denied does not drop the
   name layer).
4. Reviewed wiring change in `tools/p2_external.py`: write the handshake file
   (fixed spool, outside any runtime) BEFORE the `before_arm`/`before_create`
   verifications; import the accredited verifier explicitly; the
   always-blocking default remains the fallback.
5. HUMAN provisioning + accreditation drill (§5).

Estimated delta: ~2 scripts + 1 module + 1 test file; no changes to BATON,
ROOT Windows project, Kali, UTA006 or global WSL/Windows configuration —
`schtasks` registration itself is HUMAN-executed, not agent-executed.
