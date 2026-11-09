"""
Idempotency key generation and duplicate detection for UWS.
"""

from __future__ import annotations

import hashlib
from typing import Optional


def generate_idempotency_key(
    task_id: str,
    run_id: str,
    attempt_id: int,
    event_type: str,
    parent_event_id: Optional[str] = None,
) -> str:
    """
    Generate deterministic idempotency key for an event.
    
    Key includes: task_id|run_id|attempt_id|event_type|parent_event_id
    Same event for same parent = same key (prevents duplicate processing)
    Same event type for different parent = different key (prevents replay attacks)
    """
    parent = parent_event_id or ""
    raw = f"{task_id}|{run_id}|{attempt_id}|{event_type}|{parent}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def generate_event_id() -> str:
    """Generate unique event ID (UUID4)."""
    import uuid
    return str(uuid.uuid4())


def generate_correlation_id() -> str:
    """Generate correlation ID for tracking related events."""
    import uuid
    return str(uuid.uuid4())


def generate_run_id() -> str:
    """Generate run ID (UUID4)."""
    import uuid
    return str(uuid.uuid4())


def generate_lease_id() -> str:
    """Generate lease ID (UUID4)."""
    import uuid
    return str(uuid.uuid4())


def generate_artifact_id() -> str:
    """Generate artifact ID (UUID4)."""
    import uuid
    return str(uuid.uuid4())


def generate_finding_id() -> str:
    """Generate finding ID (UUID4)."""
    import uuid
    return str(uuid.uuid4())


def compute_sha256(content: bytes) -> str:
    """Compute SHA256 of content."""
    return hashlib.sha256(content).hexdigest()


def compute_sha256_file(path: str) -> str:
    """Compute SHA256 of file content."""
    import os
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def compute_manifest_sha256(deliverables: list[dict]) -> str:
    """
    Compute manifest SHA256 from deliverable list.
    Deliverables must be sorted by path for determinism.
    """
    # Sort by path for deterministic manifest
    sorted_deliverables = sorted(deliverables, key=lambda d: d.get("path", ""))
    import json
    manifest_json = json.dumps(sorted_deliverables, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(manifest_json.encode()).hexdigest()


def verify_idempotency_key(
    provided_key: str,
    task_id: str,
    run_id: str,
    attempt_id: int,
    event_type: str,
    parent_event_id: Optional[str] = None,
) -> bool:
    """Verify that a provided idempotency key matches expected."""
    expected = generate_idempotency_key(task_id, run_id, attempt_id, event_type, parent_event_id)
    return provided_key == expected
