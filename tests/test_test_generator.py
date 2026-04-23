"""Unit tests for schema-based value generation."""

from __future__ import annotations

import unittest

from models import OperationSpec, ParameterSpec
from test_generator import (
    INVALID_INPUT_EXPECTED_STATUSES,
    MISSING_REQUIRED_EXPECTED_STATUSES,
    TestGenerator,
    generate_invalid_value,
    generate_valid_value,
)


class TestGeneratorValueTests(unittest.TestCase):
    def test_generate_valid_value_for_object_required(self) -> None:
        schema = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "nickname": {"type": "string"},
            },
        }
        value = generate_valid_value(schema)
        self.assertEqual(set(value.keys()), {"name", "age"})
        self.assertEqual(value["name"], "test")
        self.assertEqual(value["age"], 1)

    def test_generate_invalid_value_for_integer(self) -> None:
        bad = generate_invalid_value({"type": "integer"})
        self.assertEqual(bad, "not-a-number")

    def test_negative_tests_use_negative_expected_statuses(self) -> None:
        operation = OperationSpec(
            operation_id="create_user",
            method="post",
            path="/users",
            summary="",
            tags=[],
            resource_group="users",
            requires_auth=True,
            query_params=[ParameterSpec(name="version", location="query", required=True, schema={"type": "string"})],
            request_body_schema={
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
            expected_statuses=[200],
            is_state_changing=True,
            has_body=True,
        )
        generator = TestGenerator(auth_token_present=True)
        cases = generator.generate_tests_for_operation(operation)
        by_type = {case.test_type: case for case in cases}
        self.assertEqual(by_type["invalid_wrong_type"].expected_statuses, INVALID_INPUT_EXPECTED_STATUSES)
        self.assertEqual(
            by_type["invalid_missing_required"].expected_statuses,
            MISSING_REQUIRED_EXPECTED_STATUSES,
        )

    def test_secured_without_token_only_auth_detection(self) -> None:
        operation = OperationSpec(
            operation_id="delete_x",
            method="delete",
            path="/x/{id}",
            summary="",
            tags=[],
            resource_group="x",
            requires_auth=True,
            path_params=[ParameterSpec(name="id", location="path", required=True, schema={"type": "integer"})],
            expected_statuses=[204, 200],
            is_state_changing=True,
            has_body=False,
        )
        generator = TestGenerator(auth_token_present=False)
        cases = generator.generate_tests_for_operation(operation)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].test_type, "missing_auth")
        self.assertEqual(cases[0].expected_statuses, [401, 403])


if __name__ == "__main__":
    unittest.main()
