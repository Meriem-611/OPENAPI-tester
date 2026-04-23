"""Order selected operations for test generation (public APIs first)."""

from __future__ import annotations

from typing import List

from models import OperationSpec


def prioritize_public_operations(operations: List[OperationSpec]) -> List[OperationSpec]:
    """Place operations that do not require auth before authenticated ones."""
    return sorted(operations, key=lambda op: (bool(op.requires_auth), op.operation_id))
