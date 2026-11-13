# Pending HUMAN operational authorization matrix

Status: PREPARATION ONLY. All operational grants below remain NOT_AUTHORIZED.
This document creates no accounts, keys, ACLs, tasks, services or paid calls.
Evidence here is REPO_DOCUMENTED preparation; actual host properties are UNKNOWN
until the separately authorized tests execute.

| Action | Exact authorization to define | Required evidence / owner | Rollback acceptance |
|---|---|---|---|
| Windows principal W | Dedicated account identity, binary/spool paths and rights | HUMAN grants scope; independent QA proves R cannot impersonate/control W (G2) | Revoke access, disable only new principal after preserving evidence |
| Debian custodian D | Account, protected launcher/ledger paths and ownership | Denied R writes/ptrace; independent durable journal tests (G2/G4) | Stop only owned runtime; revoke channel, retain ledger/tombstones |
| Isolated runtime R | Disposable runtime root and resource boundaries | Actual access-denial and containment tests; P3 NTV_ROOT | Stop entire owned process tree; snapshot before any separately approved cleanup |
| ETW permissions | Exact W rights, trace mode/session/decoder and bounded retention | Initial/short-lived/other-session events, loss/disconnect/drain tests (G1) | Stop only new trace; retain private ETL; restore scoped rights from baseline |
| Operational keys and ACLs | Key generation authority, protected storage, rotation and fingerprints | W/D signing boundaries, R denied export/sign/replace; no keys in Git (G2) | Revoke trust anchors; preserve non-secret hashes and custody |
| Task Scheduler | Exact no-trigger task name, principal, action, ACL and activation window | Real demand start, IgnoreNew and wrong-principal negatives (G5) | Disable only new task; terminate owned unit; retain definition and receipts |
| sudo / WSL interop separation | Precisely scoped policy changes with pre-change snapshot | Real R attempts denied; no indirect elevation or Windows-host access (G2/P3) | Restore only approved policy delta from validated snapshot |
| Authenticated transport | W/D endpoints, peer identity, replay policy, protected channel and timeout | Spoof/replay/reorder/loss tests; ABORT on uncertain ACK (G3/G4) | Revoke endpoint trust, stop owned transport, preserve journals |
| OpenCode Go allowance | Account-attestation method, included allowance threshold, Extra Usage OFF proof and bounded run grant | Fresh authoritative account evidence; invocation/request accounting (P4) | Do not start if insufficient; cancel controlled run and retain sanitized cost receipts |
| Rollback mechanism / criteria | Ownership manifest, stop authority, deadline, evidence retention and restore procedure | Dry-run then authorized actual rollback by independent QA | Abort on observer loss, residual processes, custody divergence, cost uncertainty or permission failure; no automatic cleanup |

Proposed dependency order: approve exact host/rollback scope -> provision W/D/R
and real trust boundaries -> independently verify G1/G2 -> test live protocol and
recovery G3/G4 -> verify authorized task G5 -> verify P3/P4 -> separately authorize
a bounded real P2 E2E. No step inherits authority from passing offline tests.

Rollback cannot claim success from a STOP flag alone: verify the complete owned
descendant/unit set is quiescent, revoked authority cannot restart it, and retained
custody contains the abort/terminal decision. Uncertain state remains blocked.
