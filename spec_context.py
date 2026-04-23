"""Build a bounded OpenAPI excerpt for LLM prompts (selected operations only)."""

from __future__ import annotations

import json
from typing import Any, Dict, Set

SUPPORTED = {"get", "post", "put", "patch", "delete"}


def build_api_bundle_for_llm(raw_spec: Dict[str, Any], selected_operation_ids: Set[str]) -> Dict[str, Any]:
    """
    Include info, servers, and path fragments only for operations whose operationId
    is in selected_operation_ids.
    """
    paths_out: Dict[str, Any] = {}
    paths = raw_spec.get("paths") or {}
    for path_key, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        fragment: Dict[str, Any] = {}
        for method, op_obj in path_item.items():
            ml = method.lower()
            if ml not in SUPPORTED or not isinstance(op_obj, dict):
                continue
            oid = op_obj.get("operationId", f"{ml}_{path_key.strip('/').replace('/', '_')}")
            if oid not in selected_operation_ids:
                continue
            fragment[ml] = _slim_operation(op_obj, path_item)
        if fragment:
            paths_out[path_key] = fragment

    return {
        "openapi": raw_spec.get("openapi", ""),
        "info": raw_spec.get("info", {}),
        "servers": raw_spec.get("servers", []),
        "paths": paths_out,
    }


def _slim_operation(op_obj: Dict[str, Any], path_item: Dict[str, Any]) -> Dict[str, Any]:
    keep = (
        "operationId",
        "summary",
        "description",
        "tags",
        "parameters",
        "requestBody",
        "responses",
        "security",
    )
    slim = {k: op_obj[k] for k in keep if k in op_obj}
    if "parameters" in path_item:
        slim.setdefault("_path_level_parameters", path_item["parameters"])
    return slim


def bundle_json_size_estimate(bundle: Dict[str, Any]) -> int:
    return len(json.dumps(bundle, ensure_ascii=True))


def truncate_bundle_if_needed(bundle: Dict[str, Any], max_chars: int = 120_000) -> Dict[str, Any]:
    """If bundle is huge, keep info/servers but drop verbose response bodies from paths."""
    raw = json.dumps(bundle, ensure_ascii=True)
    if len(raw) <= max_chars:
        return bundle
    slim = dict(bundle)
    paths = slim.get("paths") or {}
    slim_paths: Dict[str, Any] = {}
    for p, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        slim_paths[p] = {}
        for m, op in methods.items():
            if not isinstance(op, dict):
                continue
            op2 = {k: v for k, v in op.items() if k != "responses"}
            op2["responses"] = _one_line_responses(op.get("responses"))
            slim_paths[p][m] = op2
    slim["paths"] = slim_paths
    return slim


def _one_line_responses(responses: Any) -> Any:
    if not isinstance(responses, dict):
        return responses
    out = {}
    for code, resp in list(responses.items())[:15]:
        if isinstance(resp, dict):
            out[code] = {"description": resp.get("description", "")[:200]}
        else:
            out[code] = resp
    return out
