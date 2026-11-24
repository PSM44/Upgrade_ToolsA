"""
Receipt schema validation for UTA Bridge.

Defines the canonical receipt format and validation logic per bridge contract.
"""

import json
import re
from dataclasses import dataclass, fields
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

# Required receipt fields per bridge contract
REQUIRED_FIELDS = [
    "receipt_id",
    "idempotency_key",
    "task_id",
    "producer",
    "consumer",
    "state",
    "artifact_uri",
    "artifact_sha256",
    "evidence_class",
    "created_at",
    "contract_version",
    "test_only",
]

# Valid evidence classes per documentation
VALID_EVIDENCE_CLASSES = {
    "HUMAN_DECISION",
    "PRIMARY_DOCUMENT",
    "RELEASE_ARTIFACT",
    "RUNTIME_EVIDENCE",
    "REPORTED_ONLY",
    "HISTORICAL_REFERENCE",
    "CONTRADICTED",
    "NOT_FOUND",
    "TOOLCHAIN_REHEARSAL",
    "USER_DECLARED_BASELINE",
}

# Valid states for receipt lifecycle
VALID_STATES = {
    "EMITTED",
    "PERSISTED",
    "DELIVERED",
    "ACK_RECEIVED",
    "MORCH_REVIEW",
    "ACCEPTED",
    "REWORK_REQUIRED",
    "BLOCKED",
    "FAILED",
}

# UUID v4 regex pattern
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# SHA-256 hex pattern
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)

# Empty stream SHA-256 (SHA-256 of empty input)
EMPTY_STREAM_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# ISO 8601 UTC datetime pattern (with Z suffix)
ISO8601_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


@dataclass
class ValidationResult:
    """Result of receipt validation."""
    valid: bool
    errors: List[str]
    warnings: List[str]

    def __bool__(self) -> bool:
        return self.valid


def validate_field_presence(receipt: Dict[str, Any]) -> List[str]:
    """Check all required fields are present."""
    missing = [f for f in REQUIRED_FIELDS if f not in receipt]
    return [f"Missing required field: {m}" for m in missing]


def validate_receipt_id(value: str) -> List[str]:
    """Validate receipt_id is a valid UUID v4."""
    if not UUID_PATTERN.match(value):
        return [f"receipt_id must be UUID v4, got: {value}"]
    return []


def validate_idempotency_key(value: str) -> List[str]:
    """Validate idempotency_key is non-empty string without timestamp."""
    if not value or not isinstance(value, str):
        return ["idempotency_key must be non-empty string"]
    # Should not contain ISO timestamp pattern
    if re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value):
        return ["idempotency_key must not contain timestamp"]
    return []


def validate_task_id(value: str) -> List[str]:
    """Validate task_id format."""
    if not value or not isinstance(value, str):
        return ["task_id must be non-empty string"]
    return []


def validate_producer_consumer(value: str, field_name: str) -> List[str]:
    """Validate producer/consumer fields."""
    if value not in {"MORCH", "RORCH", "DSH/RORCH", "DSH/RORCH"}:
        return [f"{field_name} must be one of: MORCH, RORCH, DSH/RORCH"]
    return []


def validate_state(value: str) -> List[str]:
    """Validate state is a valid lifecycle state."""
    if value not in VALID_STATES:
        return [f"state must be one of: {', '.join(sorted(VALID_STATES))}"]
    return []


def validate_artifact_uri(value: str) -> List[str]:
    """Validate artifact_uri is a valid URI."""
    if not value or not isinstance(value, str):
        return ["artifact_uri must be non-empty string"]
    # Allow file://, https://, github:// schemes
    if not re.match(r"^(file|https|github)://", value):
        return [f"artifact_uri must use file://, https://, or github:// scheme, got: {value}"]
    return []


