"""
Data models for UWS MVP.
Implements the canonical event schemas from DESIGN/UTA-QA-BRIDGE-DESIGN-001/contracts/
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional
import json


class EventType(str, Enum):
    """UWS event types matching FSM transitions."""
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_HANDOFF = "TASK_HANDOFF"
    RORCH_EXECUTING = "RORCH_EXECUTING"
    SELF_TEST = "SELF_TEST"
    SUBMITTED_FOR_QA = "SUBMITTED_FOR_QA"
    QA_RUNNING = "QA_RUNNING"
    QA_PASS = "QA_PASS"
    QA_REJECTED = "QA_REJECTED"
    QA_BLOCKED = "QA_BLOCKED"
    REWORK_REQUIRED = "REWORK_REQUIRED"
    REWORK_RESPONSE_SUBMITTED = "REWORK_RESPONSE_SUBMITTED"
    QA_DISPUTE = "QA_DISPUTE"
    ESCALATED_TO_MORCH = "ESCALATED_TO_MORCH"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"
    MORCH_REVIEW = "MORCH_REVIEW"
    ACCEPTED = "ACCEPTED"
    TERMINAL_COMPLETED = "TERMINAL_COMPLETED"
    TERMINAL_BLOCKED = "TERMINAL_BLOCKED"
    TERMINAL_FAILED = "TERMINAL_FAILED"
    RECOVERY = "RECOVERY"
    HEARTBEAT = "HEARTBEAT"


class QAVerdictType(str, Enum):
    PASS = "PASS"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


class ReworkResponseType(str, Enum):
    FIXED = "FIXED"
    DISPUTED = "DISPUTED"
    NOT_REPRODUCIBLE = "NOT_REPRODUCIBLE"
    BLOCKED = "BLOCKED"


class FindingSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class QADisputeResolution(str, Enum):
    CONFIRM = "CONFIRM"
    RE_REJECT = "RE_REJECT"
    ESCALATE = "ESCALATE"


@dataclass
class UWSEvent:
    """Base UWS event - immutable once created."""
    event_id: str
    event_type: EventType
    task_id: str
    run_id: str
    attempt_id: int
    correlation_id: str
    timestamp_utc: str
    source: str
    payload_sha256: str
    payload_json: str
    parent_event_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    lease_id: Optional[str] = None
    lease_expires_at: Optional[str] = None

    @classmethod
    def create(
        cls,
        event_type: EventType,
        task_id: str,
        run_id: str,
        attempt_id: int,
        correlation_id: str,
        source: str,
        payload: dict,
        parent_event_id: Optional[str] = None,
        lease_id: Optional[str] = None,
        lease_expires_at: Optional[str] = None,
    ) -> "UWSEvent":
        """Factory creating event with computed fields."""
        event_id = str(uuid.uuid4())
        timestamp_utc = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        import hashlib
        payload_sha256 = hashlib.sha256(payload_json.encode()).hexdigest()
        idempotency_key = hashlib.sha256(
            f"{task_id}|{run_id}|{attempt_id}|{event_type.value}|{parent_event_id or ''}".encode()
        ).hexdigest()[:64]

        return cls(
            event_id=event_id,
            event_type=event_type,
            task_id=task_id,
            run_id=run_id,
            attempt_id=attempt_id,
            correlation_id=correlation_id,
            timestamp_utc=timestamp_utc,
            source=source,
            payload_sha256=payload_sha256,
            payload_json=payload_json,
            parent_event_id=parent_event_id,
            idempotency_key=idempotency_key,
            lease_id=lease_id,
            lease_expires_at=lease_expires_at,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TaskHandoff:
    """TASK_HANDOFF from MORCH to RORCH (matches schema v1)."""
    schema_version: str = "upgrade-ta.uta-qabridge.task-handoff/v1"
    task_id: str = ""
    run_id: str = ""
    attempt_id: int = 1
    correlation_id: str = ""
    action_scope: dict = field(default_factory=dict)
    manifest_sha256: str = ""
    authority: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Deliverable:
    path: str
    sha256: str
    description: str


@dataclass
class EvidenceManifest:
    manifest_sha256: str
    ntv_report_path: str
    ntv_status: Literal["PASS", "FAILED", "PARTIAL", "NOT_RUN"] = "NOT_RUN"


@dataclass
class AcceptanceCriterion:
    criterion_id: str
    description: str
    status: Literal["MET", "NOT_MET", "PARTIAL", "NOT_EVALUATED"] = "NOT_EVALUATED"
    evidence_ref: str = ""


@dataclass
class QASubmission:
    """QA_SUBMISSION from RORCH to QA (matches schema v1)."""
    schema_version: str = "upgrade-ta.uta-qabridge.qa-submission/v1"
    task_id: str = ""
    run_id: str = ""
    attempt_id: int = 1
    correlation_id: str = ""
    parent_task_handoff_id: str = ""
    deliverables: list[Deliverable] = field(default_factory=list)
    evidence_manifest: Optional[EvidenceManifest] = None
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.evidence_manifest:
            d["evidence_manifest"] = asdict(self.evidence_manifest)
        return d


@dataclass
class QAFinding:
    """QA_FINDING - a single finding from QA review."""
    finding_id: str
    parent_qa_event_id: str
    severity: FindingSeverity
    path: str
    expected: str
    observed: str
    evidence_hash: str
    acceptance_rule: str
    reproducible_test: str
    status: str = "OPEN"
    resolved_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IndependentReviewEvidence:
    reviewer_identity: str
    review_path: str
    reviewed_fronts: list[str]
    manifest_sha256: str
    status: Literal["PASS", "FAIL"]


@dataclass
class QAVerdict:
    """QA_VERDICT from QA (matches schema v1)."""
    schema_version: str = "upgrade-ta.uta-qabridge.qa-verdict/v1"
    task_id: str = ""
    run_id: str = ""
    attempt_id: int = 1
    correlation_id: str = ""
    parent_qa_submission_id: str = ""
    verdict: QAVerdictType = QAVerdictType.BLOCKED
    findings: list[str] = field(default_factory=list)  # finding_ids
    independent_review_evidence: Optional[IndependentReviewEvidence] = None
    qa_acceptance_criteria_met: bool = False
    escalation_required: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.independent_review_evidence:
            d["independent_review_evidence"] = asdict(self.independent_review_evidence)
        return d


@dataclass
class ReworkResponse:
    """REWORK_RESPONSE from RORCH to QA (matches schema v1)."""
    schema_version: str = "upgrade-ta.uta-qabridge.rework-response/v1"
    task_id: str = ""
    run_id: str = ""
    attempt_id: int = 1
    correlation_id: str = ""
    parent_qa_finding_id: str = ""
    response_type: ReworkResponseType = ReworkResponseType.FIXED
    evidence: dict = field(default_factory=dict)
    requires_morch_escalation: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QADispute:
    """QA_DISPUTE resolution (matches schema)."""
    schema_version: str = "upgrade-ta.uta-qabridge.qa-dispute/v1"
    task_id: str = ""
    run_id: str = ""
    attempt_id: int = 1
    correlation_id: str = ""
    parent_rework_response_id: str = ""
    resolution: QADisputeResolution = QADisputeResolution.CONFIRM
    new_finding: Optional[QAFinding] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.new_finding:
            d["new_finding"] = self.new_finding.to_dict()
        return d


@dataclass
class TerminalEvent:
    """TERMINAL_EVENT - final evidence manifest (matches schema)."""
    schema_version: str = "upgrade-ta.uta-qabridge.terminal-event/v1"
    task_id: str = ""
    run_id: str = ""
    attempt_id: int = 1
    correlation_id: str = ""
    terminal_state: Literal["COMPLETED", "BLOCKED", "FAILED"] = "COMPLETED"
    evidence_manifest_sha256: str = ""
    deliverables: list[Deliverable] = field(default_factory=list)
    qa_verdict_id: str = ""
    independent_review_path: str = ""
    morch_review_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ArtifactRecord:
    """Artifact manifest record for hash-pinning and invalidation."""
    artifact_id: str
    task_id: str
    run_id: str
    path: str
    sha256: str
    description: str
    created_at: str
    invalidated_at: Optional[str] = None
    invalidation_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LeaseRecord:
    """Lease for crash recovery."""
    lease_id: str
    task_id: str
    run_id: str
    holder: str
    state: str
    acquired_at: str
    expires_at: str
    heartbeat_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReworkRound:
    """Rework round tracking (3-round cap)."""
    task_id: str
    run_id: str
    round_number: int
    rework_event_id: str
    response_type: ReworkResponseType
    evidence_manifest_sha256: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)
