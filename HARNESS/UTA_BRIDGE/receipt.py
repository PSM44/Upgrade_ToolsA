"""
Receipt creation and management for UTA Bridge.

Provides utilities to create valid receipts with all required fields.
"""

import uuid
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict

from .schema import (
    validate_receipt,
    canonicalize_receipt,
    compute_receipt_hash,
    REQUIRED_FIELDS,
    VALID_STATES,
    VALID_EVIDENCE_CLASSES,
)
CONTRACT_VERSION = "uta-bridge-v1"


@dataclass
class Receipt:
    """Typed receipt data class."""
    receipt_id: str
    idempotency_key: str
    task_id: str
    producer: str
    consumer: str
    state: str
    artifact_uri: str
    artifact_sha256: str
    evidence_class: str
    created_at: str
    contract_version: str
    test_only: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def generate_idempotency_key(task_id: str, producer: str, artifact_sha256: str) -> str:
    """
    Generate deterministic idempotency key.

    Combines task_id, producer, and artifact hash (not timestamp).
    """
    import hashlib
    combined = f"{task_id}|{producer}|{artifact_sha256}"
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def create_receipt(
    task_id: str,
    producer: str,
    consumer: str,
    state: str,
    artifact_uri: str,
    artifact_sha256: str,
    evidence_class: str,
    test_only: bool = False,
    contract_version: str = CONTRACT_VERSION,
) -> Receipt:
    """
    Create a valid bridge receipt.

    Args:
        task_id: Task identifier
        producer: Producer identity (MORCH, RORCH, DSH/RORCH)
        consumer: Consumer identity (MORCH, RORCH, DSH/RORCH)
        state: Receipt lifecycle state
        artifact_uri: Durable URI for artifact (file://, https://, github://)
        artifact_sha256: SHA-256 of the artifact
        evidence_class: Evidence classification
        test_only: Whether this is a test receipt
        contract_version: Bridge contract version

    Returns:
        Receipt object with all required fields populated.
    """
    # Validate inputs
    if state not in VALID_STATES:
        raise ValueError(f"Invalid state: {state}. Must be one of: {VALID_STATES}")

    if evidence_class not in VALID_EVIDENCE_CLASSES:
        raise ValueError(f"Invalid evidence_class: {evidence_class}. Must be one of: {VALID_EVIDENCE_CLASSES}")

    if not artifact_sha256 or len(artifact_sha256) != 64:
        raise ValueError("artifact_sha256 must be 64-character hex string")

    # Generate deterministic idempotency key
    idempotency_key = generate_idempotency_key(task_id, producer, artifact_sha256)

    # Create receipt
    receipt = Receipt(
        receipt_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        task_id=task_id,
        producer=producer,
        consumer=consumer,
        state=state,
        artifact_uri=artifact_uri,
        artifact_sha256=artifact_sha256,
        evidence_class=evidence_class,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        contract_version=contract_version,
        test_only=test_only,
    )

    # Validate
    result = validate_receipt(receipt.to_dict())
    if not result.valid:
        raise ValueError(f"Created receipt is invalid: {result.errors}")

    return receipt


def create_test_receipt(
    task_id: str = "TEST-UTA-BRIDGE-001",
    artifact_sha256: str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
) -> Receipt:
    """
    Create a synthetic test receipt for acceptance testing.

    Uses a known SHA-256 of empty content for deterministic testing.
    """
    return create_receipt(
        task_id=task_id,
        producer="DSH/RORCH",
        consumer="MORCH",
        state="EMITTED",
        artifact_uri="github://PSM44/Upgrade_ToolsA/test/bridge-test",
        artifact_sha256=artifact_sha256,
        evidence_class="REPORTED_ONLY",
        test_only=True,
    )


def compute_receipt_hash(receipt: Dict[str, Any]) -> str:
    """Compute SHA-256 of canonicalized receipt."""
    from .schema import compute_receipt_hash as _compute_hash
    return _compute_hash(receipt)