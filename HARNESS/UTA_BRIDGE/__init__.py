"""
UTA Bridge — MORCH↔RORCH durable transport via GitHub PR comments.

This is the canonical implementation of the UTA Bridge per HERMES_BRIDGE_ADR.md.
The ADR status: DESIGN APPROVED / IMPLEMENTATION NOT VERIFIED.
This implementation provides the verified transport layer.
"""

from .schema import validate_receipt, canonicalize_receipt
from .ledger import BridgeLedger
from .emitter import GitHubEmitter
from .consumer import MORCHConsumer
from .receipt import Receipt, create_receipt, compute_receipt_hash, CONTRACT_VERSION

__all__ = [
    "validate_receipt",
    "canonicalize_receipt",
    "BridgeLedger",
    "GitHubEmitter",
    "MORCHConsumer",
    "Receipt",
    "create_receipt",
    "compute_receipt_hash",
    "CONTRACT_VERSION",
]

__version__ = "0.1.0"