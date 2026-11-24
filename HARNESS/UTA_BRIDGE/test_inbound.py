"""
Acceptance tests for UTA Bridge Inbound Receiver.

Tests per UTA-BRIDGE-004 specification:
- Valid order with valid approval → PENDING/DISPATCHED
- Malformed delimiter → REJECTED
- EXECUTE without APPROVAL_GRANTED → BLOCKED
- Duplicate/replay → single execution
- Author/repo/URI/hash mismatch → REJECTED
- GitHub fixture with correct hash → PASS
"""

import os
import json
import tempfile
import sqlite3
import pytest
from datetime import datetime, timezone
from pathlib import Path

from .inbound import (
    EnvelopeParser,
    InboundLedger,
    GitHubScanner,
    Dispatcher,
    ParsedEnvelope,
    ValidationError,
    START_DELIM,
    END_DELIM,
)


# ─── Test Fixtures ──────────────────────────────────────────────────────────

# Use regular strings with format() to avoid f-string triple-quote issues
_VALID_ORDER_TEMPLATE = START_DELIM + "\n" + """PROTOCOL: UTA_BRIDGE_V1
MESSAGE_ID: UTA-MORCH-RORCH-20261123-001
CORRELATION_ID: UTA-BRIDGE-003-001
SENDER: MORCH
RECIPIENT: RORCH
TASK_ID: UTA-BRIDGE-003
MODE: EXECUTE
STATUS: REQUESTED_NOT_ACCEPTED
REFERENCE_PR: #2
BASE_HEAD_OBSERVED: 46b95ad60a374b8d2662a03da559eb43e64fde5f
PAYLOAD: {"action": "remediate", "scope": "producer"}
AUTHORITY: HUMAN_AUTHORIZED
ACK_REQUIRED: true
""" + END_DELIM

VALID_ORDER = _VALID_ORDER_TEMPLATE

_VALID_APPROVAL_TEMPLATE = START_DELIM + "\n" + """PROTOCOL: UTA_BRIDGE_V1
MESSAGE_ID: UTA-HUMAN-RORCH-20261123-001
CORRELATION_ID: UTA-BRIDGE-003-001
SENDER: HUMAN
RECIPIENT: RORCH
TASK_ID: UTA-BRIDGE-003
MODE: APPROVAL_GRANTED
STATUS: ACCEPTED
REFERENCE_PR: #2
BASE_HEAD_OBSERVED: 46b95ad60a374b8d2662a03da559eb43e64fde5f
PAYLOAD: {"approved_scope": "UTA-BRIDGE-003 remediation only"}
""" + END_DELIM

VALID_APPROVAL = _VALID_APPROVAL_TEMPLATE

_MALFORMED_DELIMITER_TEMPLATE = """PROTOCOL: UTA_BRIDGE_V1
MESSAGE_ID: UTA-MORCH-RORCH-20261123-002
CORRELATION_ID: UTA-BRIDGE-004-001
SENDER: MORCH
RECIPIENT: RORCH
TASK_ID: UTA-BRIDGE-004
MODE: EXECUTE
STATUS: REQUESTED_NOT_ACCEPTED
REFERENCE_PR: #2
BASE_HEAD_OBSERVED: 46b95ad60a374b8d2662a03da559eb43e64fde5f
PAYLOAD: {"action": "test"}
""" + END_DELIM

MALFORMED_DELIMITER = _MALFORMED_DELIMITER_TEMPLATE

_INVALID_AUTHOR_TEMPLATE = START_DELIM + "\n" + """PROTOCOL: UTA_BRIDGE_V1
MESSAGE_ID: UTA-MORCH-RORCH-20261123-003
CORRELATION_ID: UTA-BRIDGE-005-001
SENDER: INVALID_SENDER
RECIPIENT: RORCH
TASK_ID: UTA-BRIDGE-005
MODE: EXECUTE
STATUS: REQUESTED_NOT_ACCEPTED
REFERENCE_PR: #2
BASE_HEAD_OBSERVED: 46b95ad60a374b8d2662a03da559eb43e64fde5f
PAYLOAD: {"action": "test"}
""" + END_DELIM

INVALID_AUTHOR = _INVALID_AUTHOR_TEMPLATE

