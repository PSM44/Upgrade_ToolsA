"""
Artifact manifest computation, SHA256, and invalidation for UWS.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import ArtifactRecord, Deliverable
from .idempotency import compute_sha256_file, generate_artifact_id


@dataclass
class ArtifactManifest:
    """Complete artifact manifest for a submission."""
    task_id: str
    run_id: str
    deliverables: list[Deliverable]
    manifest_sha256: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "deliverables": [d.__dict__ for d in self.deliverables],
            "manifest_sha256": self.manifest_sha256,
            "created_at": self.created_at,
        }


def compute_deliverable_sha256(path: str) -> str:
    """Compute SHA256 of a deliverable file."""
    return compute_sha256_file(path)


def create_deliverable(path: str, description: str = "") -> Deliverable:
    """Create a Deliverable record from a file path."""
    sha256 = compute_deliverable_sha256(path)
    return Deliverable(path=path, sha256=sha256, description=description)


def build_manifest(task_id: str, run_id: str, deliverable_paths: list[str]) -> ArtifactManifest:
    """Build artifact manifest from list of file paths."""
    deliverables = [create_deliverable(p) for p in deliverable_paths]
    manifest_sha256 = compute_manifest_sha256([d.__dict__ for d in deliverables])
    created_at = datetime.now(timezone.utc).isoformat()
    return ArtifactManifest(
        task_id=task_id,
        run_id=run_id,
        deliverables=deliverables,
        manifest_sha256=manifest_sha256,
        created_at=created_at,
    )


def compute_manifest_sha256(deliverables: list[dict]) -> str:
    """Compute deterministic manifest SHA256 from deliverable dicts."""
    sorted_deliverables = sorted(deliverables, key=lambda d: d.get("path", ""))
    manifest_json = json.dumps(sorted_deliverables, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(manifest_json.encode()).hexdigest()


def create_artifact_record(
    task_id: str,
    run_id: str,
    path: str,
    description: str = "",
    invalidated_at: Optional[str] = None,
    invalidation_reason: Optional[str] = None,
) -> ArtifactRecord:
    """Create an ArtifactRecord for ledger storage."""
    sha256 = compute_sha256_file(path)
    return ArtifactRecord(
        artifact_id=generate_artifact_id(),
        task_id=task_id,
        run_id=run_id,
        path=path,
        sha256=sha256,
        description=description,
        created_at=datetime.now(timezone.utc).isoformat(),
        invalidated_at=invalidated_at,
        invalidation_reason=invalidation_reason,
    )


def invalidate_artifact(artifact: ArtifactRecord, reason: str) -> ArtifactRecord:
    """Return new ArtifactRecord with invalidation info (immutable pattern)."""
    return ArtifactRecord(
        artifact_id=artifact.artifact_id,
        task_id=artifact.task_id,
        run_id=artifact.run_id,
        path=artifact.path,
        sha256=artifact.sha256,
        description=artifact.description,
        created_at=artifact.created_at,
        invalidated_at=datetime.now(timezone.utc).isoformat(),
        invalidation_reason=reason,
    )


def verify_manifest(manifest: ArtifactManifest, base_path: str = "") -> tuple[bool, list[str]]:
    """
    Verify all deliverables in manifest match their recorded SHA256.
    Returns (all_valid, list_of_mismatched_paths).
    """
    mismatches = []
    for d in manifest.deliverables:
        full_path = os.path.join(base_path, d.path) if base_path else d.path
        if not os.path.exists(full_path):
            mismatches.append(f"{d.path}: MISSING")
            continue
        actual_sha256 = compute_sha256_file(full_path)
        if actual_sha256 != d.sha256:
            mismatches.append(f"{d.path}: expected {d.sha256}, got {actual_sha256}")
    return len(mismatches) == 0, mismatches


def verify_manifest_matches_submission(
    manifest_sha256: str,
    qa_submission_manifest_sha256: str,
) -> bool:
    """Verify that QA verdict's parent submission manifest matches current artifact manifest."""
    return manifest_sha256 == qa_submission_manifest_sha256