def validate_artifact_sha256(value: str) -> List[str]:
    """Validate artifact_sha256 is valid SHA-256 hex."""
    if not SHA256_PATTERN.match(value):
        return [f"artifact_sha256 must be 64-char hex, got: {value}"]
    # Reject empty stream hash
    if value.lower() == EMPTY_STREAM_SHA256:
        return ["artifact_sha256 must not be empty stream hash (SHA-256 of empty input)"]
    return []


# Cross-field validators (prefixed with _)
CROSS_FIELD_VALIDATORS = {}


def validate_evidence_class(value: str) -> List[str]:
    """Validate evidence_class is a recognized class."""
    if value not in VALID_EVIDENCE_CLASSES:
        return [f"evidence_class must be one of: {', '.join(sorted(VALID_EVIDENCE_CLASSES))}"]
    return []


def validate_created_at(value: str) -> List[str]:
    """Validate created_at is ISO 8601 UTC."""
    if not ISO8601_UTC_PATTERN.match(value):
        return [f"created_at must be ISO 8601 UTC (ending in Z), got: {value}"]
    # Additional validation: parse to ensure valid date
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return [f"created_at is not a valid datetime: {value}"]
    return []


def validate_contract_version(value: str) -> List[str]:
    """Validate contract_version format."""
    if not value or not isinstance(value, str):
        return ["contract_version must be non-empty string"]
    if not re.match(r"^[a-z0-9-]+$", value):
        return [f"contract_version must be lowercase alphanumeric with hyphens, got: {value}"]
    return []


def validate_test_only(value: Any) -> List[str]:
    """Validate test_only is boolean."""
    if not isinstance(value, bool):
        return ["test_only must be boolean (true/false)"]
    return []


# Validator mapping
VALIDATORS = {
    "receipt_id": validate_receipt_id,
    "idempotency_key": validate_idempotency_key,
    "task_id": validate_task_id,
    "producer": lambda v: validate_producer_consumer(v, "producer"),
    "consumer": lambda v: validate_producer_consumer(v, "consumer"),
    "state": validate_state,
    "artifact_uri": validate_artifact_uri,
    "artifact_sha256": validate_artifact_sha256,
    "evidence_class": validate_evidence_class,
    "created_at": validate_created_at,
    "contract_version": validate_contract_version,
    "test_only": validate_test_only,
}


def validate_receipt(receipt: Dict[str, Any]) -> ValidationResult:
    """
    Validate a receipt against the bridge contract.

    Returns ValidationResult with valid flag, errors, and warnings.
    """
    errors = []
    warnings = []

    # Check required fields
    errors.extend(validate_field_presence(receipt))
    if errors:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    # Run per-field validators
    for field, validator in VALIDATORS.items():
        if field in receipt:
            errors.extend(validator(receipt[field]))

    # Run cross-field validators
    for name, validator in CROSS_FIELD_VALIDATORS.items():
        errors.extend(validator(receipt))

    # Cross-field validation
    if receipt.get("test_only") is True:
        if not receipt.get("task_id", "").startswith("TEST"):
            warnings.append("test_only=true but task_id doesn't start with 'TEST'")

    # Check for unexpected sensitive fields
    sensitive_patterns = ["secret", "token", "key", "password", "credential"]
    for key in receipt.keys():
        if any(p in key.lower() for p in sensitive_patterns):
            warnings.append(f"Potentially sensitive field name: {key}")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def canonicalize_receipt(receipt: Dict[str, Any]) -> str:
    """
    Produce canonical JSON string for hashing.

    Sorts keys, uses compact separators, ensures consistent formatting.
    """
    # Only include contract-defined fields in canonical form
    canonical = {k: receipt[k] for k in REQUIRED_FIELDS if k in receipt}
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def compute_receipt_hash(receipt: Dict[str, Any]) -> str:
    """
    Compute SHA-256 of canonicalized receipt.

    Returns hex-encoded hash.
    """
    import hashlib
    canonical = canonicalize_receipt(receipt)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()