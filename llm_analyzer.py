"""Optional provider-agnostic (OpenAI-compatible) LLM failure analyzer."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from models import FailureRecord, LLMAnalysis
from openai_compat import chat_completion_content, parse_llm_json_content


class LLMFailureAnalyzer:
    """Generate concise contextual failure interpretations via LLM."""

    def __init__(
        self,
        api_key: Optional[str],
        model: str,
        base_url: str,
        *,
        azure_endpoint: Optional[str] = None,
        azure_deployment: Optional[str] = None,
        azure_api_version: Optional[str] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.azure_endpoint = azure_endpoint
        self.azure_deployment = azure_deployment
        self.azure_api_version = azure_api_version

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def analyze_failure(self, failure: FailureRecord) -> Optional[LLMAnalysis]:
        """Return LLM analysis or None when unavailable/failed."""
        if not self.is_enabled():
            return None

        payload = self._build_prompt_payload(failure)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a QA failure analysis assistant. Use ONLY given context. "
                    "Do not invent facts or hidden API behavior."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Analyze this API test failure and return strict JSON with keys: "
                    "likely_cause, qa_bug_summary, suggested_follow_up_tests (array of 2 strings).\n"
                    f"Context:\n{json.dumps(payload, ensure_ascii=True)}"
                ),
            },
        ]
        content = chat_completion_content(
            messages,
            temperature=0.1,
            timeout=20,
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            azure_endpoint=self.azure_endpoint,
            azure_deployment=self.azure_deployment,
            azure_api_version=self.azure_api_version,
        )
        if content is None:
            return None
        parsed = parse_llm_json_content(content)
        if not parsed:
            return None
        return LLMAnalysis(
            likely_cause=parsed.get("likely_cause", "").strip(),
            qa_bug_summary=parsed.get("qa_bug_summary", "").strip(),
            suggested_follow_up_tests=list(parsed.get("suggested_follow_up_tests", []))[:2],
        )

    def _build_prompt_payload(self, failure: FailureRecord) -> Dict[str, Any]:
        execution = failure.execution
        case = execution.test_case
        excerpt = execution.response_excerpt
        if len(excerpt) > 4000:
            excerpt = excerpt[:4000] + "…"
        return {
            "operation_id": case.operation_id,
            "method": case.method,
            "path": case.path,
            "test_id": case.test_id,
            "test_type": case.test_type,
            "endpoint_called": execution.endpoint,
            "expected_statuses": case.expected_statuses,
            "observed_status": execution.observed_status,
            "execution_error": execution.error,
            "response_excerpt": excerpt,
            "heuristic_category": failure.heuristic.category,
            "heuristic_confidence": failure.heuristic.confidence,
            "heuristic_reason": failure.heuristic.reason,
            "test_notes": case.notes,
        }
