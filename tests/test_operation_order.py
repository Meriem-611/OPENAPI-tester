"""Unit tests for operation ordering (public endpoints first)."""

from __future__ import annotations

import unittest

from models import OperationSpec
from operation_order import prioritize_public_operations


class OperationOrderTests(unittest.TestCase):
    def test_public_before_authenticated(self) -> None:
        pub = OperationSpec(
            operation_id="a",
            method="get",
            path="/p",
            summary="",
            tags=[],
            resource_group="x",
            requires_auth=False,
            expected_statuses=[200],
        )
        aut = OperationSpec(
            operation_id="b",
            method="get",
            path="/q",
            summary="",
            tags=[],
            resource_group="y",
            requires_auth=True,
            expected_statuses=[200],
        )
        ordered = prioritize_public_operations([aut, pub])
        self.assertEqual([op.operation_id for op in ordered], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
