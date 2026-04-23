"""Deterministic heuristic failure analyzer."""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from jsonschema import ValidationError, validate

from models import ExecutionResult, FailureRecord, HeuristicAnalysis, OperationSpec


class HeuristicFailureAnalyzer:
    """Classify failed tests using deterministic QA-friendly heuristics."""

    def analyze_failures(
        self,
        results: List[ExecutionResult],
        operation_index: Dict[str, OperationSpec],
    ) -> List[FailureRecord]:
        """Build failure records with heuristic categories."""
        failures: List[FailureRecord] = []
        for result in results:
            if result.passed:
                continue
            operation = operation_index.get(result.test_case.operation_id)
            heuristic = self.classify(result, operation)
            failures.append(FailureRecord(execution=result, heuristic=heuristic))
        return failures

    def class_counts(self, failures: List[FailureRecord]) -> Dict[str, int]:
        """Return category frequency map."""
        counter = Counter(record.heuristic.category for record in failures)
        return dict(counter)

    def classify(
        self,
        result: ExecutionResult,
        operation: Optional[OperationSpec],
    ) -> HeuristicAnalysis:
        """Classify a single failed execution record."""
        if result.error:
            return HeuristicAnalysis(
                category="unknown_failure",
                confidence=0.65,
                reason=f"Execution error occurred before response: {result.error}",
            )

        status = result.observed_status
        if status is None:
            return HeuristicAnalysis(
                category="unknown_failure",
                confidence=0.5,
                reason="No observed status code returned.",
            )

        if status in {401, 403}:
            return HeuristicAnalysis(
                category="authentication_issue",
                confidence=0.95,
                reason="Observed 401/403 indicates missing or invalid authentication/authorization.",
            )
        if status == 404:
            return HeuristicAnalysis(
                category="resource_not_found",
                confidence=0.92,
                reason="Observed 404 indicates missing resource or invalid identifier/path.",
            )
        if status == 405:
            return HeuristicAnalysis(
                category="method_not_allowed",
                confidence=0.98,
                reason="Observed 405 indicates endpoint does not allow this HTTP method.",
            )
        if status >= 500:
            return HeuristicAnalysis(
                category="server_side_failure",
                confidence=0.97,
                reason="Observed 5xx indicates server-side failure.",
            )

        if (
            status in result.test_case.expected_statuses
            and self._is_business_error_payload(result.observed_json)
        ):
            return HeuristicAnalysis(
                category="business_error_payload",
                confidence=0.88,
                reason=(
                    "Transport status matched expected values, but response payload carries a business-level "
                    "error/notice indicator."
                ),
            )

        if status == 400:
            if result.test_case.test_type == "invalid_missing_required":
                return HeuristicAnalysis(
                    category="missing_required_parameter",
                    confidence=0.9,
                    reason="400 returned for request that intentionally omitted required input.",
                )
            if result.test_case.test_type == "invalid_wrong_type":
                return HeuristicAnalysis(
                    category="type_mismatch_or_request_schema_issue",
                    confidence=0.9,
                    reason="400 returned for request with intentionally wrong input types.",
                )

        contract_issue = self._detect_response_contract_violation(result, operation)
        if contract_issue:
            return contract_issue

        return HeuristicAnalysis(
            category="unexpected_status",
            confidence=0.75,
            reason=(
                f"Observed status {status} was not in expected set "
                f"{result.test_case.expected_statuses} and no stronger rule matched."
            ),
        )

    @staticmethod
    def _is_business_error_payload(observed_json: object) -> bool:
        if not isinstance(observed_json, dict):
            return False
        message = observed_json.get("message")
        if isinstance(message, dict):
            message_type = str(message.get("type", "")).strip().lower()
            if message_type in {"error", "notice", "warning", "fail", "failed"}:
                return True
        for key in ("error", "errors", "exception", "status"):
            if key in observed_json:
                value = observed_json.get(key)
                if isinstance(value, str) and value.strip():
                    lowered = value.strip().lower()
                    if lowered not in {"ok", "success", "successful"}:
                        return True
                if isinstance(value, (list, dict)) and value:
                    return True
        for key in ("respCode", "responseCode", "errorCode"):
            if key in observed_json:
                value = str(observed_json.get(key, "")).strip().lower()
                if value in {"0", "00", "0000", "success", "ok"}:
                    return False
                return True
        return False

    def _detect_response_contract_violation(
        self,
        result: ExecutionResult,
        operation: Optional[OperationSpec],
    ) -> Optional[HeuristicAnalysis]:
        if not operation or not operation.response_schema:
            return None
        status = result.observed_status
        if status is None or not (200 <= status < 300):
            return None
        if status in {204, 205}:
            # No-content success responses are valid and should not be treated as
            # JSON contract failures even when response_schema exists.
            return None
        if result.observed_json is None:
            return HeuristicAnalysis(
                category="response_contract_violation",
                confidence=0.8,
                reason="Expected JSON response schema but response was not valid JSON.",
            )
        try:
            validate(instance=result.observed_json, schema=operation.response_schema)
        except ValidationError as exc:
            return HeuristicAnalysis(
                category="response_contract_violation",
                confidence=0.9,
                reason=f"Response JSON failed schema validation: {exc.message}",
            )
        return None
