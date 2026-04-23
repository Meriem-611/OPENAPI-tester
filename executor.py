"""HTTP execution engine for generated tests."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests

from models import ExecutionResult, TestCase
from utils import build_url, shorten_text


class TestExecutor:
    """Execute generated tests against target API while failing gracefully."""

    def __init__(self, base_url: str, timeout: int, auth_token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth_token = auth_token

    def execute(self, tests: List[TestCase]) -> List[ExecutionResult]:
        """Run all test cases and return results."""
        results: List[ExecutionResult] = []
        total = len(tests)
        for idx, case in enumerate(tests, start=1):
            if idx == 1 or idx == total or idx % 10 == 0:
                print(f"[progress] Executing test {idx}/{total}: {case.test_id}")
            results.append(self._execute_one(case))
        return results

    def execute_one(self, case: TestCase) -> ExecutionResult:
        """Run a single test case (public wrapper for tooling/bootstrap)."""
        return self._execute_one(case)

    def _execute_one(self, case: TestCase) -> ExecutionResult:
        endpoint_path = self._substitute_path_params(case.path, case.path_params)
        url = build_url(self.base_url, endpoint_path)
        headers = dict(case.headers)

        if self.auth_token and case.test_type != "missing_auth":
            headers["Authorization"] = f"Bearer {self.auth_token}"

        request_kwargs: Dict[str, Any] = {
            "params": case.query_params or None,
            "headers": headers or None,
            "timeout": self.timeout,
        }
        if case.json_body is not None and case.method.lower() in {"post", "put", "patch"}:
            request_kwargs["json"] = case.json_body

        started = time.perf_counter()
        try:
            response = requests.request(case.method.upper(), url, **request_kwargs)
            latency_ms = (time.perf_counter() - started) * 1000
            observed_json = None
            try:
                observed_json = response.json()
            except Exception:
                observed_json = None

            passed = self._is_pass(case, response.status_code, observed_json)
            return ExecutionResult(
                test_case=case,
                endpoint=endpoint_path,
                observed_status=response.status_code,
                response_excerpt=shorten_text(observed_json if observed_json is not None else response.text),
                latency_ms=round(latency_ms, 2),
                passed=passed,
                observed_json=observed_json,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return ExecutionResult(
                test_case=case,
                endpoint=endpoint_path,
                observed_status=None,
                response_excerpt="",
                latency_ms=round(latency_ms, 2),
                passed=False,
                error=str(exc),
            )

    @staticmethod
    def _substitute_path_params(path: str, path_params: Dict[str, Any]) -> str:
        out = path
        for key, value in path_params.items():
            out = out.replace(f"{{{key}}}", str(value))
        return out

    @staticmethod
    def _is_business_error_payload(observed_json: Any) -> bool:
        """
        Best-effort detector for APIs that return HTTP 200 with business error codes.
        """
        if not isinstance(observed_json, dict):
            return False
        # Common explicit message envelope shape:
        # { "message": { "type": "error|notice", "text": "..." } }
        message = observed_json.get("message")
        if isinstance(message, dict):
            message_type = str(message.get("type", "")).strip().lower()
            if message_type in {"error", "notice", "warning", "fail", "failed"}:
                return True
        # Flat error-like keys occasionally used by REST APIs.
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
                # Common success business codes.
                if value in {"0", "00", "0000", "success", "ok"}:
                    return False
                return True
        return False

    def _is_pass(self, case: TestCase, observed_status: int, observed_json: Any) -> bool:
        """
        Status-based oracle with a business-error override for APIs that encode
        failures in response bodies while still returning 2xx.
        """
        status_ok = observed_status in case.expected_statuses
        # Tolerate common async/no-content success variants for LLM-generated 200-only
        # expectations (e.g., APIs that return 202/204 for successful GET/POST variants).
        if (
            not status_ok
            and case.generated_by == "llm"
            and case.expected_statuses == [200]
            and observed_status in {202, 204}
        ):
            status_ok = True
        business_error = self._is_business_error_payload(observed_json)
        if not business_error:
            return status_ok
        expects_error_status = bool(case.expected_statuses) and all(code >= 400 for code in case.expected_statuses)
        if observed_status // 100 == 2 and expects_error_status:
            # Negative test expected transport error but got business-level error in 2xx.
            return True
        # Happy-path style expectations should fail on business-level error payloads.
        return False
