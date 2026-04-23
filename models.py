"""Core dataclasses used across the project."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set


@dataclass
class ParameterSpec:
    """Normalized OpenAPI parameter representation."""

    name: str
    location: str  # path, query, header
    required: bool
    schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OperationSpec:
    """Normalized API operation representation used by the framework."""

    operation_id: str
    method: str
    path: str
    summary: str
    tags: List[str]
    resource_group: str
    requires_auth: bool
    # Optional raw OpenAPI security block for this operation (after path/global merge).
    security: List[Dict[str, List[str]]] = field(default_factory=list)
    path_params: List[ParameterSpec] = field(default_factory=list)
    query_params: List[ParameterSpec] = field(default_factory=list)
    header_params: List[ParameterSpec] = field(default_factory=list)
    request_body_schema: Optional[Dict[str, Any]] = None
    expected_statuses: List[int] = field(default_factory=list)
    response_schema: Optional[Dict[str, Any]] = None
    is_state_changing: bool = False
    has_body: bool = False

    def behavioral_partitions(self) -> Set[str]:
        """Compute coarse behavioral partition labels used for selection coverage."""
        method_map = {
            "get": "method:get",
            "post": "method:post",
            "put": "method:update",
            "patch": "method:update",
            "delete": "method:delete",
        }
        method_partition = method_map.get(self.method.lower(), "method:other")
        auth_partition = "auth:authenticated" if self.requires_auth else "auth:public"
        body_partition = "input:has_body" if self.has_body else "input:no_body"
        behavior_partition = (
            "behavior:state_changing" if self.is_state_changing else "behavior:read_only"
        )
        return {method_partition, auth_partition, body_partition, behavior_partition}


@dataclass
class TestCase:
    """Generated test case for an operation."""

    test_id: str
    operation_id: str
    test_type: str  # baseline_valid, invalid_wrong_type, invalid_missing_required, missing_auth
    method: str
    path: str
    expected_statuses: List[int]
    path_params: Dict[str, Any] = field(default_factory=dict)
    query_params: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    json_body: Optional[Dict[str, Any]] = None
    notes: str = ""
    expected_response_summary: str = ""
    generated_by: str = "deterministic"  # deterministic | llm


@dataclass
class ExecutionResult:
    """Single executed test result."""

    test_case: TestCase
    endpoint: str
    observed_status: Optional[int]
    response_excerpt: str
    latency_ms: Optional[float]
    passed: bool
    error: Optional[str] = None
    observed_json: Optional[Any] = None


@dataclass
class HeuristicAnalysis:
    """Deterministic failure classification output."""

    category: str
    confidence: float
    reason: str


@dataclass
class LLMAnalysis:
    """LLM-enriched failure interpretation output."""

    likely_cause: str
    qa_bug_summary: str
    suggested_follow_up_tests: List[str] = field(default_factory=list)


@dataclass
class FailureRecord:
    """Unified failure entry used by report generation."""

    execution: ExecutionResult
    heuristic: HeuristicAnalysis
    llm: Optional[LLMAnalysis] = None


@dataclass
class CoverageSummary:
    """Selection/coverage snapshot for reporting."""

    resource_groups_found: List[str]
    resource_groups_covered: List[str]
    behavioral_partitions_covered: List[str]
    selected_operations: List[str]


@dataclass
class RunSummary:
    """Top-level report summary numbers."""

    spec_name: str
    total_operations_parsed: int
    total_operations_selected: int
    total_tests_generated: int
    tests_executed: int
    passed: int
    failed: int
    execution_errors: int


def to_serializable(data: Any) -> Any:
    """Convert nested dataclasses into plain JSON-serializable dictionaries."""
    if hasattr(data, "__dataclass_fields__"):
        return asdict(data)
    if isinstance(data, list):
        return [to_serializable(item) for item in data]
    if isinstance(data, dict):
        return {key: to_serializable(value) for key, value in data.items()}
    return data
