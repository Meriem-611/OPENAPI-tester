"""LLM-based test case generation from OpenAPI context and selected operations."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from models import OperationSpec, TestCase
from openai_compat import post_chat_json


def serialize_operations_for_prompt(ops: List[OperationSpec]) -> List[Dict[str, Any]]:
    """Compact operation description for LLM prompts."""
    out: List[Dict[str, Any]] = []
    for op in ops:
        out.append(
            {
                "operation_id": op.operation_id,
                "method": op.method.upper(),
                "path": op.path,
                "summary": op.summary,
                "requires_auth": op.requires_auth,
                "resource_group": op.resource_group,
                "path_param_names": [p.name for p in op.path_params],
                "query_param_names": [p.name for p in op.query_params],
                "has_request_body": op.has_body,
            }
        )
    return out


def map_json_to_test_cases(raw: Dict[str, Any], op_index: Dict[str, OperationSpec]) -> List[TestCase]:
    """Turn LLM JSON into TestCase list; path/method taken from spec for safety."""
    cases: List[TestCase] = []
    items = raw.get("test_cases")
    if not isinstance(items, list):
        return cases
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        oid = str(item.get("operation_id", "")).strip()
        if oid not in op_index:
            continue
        spec = op_index[oid]
        tid = str(item.get("test_id", "")).strip() or f"{oid}__llm_{idx + 1}"
        test_type = str(item.get("test_type", "llm_generated")).strip() or "llm_generated"
        # Align with executor auth skip semantics
        if test_type.lower() in {"auth_missing", "no_bearer", "unauthenticated_check", "missing_token"}:
            test_type = "missing_auth"
        raw_exp = item.get("expected_statuses")
        if not isinstance(raw_exp, list) or not raw_exp:
            expected = spec.expected_statuses or [200]
        else:
            expected = [_coerce_status_code(x) for x in raw_exp]
            expected = [x for x in expected if x is not None]
            if not expected:
                expected = spec.expected_statuses or [200]

        path_params = item.get("path_params") if isinstance(item.get("path_params"), dict) else {}
        query_params = item.get("query_params") if isinstance(item.get("query_params"), dict) else {}
        headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
        jb = item.get("json_body")
        notes = str(item.get("notes", "")).strip()
        summary = str(item.get("expected_response_summary", "")).strip()
        json_body = jb if isinstance(jb, dict) else None
        # Guardrail: if spec says no request body, strip generated body and content-type.
        if not spec.has_body:
            json_body = None
            headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}

        cases.append(
            TestCase(
                test_id=tid,
                operation_id=oid,
                test_type=test_type,
                method=spec.method,
                path=spec.path,
                expected_statuses=expected,
                path_params={k: v for k, v in path_params.items()},
                query_params={k: v for k, v in query_params.items()},
                headers={k: str(v) for k, v in headers.items()},
                json_body=json_body,
                notes=notes or "LLM-generated test case.",
                expected_response_summary=summary,
                generated_by="llm",
            )
        )
    return cases


def _coerce_status_code(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


class LLMTestCaseGenerator:
    """First LLM: propose test cases with expected statuses and response intent."""

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

    def generate(
        self,
        openapi_bundle: Dict[str, Any],
        selected_operations: List[OperationSpec],
        auth_token_present: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Return parsed JSON with key test_cases, or None on failure."""
        if not selected_operations:
            return None
        ops_payload = serialize_operations_for_prompt(selected_operations)
        if auth_token_present:
            auth_instructions = (
                "The user provided credentials. For requires_auth=true operations you may propose baseline success tests "
                "and negative tests; include realistic expected_statuses and expected_response_summary."
            )
        else:
            auth_instructions = (
                "No API token was provided. For requires_auth=true operations ONLY propose auth-handling tests: "
                "test_type missing_auth (or equivalent) with expected_statuses [401] and/or [403]. "
                "Do not assume a successful 200 on secured routes without credentials."
            )
        user = json.dumps(
            {
                "openapi_excerpt": openapi_bundle,
                "operations_to_test": ops_payload,
                "auth_token_present": auth_token_present,
                "instructions": (
                    "Operations are ordered with public (no auth) first, then authenticated. "
                    "Generate test cases for each operation_id in operations_to_test, choosing case types/techniques "
                    "you judge most appropriate for that operation context. "
                    "For requires_auth=false: propose 1–3 tests (happy path + meaningful negative/boundary variants as needed). "
                    f"{auth_instructions} "
                    "Use only operation_ids from the list. Include path_params, query_params, json_body as needed. "
                    "Prefer realistic, executable tests over artificial placeholders, and include expected_statuses "
                    "that align with the operation semantics. "
                    "For negative tests, if API docs or examples suggest business errors can be returned in 2xx envelopes, "
                    "you may include 200 in expected_statuses and describe the expected error payload signal."
                ),
            },
            ensure_ascii=True,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a senior QA API test designer. Output ONLY valid JSON, no markdown. "
                    "Your task is to generate test cases per operation_id based on the provided context. "
                    "You decide the most suitable techniques (e.g., happy path, validation negative, boundary-style checks, auth handling) "
                    "for each operation. "
                    "Do not invent undocumented fields; stay within each operation's path and parameters. "
                    "Schema: { \"test_cases\": [ { \"operation_id\": str, \"test_id\": str (optional), "
                    "\"test_type\": str, \"path_params\": object, \"query_params\": object, \"headers\": object, "
                    "\"json_body\": object|null, \"expected_statuses\": [int], \"expected_response_summary\": str, "
                    "\"notes\": str } ] }."
                ),
            },
            {"role": "user", "content": user},
        ]
        return post_chat_json(
            messages,
            temperature=0.25,
            timeout=180,
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            azure_endpoint=self.azure_endpoint,
            azure_deployment=self.azure_deployment,
            azure_api_version=self.azure_api_version,
        )
