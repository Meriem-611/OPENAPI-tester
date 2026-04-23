"""Unit tests for operation selector behavior."""

from __future__ import annotations

import unittest

from models import OperationSpec
from operation_selector import OperationSelector


def make_operation(operation_id: str, method: str, group: str, requires_auth: bool, has_body: bool) -> OperationSpec:
    return OperationSpec(
        operation_id=operation_id,
        method=method,
        path=f"/{group}",
        summary="",
        tags=[],
        resource_group=group,
        requires_auth=requires_auth,
        expected_statuses=[200],
        is_state_changing=method in {"post", "put", "patch", "delete"},
        has_body=has_body,
    )


class OperationSelectorTests(unittest.TestCase):
    def test_each_resource_group_is_covered_if_budget_allows(self) -> None:
        operations = [
            make_operation("users_get", "get", "users", False, False),
            make_operation("orders_get", "get", "orders", True, False),
            make_operation("pets_post", "post", "pets", True, True),
        ]
        selector = OperationSelector(max_selected_operations=5)
        result = selector.select(operations)
        self.assertEqual(set(result.coverage.resource_groups_covered), {"users", "orders", "pets"})

    def test_respects_budget(self) -> None:
        operations = [
            make_operation("users_get", "get", "users", False, False),
            make_operation("orders_get", "get", "orders", True, False),
            make_operation("pets_post", "post", "pets", True, True),
        ]
        selector = OperationSelector(max_selected_operations=2)
        result = selector.select(operations)
        self.assertEqual(len(result.selected_operations), 2)

    def test_zero_budget_means_auto_representative_sample(self) -> None:
        operations = [
            make_operation("users_get", "get", "users", False, False),
            make_operation("users_post", "post", "users", False, True),
            make_operation("orders_get", "get", "orders", True, False),
            make_operation("pets_post", "post", "pets", True, True),
        ]
        selector = OperationSelector(max_selected_operations=0)
        result = selector.select(operations)
        self.assertLess(len(result.selected_operations), len(operations))
        self.assertEqual(set(result.coverage.resource_groups_covered), {"users", "orders", "pets"})

    def test_budget_at_least_two_includes_secured_when_catalog_has_secured(self) -> None:
        operations = [
            make_operation("a_get", "get", "alpha", False, False),
            make_operation("b_get", "get", "bravo", False, False),
            make_operation("c_sec", "get", "charlie", True, False),
        ]
        selector = OperationSelector(max_selected_operations=2)
        result = selector.select(operations)
        self.assertTrue(any(op.requires_auth for op in result.selected_operations))


if __name__ == "__main__":
    unittest.main()
