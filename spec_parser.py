"""OpenAPI 3.x + Swagger 2.0 parser with lightweight local ref resolution."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from models import OperationSpec, ParameterSpec
from utils import first_or_none, load_json_or_yaml

SUPPORTED_METHODS = {"get", "post", "put", "patch", "delete"}


class OpenAPISpecParser:
    """Parse OpenAPI spec and normalize selected operation fields."""

    def __init__(self, spec_path: Path):
        self.spec_path = spec_path
        self.raw_spec: Dict[str, Any] = {}
        self.spec_name = spec_path.name
        self.spec_major: int = 3

    def parse(self) -> Tuple[List[OperationSpec], Dict[str, Any]]:
        """Parse spec file and return operation list with metadata."""
        self.raw_spec = load_json_or_yaml(self.spec_path)
        self._validate_minimal_shape()
        operations: List[OperationSpec] = []
        paths_obj = self.raw_spec.get("paths", {})
        global_security = self.raw_spec.get("security", [])
        discovered_servers: List[Dict[str, Any]] = []

        for path, path_item in paths_obj.items():
            if isinstance(path_item.get("servers"), list):
                discovered_servers.extend(path_item.get("servers", []))
            path_level_parameters = self._resolve_node(path_item.get("parameters", []))
            for method, op_obj in path_item.items():
                if method.lower() not in SUPPORTED_METHODS:
                    continue
                resolved_op = self._resolve_node(op_obj)
                if isinstance(resolved_op.get("servers"), list):
                    discovered_servers.extend(resolved_op.get("servers", []))
                operation = self._normalize_operation(
                    path=path,
                    method=method.lower(),
                    op_obj=resolved_op,
                    path_level_parameters=path_level_parameters,
                    global_security=global_security,
                )
                operations.append(operation)

        return operations, {
            "spec_name": self.spec_name,
            "servers": self._extract_servers(),
            "discovered_servers": discovered_servers,
        }

    def _validate_minimal_shape(self) -> None:
        openapi_version = str(self.raw_spec.get("openapi", "")).strip()
        swagger_version = str(self.raw_spec.get("swagger", "")).strip()
        if openapi_version.startswith("3."):
            self.spec_major = 3
        elif swagger_version == "2.0":
            self.spec_major = 2
        else:
            raise ValueError("Only OpenAPI 3.x and Swagger/OpenAPI 2.0 specs are supported.")
        if "paths" not in self.raw_spec or not isinstance(self.raw_spec["paths"], dict):
            raise ValueError("Spec must contain a valid 'paths' object.")

    def _extract_servers(self) -> List[Dict[str, Any]]:
        if self.spec_major == 3:
            servers = self.raw_spec.get("servers", [])
            return servers if isinstance(servers, list) else []
        host = str(self.raw_spec.get("host", "")).strip()
        base_path = str(self.raw_spec.get("basePath", "")).strip()
        schemes = self.raw_spec.get("schemes", [])
        if not host:
            return []
        if not isinstance(schemes, list) or not schemes:
            schemes = ["https"]
        servers: List[Dict[str, Any]] = []
        for scheme in schemes:
            s = str(scheme).strip() or "https"
            servers.append({"url": f"{s}://{host}{base_path}"})
        return servers

    def _resolve_node(self, node: Any, ref_stack: Optional[Set[str]] = None) -> Any:
        """Recursively resolve local refs #/components/... for dict/list nodes."""
        if ref_stack is None:
            ref_stack = set()
        if isinstance(node, list):
            return [self._resolve_node(item, ref_stack=set(ref_stack)) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            ref = node["$ref"]
            if ref in ref_stack:
                # Cycle-safe fallback: preserve local ref instead of infinitely expanding.
                sibling_overrides = {
                    key: self._resolve_node(value, ref_stack=set(ref_stack))
                    for key, value in node.items()
                    if key != "$ref"
                }
                return {"$ref": ref, **sibling_overrides}

            next_stack = set(ref_stack)
            next_stack.add(ref)
            resolved = self._resolve_ref(ref)
            merged = deepcopy(resolved)
            # Allow sibling keys to override resolved content.
            for key, value in node.items():
                if key != "$ref":
                    merged[key] = self._resolve_node(value, ref_stack=set(next_stack))
            return self._resolve_node(merged, ref_stack=next_stack)
        return {key: self._resolve_node(value, ref_stack=set(ref_stack)) for key, value in node.items()}

    def _resolve_ref(self, ref: str) -> Dict[str, Any]:
        if not ref.startswith("#/"):
            raise ValueError(f"Unsupported ref type: {ref}. Only local refs allowed.")
        current: Any = self.raw_spec
        for part in ref[2:].split("/"):
            if part not in current:
                raise KeyError(f"Unresolvable ref path '{ref}' at '{part}'.")
            current = current[part]
        if not isinstance(current, dict):
            raise ValueError(f"Ref target is not an object: {ref}")
        return current

    @staticmethod
    def extract_resource_group(path: str) -> str:
        """Derive resource group from first non-parameter path segment."""
        for segment in path.strip("/").split("/"):
            if not segment:
                continue
            if not (segment.startswith("{") and segment.endswith("}")):
                return segment.lower()
        return "root"

    def _normalize_operation(
        self,
        path: str,
        method: str,
        op_obj: Dict[str, Any],
        path_level_parameters: List[Dict[str, Any]],
        global_security: List[Dict[str, Any]],
    ) -> OperationSpec:
        operation_id = op_obj.get("operationId", f"{method}_{path.strip('/').replace('/', '_')}")
        summary = op_obj.get("summary", "")
        tags = [str(tag) for tag in op_obj.get("tags", [])]
        resource_group = self.extract_resource_group(path)
        is_state_changing = method in {"post", "put", "patch", "delete"}

        op_security = op_obj.get("security")
        effective_security = op_security if op_security is not None else global_security
        security_block = self._security_as_list(effective_security)
        requires_auth = self._requires_auth(effective_security)

        merged_params = self._merge_parameters(path_level_parameters, op_obj.get("parameters", []))
        path_params = self._extract_parameters(merged_params, "path")
        query_params = self._extract_parameters(merged_params, "query")
        header_params = self._extract_parameters(merged_params, "header")

        request_body_schema = self._extract_request_body_schema(op_obj, merged_params)
        has_body = request_body_schema is not None

        expected_statuses, response_schema = self._extract_response_expectations(op_obj.get("responses", {}))

        return OperationSpec(
            operation_id=operation_id,
            method=method,
            path=path,
            summary=summary,
            tags=tags,
            resource_group=resource_group,
            requires_auth=requires_auth,
            security=security_block,
            path_params=path_params,
            query_params=query_params,
            header_params=header_params,
            request_body_schema=request_body_schema,
            expected_statuses=expected_statuses,
            response_schema=response_schema,
            is_state_changing=is_state_changing,
            has_body=has_body,
        )

    def _merge_parameters(
        self,
        path_level: List[Dict[str, Any]],
        op_level: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        resolved = [self._resolve_node(item) for item in (path_level + op_level)]
        dedup: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in resolved:
            key = (item.get("name", ""), item.get("in", ""))
            dedup[key] = item
        return list(dedup.values())

    def _extract_parameters(self, params: List[Dict[str, Any]], location: str) -> List[ParameterSpec]:
        out: List[ParameterSpec] = []
        for param in params:
            if param.get("in") != location:
                continue
            out.append(
                ParameterSpec(
                    name=str(param.get("name", "")),
                    location=location,
                    required=bool(param.get("required", False) or location == "path"),
                    schema=self._parameter_schema(param),
                )
            )
        return out

    @staticmethod
    def _parameter_schema(param: Dict[str, Any]) -> Dict[str, Any]:
        schema = param.get("schema")
        if isinstance(schema, dict):
            return schema
        # Swagger 2.0 non-body parameters often define type/format at top-level.
        inferred: Dict[str, Any] = {}
        for key in ("type", "format", "enum", "items", "default"):
            if key in param:
                inferred[key] = param[key]
        return inferred

    def _extract_request_body_schema(
        self,
        op_obj: Dict[str, Any],
        merged_params: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if self.spec_major == 2:
            for param in merged_params:
                if str(param.get("in", "")).lower() == "body":
                    schema = self._resolve_node(param.get("schema"))
                    return schema if schema is not None else {}
            for param in merged_params:
                if str(param.get("in", "")).lower() == "formdata":
                    # Form-data payload is still a request body shape.
                    return {}
            return None

        request_body = self._resolve_node(op_obj.get("requestBody"))
        if not request_body:
            return None
        content = request_body.get("content", {})
        if not isinstance(content, dict) or not content:
            return None
        app_json = content.get("application/json")
        if isinstance(app_json, dict):
            schema = app_json.get("schema")
            if schema:
                return self._resolve_node(schema)
            return {}
        for media_obj in content.values():
            if not isinstance(media_obj, dict):
                continue
            schema = media_obj.get("schema")
            if schema:
                return self._resolve_node(schema)
            return {}
        return {}

    def _extract_response_expectations(
        self, responses: Dict[str, Any]
    ) -> Tuple[List[int], Optional[Dict[str, Any]]]:
        expected_statuses: List[int] = []
        response_schema: Optional[Dict[str, Any]] = None

        for status_key, response_obj in responses.items():
            status_obj = self._resolve_node(response_obj)
            if status_key.isdigit():
                expected_statuses.append(int(status_key))

        if not expected_statuses:
            # Fallback to common success ranges when spec is weakly defined.
            expected_statuses = [200, 201, 204]

        preferred_status = first_or_none(sorted(status for status in expected_statuses if 200 <= status < 300))
        if preferred_status is not None:
            preferred_response = self._resolve_node(responses.get(str(preferred_status), {}))
            if self.spec_major == 2:
                schema = preferred_response.get("schema")
                if schema:
                    response_schema = self._resolve_node(schema)
            else:
                content = preferred_response.get("content", {})
                app_json = content.get("application/json", {})
                schema = app_json.get("schema")
                if schema:
                    response_schema = self._resolve_node(schema)

        return sorted(set(expected_statuses)), response_schema

    @staticmethod
    def _requires_auth(security_obj: Any) -> bool:
        if security_obj is None:
            return False
        if isinstance(security_obj, list):
            # In OpenAPI, empty object {} means optional auth, empty list [] means no auth.
            if len(security_obj) == 0:
                return False
            return any(bool(item) for item in security_obj if isinstance(item, dict))
        return bool(security_obj)

    @staticmethod
    def _security_as_list(security_obj: Any) -> List[Dict[str, List[str]]]:
        """
        Normalize OpenAPI security to a list of OR-requirement objects.

        Each dict maps scheme name -> list of scopes (empty list for non-OAuth schemes).
        """
        if security_obj is None:
            return []
        if isinstance(security_obj, list):
            out: List[Dict[str, List[str]]] = []
            for item in security_obj:
                if not isinstance(item, dict):
                    continue
                if not item:
                    # {} means optional auth; represent as empty alternative.
                    out.append({})
                    continue
                converted: Dict[str, List[str]] = {}
                for scheme, scopes in item.items():
                    if isinstance(scopes, list):
                        converted[str(scheme)] = [str(s) for s in scopes]
                    elif scopes is None:
                        converted[str(scheme)] = []
                    else:
                        converted[str(scheme)] = [str(scopes)]
                out.append(converted)
            return out
        if isinstance(security_obj, dict):
            return OpenAPISpecParser._security_as_list([security_obj])
        return []
