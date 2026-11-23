"""
MORCH consumer for UTA Bridge receipts.

Reads and processes receipts from GitHub PR comments, emits ACK.
"""

import os
import json
import subprocess
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

from .schema import validate_receipt, canonicalize_receipt, compute_receipt_hash
from .ledger import BridgeLedger


class MORCHConsumer:
    """
    Consumes bridge receipts and emits ACK.

    This is the MORCH-side consumer that verifies receipts and acknowledges them.
    """

    DEFAULT_REPO = "PSM44/Upgrade_ToolsA"
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

    def fetch_receipts(self, state_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch receipts from GitHub PR comments."""
        if not self._check_gh_auth():
            raise RuntimeError("GitHub CLI not authenticated")

        try:
            result = subprocess.run(
                [self.gh_cli, "api", f"/repos/{self.repo}/issues/{self.pr_number}/comments"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to fetch comments: {result.stderr}")

            comments = json.loads(result.stdout)
            receipts = []

            for comment in comments:
                body = comment.get("body", "")
                if "UTA_BRIDGE_RECEIPT_START" not in body:
                    continue

                receipt = self._parse_receipt_comment(body)
                if receipt:
                    receipt["_gh_comment_id"] = comment["id"]
                    receipt["_gh_author"] = comment.get("user", {}).get("login")
                    receipt["_gh_created_at"] = comment.get("created_at")
                    receipts.append(receipt)

            # Filter by state if provided
            if state_filter:
                receipts = [r for r in receipts if r.get("state") == state_filter]

            return receipts

        except Exception as e:
            raise RuntimeError(f"Error fetching receipts: {e}")

    def _parse_receipt_comment(self, comment_body: str) -> Optional[Dict[str, Any]]:
        """Parse a receipt from a GitHub comment body."""
        pattern = r"<!-- UTA_BRIDGE_RECEIPT_START -->(.*?)<!-- UTA_BRIDGE_RECEIPT_END -->"
        match = re.search(pattern, comment_body, re.DOTALL)
        if not match:
            return None

        content = match.group(1)
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

    def verify_receipt(self, receipt: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Verify a receipt's integrity and validity.

        Returns (is_valid, list_of_errors).
        """
        from .schema import validate_receipt, compute_receipt_hash

        result = validate_receipt(receipt)
        if not result.valid:
            return False, result.errors

        # Verify artifact_sha256 matches canonical hash
        expected_hash = compute_receipt_hash(receipt)
        if receipt.get("artifact_sha256") != expected_hash:
            return False, [f"artifact_sha256 mismatch: expected {expected_hash}, got {receipt.get('artifact_sha256')}"]

        # Verify consumer matches
        if receipt.get("consumer") != "MORCH":
            return False, [f"Receipt not addressed to MORCH: {receipt.get('consumer')}"]

        return True, []

    def emit_ack(self, receipt: Dict[str, Any], ack_state: str = "ACK_RECEIVED") -> Tuple[bool, Optional[int]]:
        """
        Emit ACK as a GitHub PR comment.

        Returns (success, ack_comment_id).
        """
        if not self._check_gh_auth():
            raise RuntimeError("GitHub CLI not authenticated")

        ack_body = "\n".join([
            "<!-- UTA_BRIDGE_ACK_START -->",
            f"## UTA Bridge ACK for {receipt['receipt_id']}",
            "",
            f"**Receipt ID:** {receipt['receipt_id']}",
            f"**Idempotency Key:** {receipt['idempotency_key']}",
            f"**Task ID:** {receipt['task_id']}",
            f"**ACK State:** {ack_state}",
            f"**ACK By:** MORCH Consumer",
            f"**ACK At:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "",
            "<!-- UTA_BRIDGE_ACK_END -->",
        ])

        try:
            result = subprocess.run(
                [
                    self.gh_cli, "api", "--method", "POST",
                    f"/repos/{self.repo}/issues/{self.pr_number}/comments",
                    "-f", f"body={ack_body}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                return False, None

            response = json.loads(result.stdout)
            ack_comment_id = response.get("id")

            return True, ack_comment_id

        except Exception as e:
            return False, None

    def process_and_ack(self, receipt: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Process a receipt and emit ACK if valid.

        Returns (success, result_dict).
        """
        # Verify receipt
        valid, errors = self.verify_receipt(receipt)
        if not valid:
            return False, {"verified": False, "errors": errors}

        # Store in ledger as received
        message_id = receipt.get("receipt_id")
        self.ledger.update_state(
            message_id,
            "MORCH_REVIEW",
            github_comment_id=receipt.get("_gh_comment_id"),
            github_author=receipt.get("_gh_author"),
            github_repo=self.repo,
            github_html_url=f"https://github.com/{self.repo}/pull/{self.pr_number}#issuecomment-{receipt.get('_gh_comment_id')}",
            retrieved_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        # Emit ACK
        ack_success, ack_comment_id = self.emit_ack(receipt, "ACK_RECEIVED")

        if ack_success:
            self.ledger.mark_ack_received(message_id, ack_comment_id)

        return ack_success, {
            "verified": True,
            "ack_emitted": ack_success,
            "ack_comment_id": ack_comment_id,
            "receipt_id": message_id,
        }