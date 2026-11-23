"""
GitHub emitter for UTA Bridge receipts.

Emits receipts as PR comments on the canonical repository.
Uses the existing GitHub CLI (gh) or GitHub API.
"""

import os
import json
import subprocess
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse

from .schema import validate_receipt, canonicalize_receipt, compute_receipt_hash
from .ledger import BridgeLedger


class GitHubEmitter:
    """
    Emits bridge receipts as GitHub PR comments.

    The canonical transport is GitHub PR comments per HERMES_BRIDGE_ADR.md.
    """

    # Canonical repository
    DEFAULT_REPO = "PSM44/Upgrade_ToolsA"
    # Default PR for bridge test messages (Issue #2 per ledger data)
    DEFAULT_PR_NUMBER = 2

    def __init__(
        self,
        repo: str = DEFAULT_REPO,
        pr_number: int = DEFAULT_PR_NUMBER,
        ledger: Optional[BridgeLedger] = None,
        gh_cli: str = "gh",
    ):
        self.repo = repo
        self.pr_number = pr_number
        self.ledger = ledger or BridgeLedger()
        self.gh_cli = gh_cli

    def _check_gh_auth(self) -> bool:
        """Verify GitHub CLI is authenticated."""
        try:
            result = subprocess.run(
                [self.gh_cli, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def _format_receipt_comment(self, receipt: Dict[str, Any]) -> str:
        """Format receipt as a structured GitHub comment."""
        lines = [
            "<!-- UTA_BRIDGE_RECEIPT_START -->",
            f"## UTA Bridge Receipt: {receipt['receipt_id']}",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| receipt_id | {receipt['receipt_id']} |",
            f"| idempotency_key | {receipt['idempotency_key']} |",
            f"| task_id | {receipt['task_id']} |",
            f"| producer | {receipt['producer']} |",
            f"| consumer | {receipt['consumer']} |",
            f"| state | {receipt['state']} |",
            f"| artifact_uri | {receipt['artifact_uri']} |",
            f"| artifact_sha256 | {receipt['artifact_sha256']} |",
            f"| evidence_class | {receipt['evidence_class']} |",
            f"| created_at | {receipt['created_at']} |",
            f"| contract_version | {receipt['contract_version']} |",
            f"| test_only | {str(receipt['test_only']).lower()} |",
            "",
            "<!-- UTA_BRIDGE_RECEIPT_END -->",
        ]
        return "\n".join(lines)

    def _parse_receipt_comment(self, comment_body: str) -> Optional[Dict[str, Any]]:
        """Parse a receipt from a GitHub comment body."""
        # Find the receipt table
        pattern = r"<!-- UTA_BRIDGE_RECEIPT_START -->(.*?)<!-- UTA_BRIDGE_RECEIPT_END -->"
        match = re.search(pattern, comment_body, re.DOTALL)
        if not match:
            return None

        content = match.group(1)
        # Parse the markdown table
        receipt = {}
        for line in content.split("\n"):
            if "|" in line and not line.startswith("| Field") and not line.startswith("|---"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3:
                    key = parts[1]
                    value = parts[2]
                    if key in ["test_only"]:
                        value = value.lower() == "true"
                    receipt[key] = value
        return receipt if receipt else None

    def emit(self, receipt: Any, dry_run: bool = False) -> Tuple[bool, Optional[str], Optional[int]]:
        """
        Emit a receipt as a GitHub PR comment.

        Returns (success, comment_url, comment_id).
        """
        # Convert Receipt dataclass to dict if needed
        if hasattr(receipt, 'to_dict'):
            receipt_dict = receipt.to_dict()
        else:
            receipt_dict = receipt

        # Validate receipt
        result = validate_receipt(receipt_dict)
        if not result.valid:
            raise ValueError(f"Invalid receipt: {result.errors}")

        if dry_run:
            # Dry run: validate but don't store in ledger or call GitHub
            return True, None, None

        # Check GitHub auth
        if not self._check_gh_auth():
            raise RuntimeError("GitHub CLI not authenticated. Run 'gh auth login'.")

        # Store in ledger first (outbox)
        message_id = self.ledger.store_outbox(receipt_dict)
        if message_id != receipt_dict["receipt_id"]:
            # Idempotent: already stored
            existing = self.ledger.get_by_id(message_id)
            if existing and existing.get("state") in ("DELIVERED", "ACK_RECEIVED"):
                return True, existing.get("github_html_url"), existing.get("github_comment_id")

        # Format comment
        comment_body = self._format_receipt_comment(receipt_dict)

        # Post comment via gh CLI
        try:
            result = subprocess.run(
                [
                    self.gh_cli, "api", "--method", "POST",
                    f"/repos/{self.repo}/issues/{self.pr_number}/comments",
                    "-f", f"body={comment_body}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                self.ledger.mark_blocked(message_id, f"GitHub API error: {error_msg}")
                return False, None, None

            # Parse response for comment_id and html_url
            response = json.loads(result.stdout)
            comment_id = response.get("id")
            comment_url = response.get("html_url")

            # Update ledger with delivery info
            self.ledger.mark_delivered(
                message_id,
                comment_id,
                response.get("user", {}).get("login", "unknown"),
                self.repo,
                comment_url,
            )

            return True, comment_url, comment_id

        except subprocess.TimeoutExpired:
            self.ledger.mark_blocked(message_id, "GitHub API timeout")
            return False, None, None
        except json.JSONDecodeError:
            self.ledger.mark_blocked(message_id, "Invalid GitHub API response")
            return False, None, None
        except Exception as e:
            self.ledger.mark_blocked(message_id, f"Emit error: {str(e)}")
            return False, None, None

    def poll_ack(self, message_id: str, timeout_seconds: int = 60) -> Tuple[bool, Optional[Dict]]:
        """
        Poll for ACK from consumer (MORCH).

        Checks GitHub PR comments for ACK receipt from consumer.
        """
        # Get the message from ledger
        msg = self.ledger.get_by_id(message_id)
        if not msg:
            return False, {"error": "Message not found in ledger"}

        if not msg.get("github_comment_id"):
            return False, {"error": "Message not yet delivered"}

        comment_id = msg["github_comment_id"]

        # Use gh to get comment reactions/replies
        # In this implementation, ACK is a reply comment from MORCH
        # containing the receipt_id and idempotency_key
        try:
            result = subprocess.run(
                [
                    self.gh_cli, "api",
                    f"/repos/{self.repo}/issues/{self.pr_number}/comments",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if result.returncode != 0:
                return False, {"error": "Failed to fetch comments"}

            comments = json.loads(result.stdout)

            # Look for ACK comment from MORCH (after our comment)
            for comment in comments:
                if comment.get("id") <= comment_id:
                    continue
                body = comment.get("body", "")
                if "UTA_BRIDGE_ACK" in body and msg["message_id"] in body:
                    # Parse ACK
                    ack_data = {
                        "ack_comment_id": comment["id"],
                        "ack_author": comment.get("user", {}).get("login"),
                        "ack_at": comment.get("created_at"),
                        "ack_body": body,
                    }
                    # Update ledger
                    self.ledger.mark_ack_received(message_id, comment["id"])
                    return True, ack_data

            return False, {"status": "no_ack_yet"}

        except Exception as e:
            return False, {"error": str(e)}