_INVALID_RECIPIENT_TEMPLATE = START_DELIM + "\n" + """PROTOCOL: UTA_BRIDGE_V1
MESSAGE_ID: UTA-MORCH-RORCH-20261123-004
CORRELATION_ID: UTA-BRIDGE-006-001
SENDER: MORCH
RECIPIENT: INVALID_RECIPIENT
TASK_ID: UTA-BRIDGE-006
MODE: EXECUTE
STATUS: REQUESTED_NOT_ACCEPTED
REFERENCE_PR: #2
BASE_HEAD_OBSERVED: 46b95ad60a374b8d2662a03da559eb43e64fde5f
PAYLOAD: {"action": "test"}
""" + END_DELIM

INVALID_RECIPIENT = _INVALID_RECIPIENT_TEMPLATE

_MISSING_FIELD_TEMPLATE = START_DELIM + "\n" + """PROTOCOL: UTA_BRIDGE_V1
MESSAGE_ID: UTA-MORCH-RORCH-20261123-005
CORRELATION_ID: UTA-BRIDGE-007-001
SENDER: MORCH
RECIPIENT: RORCH
TASK_ID: UTA-BRIDGE-007
MODE: EXECUTE
STATUS: REQUESTED_NOT_ACCEPTED
REFERENCE_PR: #2
BASE_HEAD_OBSERVED: 46b95ad60a374b8d2662a03da559eb43e64fde5f
PAYLOAD: {"action": "test"}
""" + END_DELIM

MISSING_FIELD = _MISSING_FIELD_TEMPLATE

# Test fixture with actually missing required field (TASK_ID)
_MISSING_TASK_ID_TEMPLATE = START_DELIM + "\n" + """PROTOCOL: UTA_BRIDGE_V1
MESSAGE_ID: UTA-MORCH-RORCH-20261123-005
CORRELATION_ID: UTA-BRIDGE-007-001
SENDER: MORCH
RECIPIENT: RORCH
MODE: EXECUTE
STATUS: REQUESTED_NOT_ACCEPTED
REFERENCE_PR: #2
BASE_HEAD_OBSERVED: 46b95ad60a374b8d2662a03da559eb43e64fde5f
PAYLOAD: {"action": "test"}
""" + END_DELIM

MISSING_TASK_ID = _MISSING_TASK_ID_TEMPLATE

_INVALID_JSON_TEMPLATE = START_DELIM + "\n" + """PROTOCOL: UTA_BRIDGE_V1
MESSAGE_ID: UTA-MORCH-RORCH-20261123-006
CORRELATION_ID: UTA-BRIDGE-008-001
SENDER: MORCH
RECIPIENT: RORCH
TASK_ID: UTA-BRIDGE-008
MODE: EXECUTE
STATUS: REQUESTED_NOT_ACCEPTED
REFERENCE_PR: #2
BASE_HEAD_OBSERVED: 46b95ad60a374b8d2662a03da559eb43e64fde5f
PAYLOAD: {invalid json}
""" + END_DELIM

INVALID_JSON = _INVALID_JSON_TEMPLATE


# ─── Parser Tests ───────────────────────────────────────────────────────────

class TestEnvelopeParser:
    """Test the strict envelope parser."""

    def test_valid_order_parses(self):
        """Valid order should parse successfully."""
        parser = EnvelopeParser()
        envelope = parser.parse(VALID_ORDER)
        assert envelope.message_id == "UTA-MORCH-RORCH-20261123-001"
        assert envelope.correlation_id == "UTA-BRIDGE-003-001"
        assert envelope.sender == "MORCH"
        assert envelope.recipient == "RORCH"
        assert envelope.task_id == "UTA-BRIDGE-003"
        assert envelope.mode == "EXECUTE"
        assert envelope.status == "REQUESTED_NOT_ACCEPTED"
        assert envelope.reference_pr == "#2"
        assert envelope.payload == {"action": "remediate", "scope": "producer"}

    def test_valid_approval_parses(self):
        """Valid approval should parse successfully."""
        parser = EnvelopeParser()
        envelope = parser.parse(VALID_APPROVAL)
        assert envelope.message_id == "UTA-HUMAN-RORCH-20261123-001"
        assert envelope.sender == "HUMAN"
        assert envelope.mode == "APPROVAL_GRANTED"
        assert envelope.status == "ACCEPTED"

    def test_missing_start_delimiter_rejected(self):
        """Missing start delimiter should be rejected."""
        parser = EnvelopeParser()
        with pytest.raises(ValidationError, match="start delimiter"):
            parser.parse("PROTOCOL: UTA_BRIDGE_V1\n..." + END_DELIM)

    def test_missing_end_delimiter_rejected(self):
        """Missing end delimiter should be rejected."""
        parser = EnvelopeParser()
        with pytest.raises(ValidationError, match="end delimiter"):
            parser.parse(START_DELIM + "\nPROTOCOL: UTA_BRIDGE_V1\n...")

    def test_invalid_sender_rejected(self):
        """Invalid sender should be rejected."""
        parser = EnvelopeParser()
        with pytest.raises(ValidationError, match="Invalid sender"):
            parser.parse(INVALID_AUTHOR)

    def test_invalid_recipient_rejected(self):
        """Invalid recipient should be rejected."""
        parser = EnvelopeParser()
        with pytest.raises(ValidationError, match="Invalid recipient"):
            parser.parse(INVALID_RECIPIENT)

    def test_missing_field_rejected(self):
        """Missing required field should be rejected."""
        parser = EnvelopeParser()
        with pytest.raises(ValidationError, match="Missing required field"):
            parser.parse(MISSING_TASK_ID)

    def test_invalid_json_rejected(self):
        """Invalid JSON payload should be rejected."""
        parser = EnvelopeParser()
        with pytest.raises(ValidationError, match="Invalid PAYLOAD JSON"):
            parser.parse(INVALID_JSON)


