"""Best-effort fixture discovery from sibling list endpoints to reduce placeholder IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests

from executor import TestExecutor
from models import ExecutionResult, OperationSpec, TestCase
from utils import build_url


@dataclass
class FixtureStore:
    """Maps path parameter names to candidate values discovered at runtime."""

    values: Dict[str, List[Any]] = field(default_factory=dict)
    provenance: Dict[str, List[str]] = field(default_factory=dict)

    def add(self, param: str, value: Any, source: str) -> None:
        if value is None:
            return
        key = param.lower()
        self.values.setdefault(key, [])
        self.provenance.setdefault(key, [])
        if value in self.values[key]:
            return
        self.values[key].append(value)
        self.provenance[key].append(source)

    def pick(self, param: str) -> Optional[Any]:
        key = param.lower()
        vals = self.values.get(key)
        return vals[0] if vals else None


def _path_param_count(path: str) -> int:
    return len(re.findall(r"\{[^}]+\}", path))


def _list_score(op: OperationSpec) -> float:
    """Higher is more likely a collection/list endpoint."""
    score = 0.0
    m = op.method.lower()
    if m == "get":
        score += 3.0
    oid = op.operation_id.lower()
    summary = op.summary.lower()
    if "list" in oid or "list" in summary:
        score += 2.0
    if "search" in oid or "search" in summary:
        score += 1.0
    if op.is_state_changing:
        score -= 2.0
    if op.has_body:
        score -= 1.0
    score -= 0.2 * _path_param_count(op.path)
    return score


def sort_tests_for_execution(tests: List[TestCase], op_index: Dict[str, OperationSpec]) -> List[TestCase]:
    """
    Order tests to run cheaper/safer calls first:
    - missing_auth checks early (auth behavior signal)
    - fewer path parameters first
    - non state-changing before writes
    """

    def key(tc: TestCase) -> Tuple:
        spec = op_index.get(tc.operation_id)
        ppc = _path_param_count(tc.path)
        qpc = len(tc.query_params or {})
        missing_auth = 1 if tc.test_type.lower() in {"missing_auth", "no_bearer", "unauthenticated_check"} else 0
        state = 1 if spec and spec.is_state_changing else 0
        return (
            -missing_auth,
            ppc + qpc,
            state,
            tc.method.lower() != "get",
            tc.operation_id,
            tc.test_id,
        )

    return sorted(tests, key=key)


def choose_list_operations(
    operations: List[OperationSpec],
    wanted_params: Iterable[str],
) -> List[OperationSpec]:
    wanted = {p.lower() for p in wanted_params}
    candidates: List[OperationSpec] = []
    for op in operations:
        if op.method.lower() != "get":
            continue
        if op.is_state_changing:
            continue
        if op.has_body:
            continue
        names = {p.name.lower() for p in op.path_params}
        # Prefer direct param-name overlap, but also allow generic list/catalog
        # endpoints (with no path params) as seed sources.
        if not names.intersection(wanted):
            path_l = op.path.lower()
            oid_l = op.operation_id.lower()
            if names:
                continue
            if not any(k in path_l or k in oid_l for k in ("list", "providers", "services", "catalog")):
                continue
        if _path_param_count(op.path) > 1:
            continue
        candidates.append(op)
    candidates.sort(key=_list_score, reverse=True)
    return candidates


def _extract_ids_from_json(obj: Any, depth: int = 0) -> List[Any]:
    if depth > 8:
        return []
    out: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in {"id", "node_id"} and isinstance(v, (int, str)):
                out.append(v)
            if lk.endswith("_id") and isinstance(v, (int, str)):
                out.append(v)
            out.extend(_extract_ids_from_json(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj[:200]:
            out.extend(_extract_ids_from_json(item, depth + 1))
    return out


def run_bootstrap_probes(
    *,
    base_url: str,
    timeout: int,
    auth_token: Optional[str],
    operations: List[OperationSpec],
    wanted_params: Iterable[str],
    max_probes: int,
) -> Tuple[FixtureStore, List[Dict[str, Any]]]:
    store = FixtureStore()
    log: List[Dict[str, Any]] = []
    wanted_set = {str(p).lower() for p in wanted_params}
    probes = choose_list_operations(operations, wanted_set)[:max_probes]
    for op in probes:
        url = build_url(base_url.rstrip("/"), op.path)
        headers: Dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        try:
            resp = requests.get(url, headers=headers or None, timeout=timeout)
            entry = {
                "operation_id": op.operation_id,
                "path": op.path,
                "status": resp.status_code,
            }
            if resp.status_code // 100 != 2:
                log.append(entry)
                continue
            try:
                data = resp.json()
            except Exception:
                log.append({**entry, "note": "non-json"})
                continue
            ids = _extract_ids_from_json(data)
            entry["ids_found"] = min(5, len(ids))
            log.append(entry)
            path_param_names = [p.name for p in op.path_params]
            if path_param_names:
                for pid in path_param_names:
                    for val in ids:
                        store.add(pid, val, f"bootstrap:{op.operation_id}")
            else:
                # For list/catalog endpoints without path params, seed all currently
                # wanted params as a best-effort fallback.
                for wanted_name in wanted_set:
                    for val in ids:
                        store.add(wanted_name, val, f"bootstrap:{op.operation_id}")
        except Exception as exc:
            log.append({"operation_id": op.operation_id, "path": op.path, "error": str(exc)})
    return store, log


def maybe_substitute_path_params(tc: TestCase, store: FixtureStore) -> TestCase:
    new_params = dict(tc.path_params)
    changed = False
    for k, v in list(new_params.items()):
        substitute = store.pick(k)
        if substitute is None:
            continue
        if str(v).lower() in {"test", "1", "12345", "123", "validmanifestcode", "my-github-app"}:
            new_params[k] = substitute
            changed = True
    if not changed:
        return tc
    return TestCase(
        test_id=tc.test_id + "__fixture_retry",
        operation_id=tc.operation_id,
        test_type=tc.test_type,
        method=tc.method,
        path=tc.path,
        expected_statuses=tc.expected_statuses,
        path_params=new_params,
        query_params=dict(tc.query_params),
        headers=dict(tc.headers),
        json_body=tc.json_body,
        notes=(tc.notes + " [fixture-substituted]").strip(),
        expected_response_summary=tc.expected_response_summary,
        generated_by=tc.generated_by,
    )


def retry_with_fixtures(
    *,
    executor: TestExecutor,
    operations: List[OperationSpec],
    initial_results: List[ExecutionResult],
    max_probes: int,
) -> Tuple[List[ExecutionResult], FixtureStore, List[Dict[str, Any]]]:
    """
    Append retry executions for failures that look like placeholder IDs.

    Original results are preserved; retries are appended with altered test_id.
    """
    wanted: List[str] = []
    for r in initial_results:
        if r.passed or r.error:
            continue
        if r.observed_status not in {404, 422}:
            continue
        for k in r.test_case.path_params.keys():
            wanted.append(k)
    store, probe_log = run_bootstrap_probes(
        base_url=executor.base_url,
        timeout=executor.timeout,
        auth_token=executor.auth_token,
        operations=operations,
        wanted_params=set(wanted),
        max_probes=max_probes,
    )
    extras: List[ExecutionResult] = []
    for r in initial_results:
        if r.passed or r.error:
            continue
        if r.observed_status not in {404, 422}:
            continue
        new_case = maybe_substitute_path_params(r.test_case, store)
        if new_case.test_id == r.test_case.test_id:
            continue
        extras.append(executor.execute_one(new_case))
    return initial_results + extras, store, probe_list_to_jsonable(probe_log)


def apply_fixtures_before_execution(
    *,
    executor: TestExecutor,
    operations: List[OperationSpec],
    cases: List[TestCase],
    max_probes: int,
) -> Tuple[List[TestCase], FixtureStore, List[Dict[str, Any]]]:
    """
    Pre-execution prerequisite bootstrap:
    - probes helper/list endpoints not necessarily in selected ops,
    - substitutes placeholder path params in planned tests before first execution pass.
    """
    wanted: Set[str] = set()
    for tc in cases:
        for key in tc.path_params.keys():
            wanted.add(key)
    if not wanted or max_probes <= 0:
        return cases, FixtureStore(), []
    store, probe_log = run_bootstrap_probes(
        base_url=executor.base_url,
        timeout=executor.timeout,
        auth_token=executor.auth_token,
        operations=operations,
        wanted_params=wanted,
        max_probes=max_probes,
    )
    adjusted: List[TestCase] = []
    for tc in cases:
        adjusted.append(maybe_substitute_path_params(tc, store))
    return adjusted, store, probe_list_to_jsonable(probe_log)


def probe_list_to_jsonable(log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return log


def store_to_jsonable(store: FixtureStore) -> Dict[str, Any]:
    return {"values": store.values, "provenance": store.provenance}
