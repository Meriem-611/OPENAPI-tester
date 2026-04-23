"""Schema-driven bounded test generation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from models import OperationSpec, ParameterSpec, TestCase

INVALID_INPUT_EXPECTED_STATUSES = [400, 401, 403, 404, 409, 415, 422]
MISSING_REQUIRED_EXPECTED_STATUSES = [400, 422]


def generate_valid_value(schema: Dict[str, Any]) -> Any:
    """Generate best-effort valid value from a JSON schema fragment."""
    if not schema:
        return "test"
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]

    schema_type = _normalize_schema_type(schema.get("type"))
    schema_format = schema.get("format")

    if schema_type == "string":
        if schema_format == "email":
            return "test@example.com"
        return "test"
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        item_schema = schema.get("items", {})
        return [generate_valid_value(item_schema)]
    if schema_type == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = schema.get("required", [])
        return {name: generate_valid_value(props.get(name, {})) for name in required}
    return "test"


def generate_invalid_value(schema: Dict[str, Any]) -> Any:
    """Generate intentionally invalid value by type inversion."""
    schema_type = _normalize_schema_type(schema.get("type"))
    if schema_type == "string":
        return 123
    if schema_type in {"integer", "number"}:
        return "not-a-number"
    if schema_type == "boolean":
        return "not-a-bool"
    if schema_type == "array":
        return {"not": "array"}
    if schema_type == "object":
        return "not-an-object"
    return None


def _normalize_schema_type(raw_type: Any) -> Optional[str]:
    """Normalize schema type that may be string or list like ['string', 'null'].""" 
    if isinstance(raw_type, str):
        return raw_type
    if isinstance(raw_type, list):
        for entry in raw_type:
            if isinstance(entry, str) and entry != "null":
                return entry
        for entry in raw_type:
            if isinstance(entry, str):
                return entry
    return None


class TestGenerator:
    """Generate bounded test cases per selected operation."""

    def __init__(self, max_tests_per_operation: int = 4, auth_token_present: bool = False):
        self.max_tests_per_operation = max(1, min(4, max_tests_per_operation))
        self.auth_token_present = auth_token_present

    def generate_tests_for_operation(self, operation: OperationSpec) -> List[TestCase]:
        """Generate baseline, invalid, and auth-focused tests."""
        if operation.requires_auth and not self.auth_token_present:
            return self._tests_secured_without_credentials(operation)

        cases: List[TestCase] = []
        index = 1

        baseline = self._build_baseline_test(operation, index)
        cases.append(baseline)
        index += 1

        invalid_wrong_type = self._build_invalid_wrong_type_test(operation, index)
        if invalid_wrong_type:
            cases.append(invalid_wrong_type)
            index += 1

        invalid_missing_required = self._build_invalid_missing_required_test(operation, index)
        if invalid_missing_required:
            cases.append(invalid_missing_required)
            index += 1

        if operation.requires_auth:
            missing_auth = self._build_missing_auth_test(operation, index)
            cases.append(missing_auth)

        return cases[: self.max_tests_per_operation]

    def _tests_secured_without_credentials(self, operation: OperationSpec) -> List[TestCase]:
        """Without a token, only verify that the API enforces authentication (401/403)."""
        missing = self._build_missing_auth_test(operation, 1)
        return [missing][: self.max_tests_per_operation]

    def generate(self, operations: List[OperationSpec]) -> List[TestCase]:
        """Generate test cases for selected operations."""
        all_cases: List[TestCase] = []
        for operation in operations:
            all_cases.extend(self.generate_tests_for_operation(operation))
        return all_cases

    def _build_baseline_test(self, operation: OperationSpec, index: int) -> TestCase:
        path_params = self._build_param_values(operation.path_params, valid=True)
        query_params = self._build_param_values(operation.query_params, valid=True)
        headers = self._build_header_values(operation.header_params, valid=True)
        body = self._build_body(operation.request_body_schema, valid=True)
        return TestCase(
            test_id=f"{operation.operation_id}__t{index}",
            operation_id=operation.operation_id,
            test_type="baseline_valid",
            method=operation.method,
            path=operation.path,
            expected_statuses=operation.expected_statuses,
            path_params=path_params,
            query_params=query_params,
            headers=headers,
            json_body=body,
            notes="Best-effort valid schema-driven request.",
        )

    def _build_invalid_wrong_type_test(self, operation: OperationSpec, index: int) -> Optional[TestCase]:
        path_params = self._build_param_values(operation.path_params, valid=True)
        query_params = self._build_param_values(operation.query_params, valid=True)
        headers = self._build_header_values(operation.header_params, valid=True)
        body = self._build_body(operation.request_body_schema, valid=True)

        injected = False
        if operation.request_body_schema:
            bad_body = self._build_body(operation.request_body_schema, valid=False)
            if bad_body is not None:
                body = bad_body
                injected = True
        elif operation.query_params:
            required_or_first = self._required_or_first(operation.query_params)
            if required_or_first:
                query_params[required_or_first.name] = generate_invalid_value(required_or_first.schema)
                injected = True
        elif operation.path_params:
            first_param = operation.path_params[0]
            path_params[first_param.name] = generate_invalid_value(first_param.schema)
            injected = True

        if not injected:
            return None

        return TestCase(
            test_id=f"{operation.operation_id}__t{index}",
            operation_id=operation.operation_id,
            test_type="invalid_wrong_type",
            method=operation.method,
            path=operation.path,
            expected_statuses=INVALID_INPUT_EXPECTED_STATUSES,
            path_params=path_params,
            query_params=query_params,
            headers=headers,
            json_body=body,
            notes="Intentional wrong-type input to trigger schema validation.",
        )

    def _build_invalid_missing_required_test(
        self, operation: OperationSpec, index: int
    ) -> Optional[TestCase]:
        path_params = self._build_param_values(operation.path_params, valid=True)
        query_params = self._build_param_values(operation.query_params, valid=True)
        headers = self._build_header_values(operation.header_params, valid=True)
        body = self._build_body(operation.request_body_schema, valid=True)
        note = ""
        injected = False

        required_query = [p for p in operation.query_params if p.required]
        if required_query:
            query_params.pop(required_query[0].name, None)
            note = f"Omitted required query parameter '{required_query[0].name}'."
            injected = True
        elif operation.request_body_schema and isinstance(body, dict):
            required_fields = operation.request_body_schema.get("required", [])
            if required_fields:
                body.pop(required_fields[0], None)
                note = f"Omitted required body field '{required_fields[0]}'."
                injected = True

        if not injected:
            return None

        return TestCase(
            test_id=f"{operation.operation_id}__t{index}",
            operation_id=operation.operation_id,
            test_type="invalid_missing_required",
            method=operation.method,
            path=operation.path,
            expected_statuses=MISSING_REQUIRED_EXPECTED_STATUSES,
            path_params=path_params,
            query_params=query_params,
            headers=headers,
            json_body=body,
            notes=note or "Intentional missing required input.",
        )

    def _build_missing_auth_test(self, operation: OperationSpec, index: int) -> TestCase:
        return TestCase(
            test_id=f"{operation.operation_id}__t{index}",
            operation_id=operation.operation_id,
            test_type="missing_auth",
            method=operation.method,
            path=operation.path,
            expected_statuses=[401, 403],
            path_params=self._build_param_values(operation.path_params, valid=True),
            query_params=self._build_param_values(operation.query_params, valid=True),
            headers=self._build_header_values(operation.header_params, valid=True),
            json_body=self._build_body(operation.request_body_schema, valid=True),
            notes="Auth intentionally omitted for secured endpoint.",
        )

    def _build_param_values(self, params: List[ParameterSpec], valid: bool) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for param in params:
            if valid or param.required:
                values[param.name] = (
                    generate_valid_value(param.schema) if valid else generate_invalid_value(param.schema)
                )
        return values

    def _build_header_values(self, params: List[ParameterSpec], valid: bool) -> Dict[str, str]:
        raw = self._build_param_values(params, valid=valid)
        return {k: str(v) for k, v in raw.items()}

    def _build_body(self, schema: Optional[Dict[str, Any]], valid: bool) -> Optional[Dict[str, Any]]:
        if not schema:
            return None
        generated = generate_valid_value(schema) if valid else generate_invalid_value(schema)
        if isinstance(generated, dict):
            return deepcopy(generated)
        return {"value": generated}

    @staticmethod
    def _required_or_first(params: List[ParameterSpec]) -> Optional[ParameterSpec]:
        required = [p for p in params if p.required]
        if required:
            return required[0]
        return params[0] if params else None
