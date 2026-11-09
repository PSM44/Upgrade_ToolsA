"""
Finite State Machine for UWS MVP.
Implements the Execution↔QA state machine from DESIGN/UTA-QA-BRIDGE-DESIGN-001/fsm/FSM-UTA-QA-BRIDGE-EXECUTION-QA-LEARNING.md
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Dict, List, Set
from abc import ABC, abstractmethod

from .models import (
    EventType, QAVerdictType, ReworkResponseType, FindingSeverity,
    QADisputeResolution, UWSEvent, QASubmission, QAVerdict, QAFinding,
    ReworkResponse, ReworkRound
)
from .ledger import Ledger
from .idempotency import generate_idempotency_key


class FSMState(str, Enum):
    """FSM states matching the design specification."""
    TASK_ASSIGNED = "TASK_ASSIGNED"
    RORCH_EXECUTING = "RORCH_EXECUTING"
    SELF_TEST = "SELF_TEST"
    SUBMITTED_FOR_QA = "SUBMITTED_FOR_QA"
    QA_RUNNING = "QA_RUNNING"
    QA_PASS = "QA_PASS"
    QA_REJECTED = "QA_REJECTED"
    QA_BLOCKED = "QA_BLOCKED"
    REWORK_REQUIRED = "REWORK_REQUIRED"
    REWORK_RESPONSE_SUBMITTED = "REWORK_RESPONSE_SUBMITTED"
    QA_DISPUTE_RESOLUTION = "QA_DISPUTE_RESOLUTION"
    ESCALATED_TO_MORCH = "ESCALATED_TO_MORCH"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"
    MORCH_REVIEW = "MORCH_REVIEW"
    ACCEPTED = "ACCEPTED"
    TERMINAL_COMPLETED = "TERMINAL_COMPLETED"
    TERMINAL_BLOCKED = "TERMINAL_BLOCKED"
    TERMINAL_FAILED = "TERMINAL_FAILED"


class TransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class GuardError(Exception):
    """Raised when a transition guard fails."""
    pass


@dataclass
class Transition:
    """Represents a valid state transition with its guard."""
    from_state: FSMState
    to_state: FSMState
    event_type: EventType
    guard: Optional[Callable[["UWSContext"], bool]] = None
    description: str = ""


@dataclass
class UWSContext:
    """Context passed to guards and transitions."""
    task_id: str
    run_id: str
    attempt_id: int
    correlation_id: str
    ledger: Ledger
    current_state: FSMState
    rework_round: int = 0
    technical_failure_count: int = 0
    last_event: Optional[UWSEvent] = None
    payload: Optional[dict] = None

    def __post_init__(self):
        if self.payload is None:
            self.payload = {}


class UWSFSM:
    """
    Deterministic Finite State Machine for UWS Execution↔QA Loop.
    
    All transitions require explicit events written to the ledger.
    Guards enforce the transition rules from the design specification.
    """

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self._transitions: List[Transition] = []
        self._build_transitions()

    def _build_transitions(self) -> None:
        """Build all valid transitions with their guards."""
        # TASK_ASSIGNED → RORCH_EXECUTING
        self._add_transition(
            FSMState.TASK_ASSIGNED,
            FSMState.RORCH_EXECUTING,
            EventType.RORCH_EXECUTING,
            guard=self._guard_task_assigned_to_executing,
            description="RORCH begins execution"
        )

        # RORCH_EXECUTING → SELF_TEST
        self._add_transition(
            FSMState.RORCH_EXECUTING,
            FSMState.SELF_TEST,
            EventType.SELF_TEST,
            guard=self._guard_executing_to_self_test,
            description="RORCH runs self-tests"
        )

        # SELF_TEST → SUBMITTED_FOR_QA (success)
        self._add_transition(
            FSMState.SELF_TEST,
            FSMState.SUBMITTED_FOR_QA,
            EventType.SUBMITTED_FOR_QA,
            guard=self._guard_self_test_to_submitted,
            description="Self-tests pass, submit for QA"
        )

        # SELF_TEST → REWORK_REQUIRED (self-test failure)
        self._add_transition(
            FSMState.SELF_TEST,
            FSMState.REWORK_REQUIRED,
            EventType.REWORK_REQUIRED,
            guard=self._guard_self_test_failed,
            description="Self-test failed, rework needed"
        )

        # RORCH_EXECUTING → BLOCKED (scope/auth/evidence gap)
        self._add_transition(
            FSMState.RORCH_EXECUTING,
            FSMState.QA_BLOCKED,
            EventType.QA_BLOCKED,
            guard=self._guard_executing_to_blocked,
            description="Execution blocked by scope/auth/evidence gap"
        )

        # SUBMITTED_FOR_QA → QA_RUNNING
        self._add_transition(
            FSMState.SUBMITTED_FOR_QA,
            FSMState.QA_RUNNING,
            EventType.QA_RUNNING,
            guard=self._guard_submitted_to_qa_running,
            description="QA accepts submission for review"
        )

        # SUBMITTED_FOR_QA → QA_BLOCKED (invalid submission)
        self._add_transition(
            FSMState.SUBMITTED_FOR_QA,
            FSMState.QA_BLOCKED,
            EventType.QA_BLOCKED,
            guard=self._guard_submitted_to_blocked,
            description="QA rejects invalid submission"
        )

        # QA_RUNNING → QA_PASS
        self._add_transition(
            FSMState.QA_RUNNING,
            FSMState.QA_PASS,
            EventType.QA_PASS,
            guard=self._guard_qa_running_to_pass,
            description="All criteria met + independent review PASS"
        )

        # QA_RUNNING → QA_REJECTED
        self._add_transition(
            FSMState.QA_RUNNING,
            FSMState.QA_REJECTED,
            EventType.QA_REJECTED,
            guard=self._guard_qa_running_to_rejected,
            description="Findings identified"
        )

        # QA_RUNNING → QA_BLOCKED
        self._add_transition(
            FSMState.QA_RUNNING,
            FSMState.QA_BLOCKED,
            EventType.QA_BLOCKED,
            guard=self._guard_qa_running_to_blocked,
            description="Cannot evaluate (evidence/scope/auth gap)"
        )

        # QA_REJECTED → REWORK_REQUIRED
        self._add_transition(
            FSMState.QA_REJECTED,
            FSMState.REWORK_REQUIRED,
            EventType.REWORK_REQUIRED,
            guard=self._guard_qa_rejected_to_rework,
            description="RORCH responds to findings"
        )

        # REWORK_REQUIRED → RORCH_EXECUTING (round < 3)
        self._add_transition(
            FSMState.REWORK_REQUIRED,
            FSMState.RORCH_EXECUTING,
            EventType.RORCH_EXECUTING,
            guard=self._guard_rework_to_executing,
            description="Rework round < 3, continue execution"
        )

        # REWORK_REQUIRED → ESCALATED_TO_MORCH (round ≥ 3 or contradiction)
        self._add_transition(
            FSMState.REWORK_REQUIRED,
            FSMState.ESCALATED_TO_MORCH,
            EventType.ESCALATED_TO_MORCH,
            guard=self._guard_rework_to_morch,
            description="Max rounds reached or contradiction"
        )

        # QA_BLOCKED → RETRY_OR_ESCALATE (simplified to QA_RUNNING or ESCALATED)
        self._add_transition(
            FSMState.QA_BLOCKED,
            FSMState.QA_RUNNING,
            EventType.QA_RUNNING,
            guard=self._guard_blocked_to_retry,
            description="RORCH provides missing evidence/clarification"
        )

        self._add_transition(
            FSMState.QA_BLOCKED,
            FSMState.ESCALATED_TO_MORCH,
            EventType.ESCALATED_TO_MORCH,
            guard=self._guard_blocked_to_morch,
            description="Cannot resolve, escalate to MORCH"
        )

        # QA_PASS → MORCH_REVIEW
        self._add_transition(
            FSMState.QA_PASS,
            FSMState.MORCH_REVIEW,
            EventType.MORCH_REVIEW,
            guard=self._guard_qa_pass_to_morch_review,
            description="Durable evidence manifest + lease"
        )

        # MORCH_REVIEW → ACCEPTED
        self._add_transition(
            FSMState.MORCH_REVIEW,
            FSMState.ACCEPTED,
            EventType.ACCEPTED,
            guard=self._guard_morch_review_to_accepted,
            description="MORCH accepts evidence"
        )

        # MORCH_REVIEW → REWORK_REQUIRED (separate from QA)
        self._add_transition(
            FSMState.MORCH_REVIEW,
            FSMState.REWORK_REQUIRED,
            EventType.REWORK_REQUIRED,
            guard=self._guard_morch_review_to_rework,
            description="MORCH requires rework (separate from QA)"
        )

        # ACCEPTED → TERMINAL_COMPLETED
        self._add_transition(
            FSMState.ACCEPTED,
            FSMState.TERMINAL_COMPLETED,
            EventType.TERMINAL_COMPLETED,
            guard=self._guard_accepted_to_terminal,
            description="Terminal evidence manifest complete"
        )

        # ESCALATED_TO_MORCH → ESCALATED_TO_HUMAN
        self._add_transition(
            FSMState.ESCALATED_TO_MORCH,
            FSMState.ESCALATED_TO_HUMAN,
            EventType.ESCALATED_TO_HUMAN,
            guard=self._guard_morch_to_human,
            description="MORCH cannot resolve"
        )

        # ESCALATED_TO_HUMAN → TERMINAL_BLOCKED (denied)
        self._add_transition(
            FSMState.ESCALATED_TO_HUMAN,
            FSMState.TERMINAL_BLOCKED,
            EventType.TERMINAL_BLOCKED,
            guard=self._guard_human_denied,
            description="HUMAN denies authorization"
        )

        # ESCALATED_TO_HUMAN → QA_RUNNING (authorized)
        self._add_transition(
            FSMState.ESCALATED_TO_HUMAN,
            FSMState.QA_RUNNING,
            EventType.QA_RUNNING,
            guard=self._guard_human_authorized,
            description="HUMAN authorizes continuation"
        )

    def _guard_task_assigned_to_executing(self, ctx: UWSContext, payload: dict) -> bool:
        """TASK_ASSIGNED → RORCH_EXECUTING: Always allowed as first step."""
        return True

    def _add_transition(
        self,
        from_state: FSMState,
        to_state: FSMState,
        event_type: EventType,
        guard: Optional[Callable[[UWSContext, dict], bool]] = None,
        description: str = "",
    ) -> None:
        self._transitions.append(Transition(
            from_state=from_state,
            to_state=to_state,
            event_type=event_type,
            guard=guard,
            description=description,
        ))

    def get_valid_transitions(self, state: FSMState) -> List[Transition]:
        """Get all valid transitions from a given state."""
        return [t for t in self._transitions if t.from_state == state]

    def can_transition(self, context: UWSContext, target_state: FSMState) -> bool:
        """Check if transition to target_state is valid from current state."""
        transitions = self.get_valid_transitions(context.current_state)
        for t in transitions:
            if t.to_state == target_state:
                if t.guard is None or t.guard(context):
                    return True
        return False

    def transition(
        self,
        context: UWSContext,
        target_state: FSMState,
        event_payload: dict,
        source: str,
        parent_event_id: Optional[str] = None,
        lease_id: Optional[str] = None,
        lease_expires_at: Optional[str] = None,
    ) -> UWSEvent:
        """
        Execute a state transition, creating and persisting the event.
        Validates guards and writes event to ledger atomically.
        """
        # Find matching transition
        transition = None
        for t in self.get_valid_transitions(context.current_state):
            if t.to_state == target_state:
                transition = t
                break

        if not transition:
            valid = [f"{t.from_state.value}→{t.to_state.value}" for t in self.get_valid_transitions(context.current_state)]
            raise TransitionError(
                f"Invalid transition from {context.current_state.value} to {target_state.value}. "
                f"Valid: {valid}"
            )

        # Check guard with the new payload (since guard validates the transition TO the new state)
        if transition.guard and not transition.guard(context, event_payload):
            raise GuardError(f"Guard failed for transition {context.current_state.value} → {target_state.value}")

        # Create event
        event = UWSEvent.create(
            event_type=transition.event_type,
            task_id=context.task_id,
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            correlation_id=context.correlation_id,
            source=source,
            payload=event_payload,
            parent_event_id=parent_event_id,
            lease_id=lease_id,
            lease_expires_at=lease_expires_at,
        )

        # Persist to ledger (atomic)
        self.ledger.append_event(event)

        # Update context
        context.current_state = target_state
        context.last_event = event
        context.payload = event_payload  # Update payload for next transition's guard

        return event

    # ========== Guard Functions ==========

    def _guard_executing_to_self_test(self, ctx: UWSContext, payload: dict) -> bool:
        """RORCH_EXECUTING → SELF_TEST: Execution complete, ready for self-test."""
        return payload is not None and payload.get("execution_complete", False)

    def _guard_self_test_to_submitted(self, ctx: UWSContext, payload: dict) -> bool:
        """SELF_TEST → SUBMITTED_FOR_QA: Self-tests passed, have QA submission."""
        return (
            payload is not None
            and payload.get("self_test_passed", False)
            and "qa_submission" in payload
        )

    def _guard_self_test_failed(self, ctx: UWSContext, payload: dict) -> bool:
        """SELF_TEST → REWORK_REQUIRED: Self-tests failed."""
        return payload is None or not payload.get("self_test_passed", True)

    def _guard_executing_to_blocked(self, ctx: UWSContext, payload: dict) -> bool:
        """RORCH_EXECUTING → QA_BLOCKED: Scope/auth/evidence gap."""
        return payload is not None and payload.get("blocked_reason") in ("scope_gap", "auth_gap", "evidence_gap")

    def _guard_submitted_to_qa_running(self, ctx: UWSContext, payload: dict) -> bool:
        """SUBMITTED_FOR_QA → QA_RUNNING: Valid QA_SUBMISSION schema."""
        if payload is None:
            return False
        submission = payload.get("qa_submission")
        # Accept either QASubmission object or dict (from to_dict())
        if isinstance(submission, QASubmission):
            return True
        if isinstance(submission, dict):
            # Check required fields for dict
            return all(k in submission for k in ("task_id", "run_id", "deliverables", "evidence_manifest", "acceptance_criteria"))
        return False

    def _guard_submitted_to_blocked(self, ctx: UWSContext, payload: dict) -> bool:
        """SUBMITTED_FOR_QA → QA_BLOCKED: Invalid submission."""
        return payload is not None and payload.get("submission_invalid", False)

    def _guard_qa_running_to_pass(self, ctx: UWSContext, payload: dict) -> bool:
        """QA_RUNNING → QA_PASS: All criteria met + independent review PASS."""
        if payload is None:
            return False
        verdict = payload.get("qa_verdict")
        if verdict is None:
            return False
        # Handle both QAVerdict object and dict
        if isinstance(verdict, QAVerdict):
            return (
                verdict.verdict == QAVerdictType.PASS
                and verdict.independent_review_evidence is not None
                and verdict.independent_review_evidence.status == "PASS"
            )
        if isinstance(verdict, dict):
            return (
                verdict.get("verdict") == QAVerdictType.PASS.value
                and verdict.get("independent_review_evidence") is not None
                and verdict["independent_review_evidence"].get("status") == "PASS"
            )
        return False

    def _guard_qa_running_to_rejected(self, ctx: UWSContext, payload: dict) -> bool:
        """QA_RUNNING → QA_REJECTED: At least one finding."""
        if payload is None:
            return False
        verdict = payload.get("qa_verdict")
        if verdict is None:
            return False
        if isinstance(verdict, QAVerdict):
            return verdict.verdict == QAVerdictType.REJECTED and len(verdict.findings) > 0
        if isinstance(verdict, dict):
            return verdict.get("verdict") == QAVerdictType.REJECTED.value and len(verdict.get("findings", [])) > 0
        return False

    def _guard_qa_running_to_blocked(self, ctx: UWSContext, payload: dict) -> bool:
        """QA_RUNNING → QA_BLOCKED: Cannot evaluate (evidence/scope/auth gap)."""
        if payload is None:
            return False
        verdict = payload.get("qa_verdict")
        if verdict is None:
            return False
        if isinstance(verdict, QAVerdict):
            return verdict.verdict == QAVerdictType.BLOCKED
        if isinstance(verdict, dict):
            return verdict.get("verdict") == QAVerdictType.BLOCKED.value
        return False

    def _guard_qa_rejected_to_rework(self, ctx: UWSContext, payload: dict) -> bool:
        """QA_REJECTED → REWORK_REQUIRED: Valid REWORK_RESPONSE."""
        if payload is None:
            return False
        rework = payload.get("rework_response")
        if rework is None:
            return False
        # Accept both ReworkResponse object and dict
        if isinstance(rework, ReworkResponse):
            return True
        if isinstance(rework, dict):
            # Check required fields for dict
            return all(k in rework for k in ("task_id", "run_id", "attempt_id", "correlation_id", "parent_qa_finding_id", "response_type", "evidence", "requires_morch_escalation"))
        return False

    def _guard_rework_to_executing(self, ctx: UWSContext, payload: dict) -> bool:
        """REWORK_REQUIRED → RORCH_EXECUTING: Round < 3."""
        return ctx.rework_round < 3

    def _guard_rework_to_morch(self, ctx: UWSContext, payload: dict) -> bool:
        """REWORK_REQUIRED → ESCALATED_TO_MORCH: Round ≥ 3 OR contradiction."""
        return ctx.rework_round >= 3 or (payload is not None and payload.get("contradiction", False))

    def _guard_blocked_to_retry(self, ctx: UWSContext, payload: dict) -> bool:
        """QA_BLOCKED → QA_RUNNING: RORCH provides missing evidence."""
        return payload is not None and payload.get("evidence_provided", False)

    def _guard_blocked_to_morch(self, ctx: UWSContext, payload: dict) -> bool:
        """QA_BLOCKED → ESCALATED_TO_MORCH: Cannot resolve."""
        return payload is not None and payload.get("cannot_resolve", False)

    def _guard_qa_pass_to_morch_review(self, ctx: UWSContext, payload: dict) -> bool:
        """QA_PASS → MORCH_REVIEW: Durable evidence manifest + lease."""
        return (
            payload is not None
            and payload.get("evidence_manifest_complete", False)
            and payload.get("lease_active", False)
        )

    def _guard_morch_review_to_accepted(self, ctx: UWSContext, payload: dict) -> bool:
        """MORCH_REVIEW → ACCEPTED: MORCH accepts evidence."""
        return payload is not None and payload.get("morch_decision") == "ACCEPTED"

    def _guard_morch_review_to_rework(self, ctx: UWSContext, payload: dict) -> bool:
        """MORCH_REVIEW → REWORK_REQUIRED: MORCH requires rework."""
        return payload is not None and payload.get("morch_decision") == "REWORK_REQUIRED"

    def _guard_accepted_to_terminal(self, ctx: UWSContext, payload: dict) -> bool:
        """ACCEPTED → TERMINAL_COMPLETED: Terminal evidence manifest."""
        return payload is not None and payload.get("terminal_manifest_complete", False)

    def _guard_morch_to_human(self, ctx: UWSContext, payload: dict) -> bool:
        """ESCALATED_TO_MORCH → ESCALATED_TO_HUMAN: MORCH cannot resolve."""
        return payload is not None and payload.get("morch_cannot_resolve", False)

    def _guard_human_denied(self, ctx: UWSContext, payload: dict) -> bool:
        """ESCALATED_TO_HUMAN → TERMINAL_BLOCKED: HUMAN denies."""
        return payload is not None and payload.get("human_decision") == "DENIED"

    def _guard_human_authorized(self, ctx: UWSContext, payload: dict) -> bool:
        """ESCALATED_TO_HUMAN → QA_RUNNING: HUMAN authorizes."""
        return payload is not None and payload.get("human_decision") == "AUTHORIZED"


def create_initial_context(
    task_id: str,
    run_id: str,
    attempt_id: int,
    correlation_id: str,
    ledger: Ledger,
) -> UWSContext:
    """Create initial FSM context for a new task run."""
    return UWSContext(
        task_id=task_id,
        run_id=run_id,
        attempt_id=attempt_id,
        correlation_id=correlation_id,
        ledger=ledger,
        current_state=FSMState.TASK_ASSIGNED,
        rework_round=0,
        technical_failure_count=0,
    )
