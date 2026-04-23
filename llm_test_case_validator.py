"""Second LLM: validate and optionally revise proposed test cases."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from openai_compat import post_chat_json


class LLMTestCaseValidator:
    """Validate generator JSON against operation constraints; request fixes if needed."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        *,
        azure_endpoint: Optional[str] = None,
        azure_deployment: Optional[str] = None,
        azure_api_version: Optional[str] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.azure_endpoint = azure_endpoint
        self.azure_deployment = azure_deployment
        self.azure_api_version = azure_api_version

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def validate_and_refine(
        self,
        proposed: Dict[str, Any],
        operation_summaries: List[Dict[str, Any]],
        iteration: int,
        max_iterations: int,
        auth_token_present: bool = False,
    ) -> Dict[str, Any]:
        """
        Return dict with keys:
        - approved: bool
        - test_cases: same shape as generator output (key test_cases)
        - reviewer_notes: str
        """
        default = {"approved": True, "test_cases": proposed.get("test_cases", []), "reviewer_notes": ""}
        if not self.is_enabled():
            return default
        auth_rule = (
            "If auth_token_present is false, secured operations must only have auth-enforcement tests (401/403), not 200 baselines."
            if not auth_token_present
            else "auth_token_present is true; secured operations may include success-path expectations where appropriate."
        )
        payload = json.dumps(
            {
                "proposed": proposed,
                "allowed_operations": operation_summaries,
                "auth_token_present": auth_token_present,
                "iteration": iteration,
                "max_iterations": max_iterations,
                "rules": [
                    "Every operation_id must appear in allowed_operations.",
                    "Paths and HTTP methods must match the spec; do not change operation_id bindings.",
                    "Do not include real API keys or passwords; use placeholders only in notes.",
                    auth_rule,
                    "expected_statuses must be non-empty arrays of integers.",
                ],
            },
            ensure_ascii=True,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a strict QA reviewer for API test plans. Output ONLY valid JSON. "
                    "If the proposal is acceptable, set approved true and echo test_cases unchanged "
                    "(or with minor fixes). If not, set approved false and put a corrected "
                    "test_cases array that fixes issues. "
                    "Schema: { \"approved\": bool, \"test_cases\": [...], \"reviewer_notes\": str }."
                ),
            },
            {"role": "user", "content": payload},
        ]
        result = post_chat_json(
            messages,
            temperature=0.1,
            timeout=120,
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            azure_endpoint=self.azure_endpoint,
            azure_deployment=self.azure_deployment,
            azure_api_version=self.azure_api_version,
        )
        if not result or "test_cases" not in result:
            return default
        approved = bool(result.get("approved", True))
        return {
            "approved": approved,
            "test_cases": result.get("test_cases", proposed.get("test_cases", [])),
            "reviewer_notes": str(result.get("reviewer_notes", "")),
        }