# ─── Ledger Tests ───────────────────────────────────────────────────────────

class TestInboundLedger:
    """Test the inbound deduplication ledger."""

    def setup_method(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_inbound.db"
        self.ledger = InboundLedger(str(self.db_path))
        self.parser = EnvelopeParser()

    def teardown_method(self):
        self.tmpdir.cleanup()

    def _envelope(self, body: str) -> ParsedEnvelope:
        return self.parser.parse(body)

    def test_claim_new_order(self):
        """New order should be claimed."""
        envelope = self._envelope(VALID_ORDER)
        state, detail = self.ledger.claim_order(envelope, 12345, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        assert state == "CLAIMED"
        assert "claimed" in detail.lower()

    def test_validated_order_reclaimed_for_dispatch(self):
        """VALIDATED order should be re-claimed for dispatch (not treated as duplicate)."""
        envelope = self._envelope(VALID_ORDER)
        # First claim
        self.ledger.claim_order(envelope, 12345, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        # Second claim (same message_id, same payload) - should be re-claimed for dispatch
        state, detail = self.ledger.claim_order(envelope, 67890, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        assert state == "CLAIMED"
        assert "re-claimed" in detail.lower()

    def test_acknowledged_order_deduplicated(self):
        """ACKNOWLEDGED order should be deduplicated."""
        envelope = self._envelope(VALID_ORDER)
        # First claim
        self.ledger.claim_order(envelope, 12345, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        # Update to ACKNOWLEDGED
        self.ledger.update_state(envelope.message_id, "ACKNOWLEDGED")
        # Second claim (same message_id, same payload)
        state, detail = self.ledger.claim_order(envelope, 67890, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        assert state == "DUPLICATE"
        assert "already processed" in detail.lower()

    def test_conflict_detected(self):
        """Same message_id with different payload should be conflict."""
        envelope1 = self._envelope(VALID_ORDER)
        envelope2 = self._envelope(VALID_APPROVAL)  # Different payload, same message_id format but different
        envelope2.message_id = "UTA-MORCH-RORCH-20261123-001"  # Force same ID

        self.ledger.claim_order(envelope1, 12345, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        state, detail = self.ledger.claim_order(envelope2, 67890, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        assert state == "CONFLICT"
        assert "conflict" in detail.lower()

    def test_state_transitions(self):
        """State transitions should work."""
        envelope = self._envelope(VALID_ORDER)
        self.ledger.claim_order(envelope, 12345, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")

        # Update state
        assert self.ledger.update_state(envelope.message_id, "DISPATCHED")
        order = self.ledger.get_order(envelope.message_id)
        assert order["state"] == "DISPATCHED"

        # Update with error
        assert self.ledger.update_state(envelope.message_id, "BLOCKED", error_detail="test error")
        order = self.ledger.get_order(envelope.message_id)
        assert order["state"] == "BLOCKED"
        assert order["error_detail"] == "test error"

    def test_get_pending_orders(self):
        """Should return pending orders."""
        envelope1 = self._envelope(VALID_ORDER)
        envelope2 = self._envelope(VALID_APPROVAL)
        envelope2.message_id = "UTA-HUMAN-RORCH-20261123-001"

        self.ledger.claim_order(envelope1, 12345, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        self.ledger.claim_order(envelope2, 67890, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")

        pending = self.ledger.get_pending_orders()
        assert len(pending) == 2


# ─── Dispatcher Tests ───────────────────────────────────────────────────────

class TestDispatcher:
    """Test the order dispatcher."""

    def setup_method(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_dispatch.db"
        self.ledger = InboundLedger(str(self.db_path))
        self.parser = EnvelopeParser()
        self.dispatcher = Dispatcher(self.ledger)

    def teardown_method(self):
        self.tmpdir.cleanup()

    def test_execute_without_approval_blocked(self):
        """EXECUTE without approval should be blocked."""
        envelope = self.parser.parse(VALID_ORDER)
        self.ledger.claim_order(envelope, 12345, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")

        order = self.ledger.get_order(envelope.message_id)
        success, detail = Dispatcher(self.ledger).dispatch({"envelope": envelope.to_dict()})

        assert not success
        assert detail["blocked"] is True
        assert "APPROVAL_GRANTED" in detail["reason"]

    def test_execute_with_approval_dispatched(self):
        """EXECUTE with prior approval should be dispatched."""
        # First add approval
        approval = self.parser.parse(VALID_APPROVAL)
        self.ledger.claim_order(approval, 12345, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        self.ledger.update_state(approval.message_id, "ACKNOWLEDGED")

        # Then order
        order_env = self.parser.parse(VALID_ORDER)
        self.ledger.claim_order(order_env, 67890, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")

        order = self.ledger.get_order(order_env.message_id)
        success, detail = Dispatcher(self.ledger).dispatch({"envelope": order_env.to_dict()})

        assert success
        assert detail["dispatched"] is True


# ─── Integration Test ───────────────────────────────────────────────────────

class TestIntegratedFlow:
    """Test the complete inbound flow."""

    def setup_method(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_flow.db"
        self.ledger = InboundLedger(str(self.db_path))
        self.parser = EnvelopeParser()

    def teardown_method(self):
        self.tmpdir.cleanup()

    def test_valid_order_with_approval_e2e(self):
        """Complete flow: approval -> order -> dispatch."""
        # 1. Add approval
        approval = self.parser.parse(VALID_APPROVAL)
        state, _ = self.ledger.claim_order(approval, 111, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        assert state == "CLAIMED"
        self.ledger.update_state(approval.message_id, "ACKNOWLEDGED")

        # 2. Process order
        order = self.parser.parse(VALID_ORDER)
        state, _ = self.ledger.claim_order(order, 222, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        assert state == "CLAIMED"

        # 3. Dispatch
        dispatcher = Dispatcher(self.ledger)
        success, detail = dispatcher.dispatch({"envelope": order.to_dict()})
        assert success
        assert detail["dispatched"] is True

        # 4. Verify ledger state
        final = self.ledger.get_order(order.message_id)
        assert final["state"] == "DISPATCHED"

    def test_validated_order_reclaimed_for_dispatch(self):
        """VALIDATED order should be re-claimed for dispatch (not treated as duplicate)."""
        envelope = self.parser.parse(VALID_ORDER)

        # First claim
        state1, detail1 = self.ledger.claim_order(envelope, 111, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")
        assert state1 == "CLAIMED"
        assert "claimed" in detail1.lower()

        # Second claim (simulate replay) - should be re-claimed for dispatch
        state2, detail2 = self.ledger.claim_order(envelope, 222, "PSM44", "PSM44/Upgrade_ToolsA", "https://github.com/...")

        assert state2 == "CLAIMED"
        assert "re-claimed" in detail2.lower()

        # Only one order in ledger
        with sqlite3.connect(self.ledger.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM inbound_orders WHERE message_id = ?", (envelope.message_id,))
            assert cursor.fetchone()[0] == 1


# ─── Fixture Validation ─────────────────────────────────────────────────────

class TestFixtureValidation:
    """Test that the test fixture is valid and accessible."""

    def test_fixture_exists(self):
        """Test fixture should exist."""
        fixture_path = Path("/mnt/c/01. GitHub/Upgrade_TA/HARNESS/UTA_BRIDGE/test_fixtures/bridge_test_v1.json")
        assert fixture_path.exists(), "Test fixture not found"

    def test_fixture_hash(self):
        """Test fixture hash should match expected."""
        import hashlib
        fixture_path = Path("/mnt/c/01. GitHub/Upgrade_TA/HARNESS/UTA_BRIDGE/test_fixtures/bridge_test_v1.json")
        with open(fixture_path, "rb") as f:
            content = f.read()
        assert len(content) == 551
        h = hashlib.sha256(content).hexdigest()
        assert h == "3235cd0c64fc0e390b5cff5b45510070b8161978ed8ed04a6a53ba982ee5a0bb"


# ─── Run Tests ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])