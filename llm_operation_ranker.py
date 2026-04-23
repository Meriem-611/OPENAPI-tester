"""Optional LLM-assisted ordering of selected operations before test generation."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from models import OperationSpec
from openai_compat import post_chat_json


def _compact_ops(ops: List[OperationSpec]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for op in ops:
        out.append(
            {
                "operation_id": op.operation_id,
                "method": op.method.upper(),
                "path": op.path,
                "summary": op.summary,
                "requires_auth": op.requires_auth,
                "path_param_names": [p.name for p in op.path_params],
                "query_param_names": [p.name for p in op.query_params],
                "has_body": op.has_body,
                "is_state_changing": op.is_state_changing,
            }
        )
    return out


def rank_operations_with_llm(
    *,
    api_key: str,
    model: str,
    base_url: str,
    azure_endpoint: Optional[str],
    azure_deployment: Optional[str],
    azure_api_version: Optional[str],
    operations: List[OperationSpec],
    auth_token_present: bool,
) -> Optional[List[str]]:
    """
    Ask an LLM for a recommended execution order as operation_id list.

    Returns None on failure (caller keeps original order).
    """
    if not operations or not api_key:
        return None
    payload = json.dumps(
        {
            "operations": _compact_ops(operations),
            "auth_token_present": auth_token_present,
            "rules": [
                "Prefer safer/cheaper calls first: fewer path parameters, GET before writes.",
                "If auth_token_present is false, prioritize public endpoints and auth-checking flows.",
                "Do not invent operations; only reorder given operation_id values.",
            ],
        },
        ensure_ascii=True,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You rank API operations for a smoke-test run. Output ONLY JSON: "
                '{ "ranked_operation_ids": [str, ...] } containing every input operation_id exactly once.'
            ),
        },
        {"role": "user", "content": payload},
    ]
    parsed = post_chat_json(
        messages,
        temperature=0.1,
        timeout=120,
        api_key=api_key,
        base_url=base_url,
        model=model,
        azure_endpoint=azure_endpoint,
        azure_deployment=azure_deployment,
        azure_api_version=azure_api_version,
    )
    if not parsed:
        return None
    ranked = parsed.get("ranked_operation_ids")
    if not isinstance(ranked, list):
        return None
    ids = [str(x).strip() for x in ranked if str(x).strip()]
    expected = {op.operation_id for op in operations}
    if set(ids) != expected:
        return None
    return ids


def reorder_operations(operations: List[OperationSpec], ranked_ids: List[str]) -> List[OperationSpec]:
    index = {op.operation_id: op for op in operations}
    return [index[i] for i in ranked_ids]
