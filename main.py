"""CLI entrypoint for failure-aware OpenAPI testing workflow."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List

from auth_partition import AuthPartition, merge_selector_candidates, partition_operations
from auth_scheme_filter import filter_by_path_prefix_excludes
from config import Config, build_arg_parser, load_config
from executor import TestExecutor
from fixture_bootstrap import (
    apply_fixtures_before_execution,
    retry_with_fixtures,
    sort_tests_for_execution,
    store_to_jsonable,
)
from heuristic_analyzer import HeuristicFailureAnalyzer
from llm_analyzer import LLMFailureAnalyzer
from llm_operation_ranker import rank_operations_with_llm, reorder_operations
from llm_test_case_generator import (
    LLMTestCaseGenerator,
    map_json_to_test_cases,
    serialize_operations_for_prompt,
)
from llm_test_case_validator import LLMTestCaseValidator
from models import FailureRecord, OperationSpec, RunSummary, TestCase
from operation_order import prioritize_public_operations
from operation_selector import OperationSelector
from report_generator import ReportGenerator
from spec_context import build_api_bundle_for_llm, truncate_bundle_if_needed
from spec_parser import OpenAPISpecParser
from test_generator import TestGenerator
from utils import ensure_dir


def _filter_operations_by_available_auth(
    operations: List[OperationSpec],
    auth_token: str | None,
) -> List[OperationSpec]:
    """
    Keep only operations executable with currently available credentials.

    Current credential model supports bearer token auth only:
    - public operations are always eligible
    - secured operations are eligible only when auth_token is present
    """
    has_bearer = bool(auth_token)
    return [op for op in operations if (not op.requires_auth) or has_bearer]


def determine_base_url(spec_metadata: Dict[str, object], cli_base_url: str | None) -> str:
    """Pick base URL from CLI override or spec servers entry."""
    if cli_base_url:
        return cli_base_url

    def resolve_server_url(server: Dict[str, object]) -> str:
        url = str(server.get("url", "")).strip()
        if not url:
            return ""
        variables = server.get("variables", {})
        if isinstance(variables, dict):
            for name, var_obj in variables.items():
                default = ""
                if isinstance(var_obj, dict):
                    default = str(var_obj.get("default", "")).strip()
                url = url.replace("{" + str(name) + "}", default)
        return url

    unresolved_placeholder = re.compile(r"\{[^}]+\}")

    for key in ("servers", "discovered_servers"):
        servers = spec_metadata.get(key, [])
        if isinstance(servers, list):
            for server in servers:
                if isinstance(server, dict) and server.get("url"):
                    resolved = resolve_server_url(server)
                    if resolved and not unresolved_placeholder.search(resolved):
                        return resolved

    raise ValueError(
        "No concrete base URL found. Provide --base-url or ensure spec servers have resolvable URLs."
    )


def _prepare_operations_for_run(
    operations_all: List[OperationSpec],
    raw_spec: Dict[str, object],
    auth_token: str | None,
    auth_scheme_allowlist: str,
    exclude_path_prefixes: List[str],
) -> tuple[List[OperationSpec], AuthPartition]:
    """
    Returns (selector_candidates, auth_partition).

    `selector_candidates` are public + secured endpoints compatible with provided credentials.
    `auth_partition` retains additional secured ops that declare incompatible auth schemes
    so the selector can optionally include one \"contrast\" endpoint.
    """
    ops = _filter_operations_by_available_auth(operations_all, auth_token)
    ops, dropped_paths = filter_by_path_prefix_excludes(ops, exclude_path_prefixes)
    if dropped_paths:
        print(f"[progress] Path-prefix excludes removed {dropped_paths} operation(s).")

    partition = partition_operations(
        ops,
        raw_spec,
        has_credentials=bool(auth_token),
        allowlist_csv=auth_scheme_allowlist,
    )
    candidates = merge_selector_candidates(partition)
    return candidates, partition


def _generate_test_cases(
    config: Config,
    spec_parser: OpenAPISpecParser,
    selected_ops: List[OperationSpec],
    op_index: Dict[str, OperationSpec],
) -> List[TestCase]:
    """Deterministic generation, or LLM + optional reviewer loop when configured."""
    auth_ok = bool(config.auth_token)
    deterministic = TestGenerator(max_tests_per_operation=4, auth_token_present=auth_ok)
    if not config.llm_generate_tests:
        print("[progress] Generating deterministic test cases...")
        return deterministic.generate(selected_ops)
    if not config.effective_llm_api_key():
        print(
            "Warning: --llm-generate-tests requires OPENAI_API_KEY or Azure "
            "(AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY); using deterministic generator."
        )
        print("[progress] Falling back to deterministic test generation...")
        return deterministic.generate(selected_ops)

    gen = LLMTestCaseGenerator(
        config.effective_llm_api_key() or "",
        config.openai_model,
        config.llm_base_url,
        azure_endpoint=config.azure_openai_endpoint,
        azure_deployment=config.azure_openai_deployment,
        azure_api_version=config.azure_openai_api_version,
    )
    selected_ids = {op.operation_id for op in selected_ops}
    print(f"[progress] Building LLM context for {len(selected_ids)} selected operations...")
    bundle = build_api_bundle_for_llm(spec_parser.raw_spec, selected_ids)
    bundle = truncate_bundle_if_needed(bundle)
    print("[progress] Requesting LLM-generated test plan...")
    raw = gen.generate(bundle, selected_ops, auth_token_present=auth_ok)
    if not raw:
        print("Warning: LLM test generation failed; using deterministic generator.")
        print("[progress] Falling back to deterministic test generation...")
        return deterministic.generate(selected_ops)

    working: Dict = dict(raw)
    if config.llm_validate_test_cases:
        print(
            f"[progress] Running LLM test-plan validation "
            f"(max {config.max_llm_validation_iterations} iteration(s))..."
        )
        validator = LLMTestCaseValidator(
            config.effective_llm_api_key() or "",
            config.openai_model,
            config.llm_base_url,
            azure_endpoint=config.azure_openai_endpoint,
            azure_deployment=config.azure_openai_deployment,
            azure_api_version=config.azure_openai_api_version,
        )
        summaries = serialize_operations_for_prompt(selected_ops)
        last_notes = ""
        for it in range(config.max_llm_validation_iterations):
            print(
                f"[progress] LLM validation iteration "
                f"{it + 1}/{config.max_llm_validation_iterations}..."
            )
            vr = validator.validate_and_refine(
                working,
                summaries,
                it + 1,
                config.max_llm_validation_iterations,
                auth_token_present=auth_ok,
            )
            working["test_cases"] = vr["test_cases"]
            last_notes = str(vr.get("reviewer_notes", ""))
            if vr.get("approved"):
                print(f"[progress] LLM reviewer approved on iteration {it + 1}.")
                break
        try:
            (config.output_dir / "llm_test_reviewer_notes.txt").write_text(last_notes, encoding="utf-8")
        except OSError:
            pass

    cases = map_json_to_test_cases(working, op_index)
    if not cases:
        print("Warning: LLM returned no usable test cases; using deterministic generator.")
        print("[progress] Falling back to deterministic test generation...")
        return deterministic.generate(selected_ops)

    try:
        (config.output_dir / "llm_generated_test_plan.json").write_text(
            json.dumps(working, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
    except OSError:
        pass
    return cases


def enrich_with_llm(
    failures: List[FailureRecord],
    llm_analyzer: LLMFailureAnalyzer,
) -> None:
    """Populate failure records with LLM analysis in place."""
    if not llm_analyzer.is_enabled():
        return
    total = len(failures)
    if total == 0:
        return
    print(f"[progress] Running LLM failure analysis for {total} failed test(s)...")
    for idx, failure in enumerate(failures, start=1):
        if idx == 1 or idx == total or idx % 10 == 0:
            print(f"[progress] LLM failure analysis {idx}/{total}...")
        failure.llm = llm_analyzer.analyze_failure(failure)


def run() -> Dict[str, Path]:
    """Execute the full pipeline and return report file paths."""
    print("[progress] Parsing CLI arguments and loading configuration...")
    parser = build_arg_parser()
    args = parser.parse_args()
    config = load_config(args)
    ensure_dir(config.output_dir)

    print(f"[progress] Parsing OpenAPI spec: {config.spec_path}")
    spec_parser = OpenAPISpecParser(config.spec_path)
    operations_all, metadata = spec_parser.parse()
    if not operations_all:
        raise ValueError("No supported operations found in spec.")
    print(f"[progress] Parsed {len(operations_all)} operation(s).")
    base_url = determine_base_url(metadata, config.base_url)
    operations, auth_partition = _prepare_operations_for_run(
        operations_all,
        spec_parser.raw_spec,
        config.auth_token,
        config.auth_scheme_allowlist,
        config.exclude_path_prefixes,
    )
    dropped = len(operations_all) - len(operations)
    if dropped > 0:
        print(f"[progress] Filtered out {dropped} operation(s) (path excludes + auth token gating).")
    print(
        "[progress] Auth partition: "
        f"public={len(auth_partition.public_ops)}, "
        f"secured_satisfiable={len(auth_partition.satisfiable_secured)}, "
        f"secured_contrast={len(auth_partition.contrast_secured)}"
    )
    if not operations:
        raise ValueError(
            "No operations are eligible with current credentials. "
            "Provide --auth-token (or AUTH_TOKEN) or use a spec with public endpoints."
        )

    print("[progress] Selecting representative operations...")
    selector = OperationSelector(max_selected_operations=config.max_selected_operations)
    selection = selector.select(
        operations,
        auth_contrast_pool=auth_partition.contrast_secured if config.include_auth_contrast else [],
        include_auth_contrast=bool(config.include_auth_contrast and config.auth_token),
    )
    selected_ops = prioritize_public_operations(selection.selected_operations)
    op_index: Dict[str, OperationSpec] = {op.operation_id: op for op in selected_ops}
    print(f"[progress] Selected {len(selected_ops)} operation(s) for testing.")

    if config.llm_rank_operations and config.effective_llm_api_key():
        print("[progress] Requesting LLM suggested operation execution order...")
        ranked = rank_operations_with_llm(
            api_key=config.effective_llm_api_key() or "",
            model=config.openai_model,
            base_url=config.llm_base_url,
            azure_endpoint=config.azure_openai_endpoint,
            azure_deployment=config.azure_openai_deployment,
            azure_api_version=config.azure_openai_api_version,
            operations=selected_ops,
            auth_token_present=bool(config.auth_token),
        )
        if ranked:
            selected_ops = reorder_operations(selected_ops, ranked)
            op_index = {op.operation_id: op for op in selected_ops}
            print("[progress] Applied LLM operation ordering.")
            try:
                (config.output_dir / "llm_operation_rank.json").write_text(
                    json.dumps({"ranked_operation_ids": ranked}, indent=2, ensure_ascii=True),
                    encoding="utf-8",
                )
            except OSError:
                pass
        else:
            print("[progress] LLM operation ordering unavailable; using selector order.")

    print("[progress] Generating test cases...")
    test_cases = _generate_test_cases(
        config=config,
        spec_parser=spec_parser,
        selected_ops=selected_ops,
        op_index=op_index,
    )
    print(f"[progress] Generated {len(test_cases)} test case(s).")

    print("[progress] Executing tests (ordered: fewer parameters / safer calls first)...")
    executor = TestExecutor(base_url=base_url, timeout=config.timeout, auth_token=config.auth_token)
    ordered_cases = sort_tests_for_execution(test_cases, op_index)
    if config.fixture_bootstrap and config.fixture_bootstrap_max_probes > 0:
        print("[progress] Running pre-execution prerequisite bootstrap...")
        ordered_cases, pre_store, pre_probe_log = apply_fixtures_before_execution(
            executor=executor,
            operations=operations_all,
            cases=ordered_cases,
            max_probes=config.fixture_bootstrap_max_probes,
        )
        try:
            (config.output_dir / "fixture_store_pre_execution.json").write_text(
                json.dumps(store_to_jsonable(pre_store), indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
            (config.output_dir / "fixture_bootstrap_probes_pre_execution.json").write_text(
                json.dumps(pre_probe_log, indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
        except OSError:
            pass
    execution_results = executor.execute(ordered_cases)
    print(f"[progress] Finished primary execution pass ({len(execution_results)} test(s)).")

    if config.fixture_bootstrap and config.fixture_bootstrap_max_probes > 0:
        print("[progress] Running fixture bootstrap (probe list endpoints, retry placeholder IDs)...")
        execution_results, store, probe_log = retry_with_fixtures(
            executor=executor,
            operations=operations_all,
            initial_results=execution_results,
            max_probes=config.fixture_bootstrap_max_probes,
        )
        try:
            (config.output_dir / "fixture_store.json").write_text(
                json.dumps(store_to_jsonable(store), indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
            (config.output_dir / "fixture_bootstrap_probes.json").write_text(
                json.dumps(probe_log, indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
        except OSError:
            pass
        print(f"[progress] After bootstrap retries: {len(execution_results)} total execution record(s).")

    print("[progress] Running heuristic failure analysis...")
    heuristic = HeuristicFailureAnalyzer()
    failures = heuristic.analyze_failures(execution_results, op_index)
    print(f"[progress] Heuristic analysis found {len(failures)} failure record(s).")

    llm_key = config.effective_llm_api_key() if config.enable_llm else None
    llm_analyzer = LLMFailureAnalyzer(
        api_key=llm_key,
        model=config.openai_model,
        base_url=config.llm_base_url,
        azure_endpoint=config.azure_openai_endpoint,
        azure_deployment=config.azure_openai_deployment,
        azure_api_version=config.azure_openai_api_version,
    )
    enrich_with_llm(failures, llm_analyzer)

    passed = sum(1 for r in execution_results if r.passed)
    failed = len(execution_results) - passed
    execution_errors = sum(1 for r in execution_results if r.error)

    summary = RunSummary(
        spec_name=str(metadata.get("spec_name", config.spec_path.name)),
        total_operations_parsed=len(operations_all),
        total_operations_selected=len(selected_ops),
        total_tests_generated=len(test_cases),
        tests_executed=len(execution_results),
        passed=passed,
        failed=failed,
        execution_errors=execution_errors,
    )

    failure_counts = dict(Counter([failure.heuristic.category for failure in failures]))
    print("[progress] Writing reports...")
    reporter = ReportGenerator(config.output_dir)
    paths = reporter.write_reports(
        summary,
        selection.coverage,
        failure_counts,
        failures,
        execution_results,
    )

    print("Run complete.")
    print(f"Selected operations: {len(selected_ops)} / Parsed operations: {len(operations)}")
    print(f"Tests executed: {len(execution_results)} | Passed: {passed} | Failed: {failed}")
    print(f"JSON report: {paths['json_report']}")
    print(f"Markdown report: {paths['markdown_report']}")
    return paths


if __name__ == "__main__":
    run()
