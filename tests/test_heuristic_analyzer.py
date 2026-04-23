"""Unit tests for heuristic failure classification."""

from __future__ import annotations

import unittest

from heuristic_analyzer import HeuristicFailureAnalyzer
from models import ExecutionResult, OperationSpec, TestCase


def make_result(status: int, test_type: str = "baseline_valid") -> ExecutionResult:
    case = TestCase(
        test_id="t1",
        operation_id="op1",
        test_type=test_type,
        method="get",
        path="/users/1",
        expected_statuses=[200],
    )
    return ExecutionResult(
        test_case=case,
        endpoint="/users/1",
        observed_status=status,
        response_excerpt="",
        latency_ms=5.0,
        passed=False,
    )


class HeuristicAnalyzerTests(unittest.TestCase):
    def test_authentication_issue(self) -> None:
        analyzer = HeuristicFailureAnalyzer()
        result = make_result(401)
        analysis = analyzer.classify(result, None)
        self.assertEqual(analysis.category, "authentication_issue")

    def test_missing_required_parameter(self) -> None:
        analyzer = HeuristicFailureAnalyzer()
        result = make_result(400, test_type="invalid_missing_required")
        analysis = analyzer.classify(result, None)
        self.assertEqual(analysis.category, "missing_required_parameter")

    def test_response_contract_violation(self) -> None:
        analyzer = HeuristicFailureAnalyzer()
        result = make_result(200)
        result.observed_json = {"name": 123}
        operation = OperationSpec(
            operation_id="op1",
            method="get",
            path="/users/1",
            summary="",
            tags=[],
            resource_group="users",
            requires_auth=False,
            expected_statuses=[200],
            response_schema={
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
        )
        analysis = analyzer.classify(result, operation)
        self.assertEqual(analysis.category, "response_contract_violation")


if __name__ == "__main__":
    unittest.main()
