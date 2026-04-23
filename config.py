"""Configuration loading from environment variables and CLI values."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from package directory (same folder as this file) if present.
load_dotenv(Path(__file__).resolve().parent / ".env")
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Config:
    """Runtime configuration for a tool run."""

    spec_path: Path
    base_url: Optional[str]
    output_dir: Path
    auth_token: Optional[str]
    max_selected_operations: Optional[int]
    mode: str
    enable_llm: bool
    timeout: int
    openai_api_key: Optional[str]
    openai_model: str
    llm_base_url: str
    azure_openai_endpoint: Optional[str]
    azure_openai_api_key: Optional[str]
    azure_openai_deployment: Optional[str]
    azure_openai_api_version: Optional[str]
    llm_generate_tests: bool
    llm_validate_test_cases: bool
    max_llm_validation_iterations: int
    fixture_bootstrap: bool
    fixture_bootstrap_max_probes: int
    llm_rank_operations: bool
    auth_scheme_allowlist: str
    exclude_path_prefixes: List[str]
    include_auth_contrast: bool

    def uses_azure_openai(self) -> bool:
        """True when Azure endpoint + key are set; chat uses deployment URL and api-key header."""
        return bool(
            self.azure_openai_endpoint
            and self.azure_openai_api_key
            and self.azure_openai_deployment
            and self.azure_openai_api_version
        )

    def effective_llm_api_key(self) -> Optional[str]:
        """Prefer Azure key when Azure is configured; otherwise OpenAI key."""
        if self.uses_azure_openai():
            return self.azure_openai_api_key
        return self.openai_api_key


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def build_arg_parser() -> argparse.ArgumentParser:
    """Build command line parser."""
    parser = argparse.ArgumentParser(description="Failure-aware OpenAPI tester")
    parser.add_argument("--spec", required=True, help="Path to OpenAPI spec (YAML or JSON)")
    parser.add_argument("--base-url", default=None, help="Override base URL for requests")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where JSON/Markdown reports are written",
    )
    parser.add_argument("--auth-token", default=None, help="Bearer token for authenticated APIs")
    parser.add_argument(
        "--max-selected-operations",
        type=int,
        default=int(os.getenv("MAX_SELECTED_OPERATIONS", "0")),
        help="Max operations selected; use 0 for auto representative sample size",
    )
    parser.add_argument(
        "--mode",
        choices=["quick", "full"],
        default=os.getenv("MODE", "quick"),
        help="Selection strategy mode (does not enforce an operation cap)",
    )
    parser.add_argument(
        "--enable-llm",
        action="store_true",
        help="Enable optional LLM failure analysis (requires API key)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("DEFAULT_TIMEOUT", "10")),
        help="HTTP request timeout in seconds",
    )
    parser.add_argument(
        "--llm-generate-tests",
        action="store_true",
        help="After selecting operations, use an LLM to propose test cases and expected statuses (needs OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--llm-validate-test-cases",
        action="store_true",
        help="Use a second LLM pass to review/revise proposed test cases before execution",
    )
    parser.add_argument(
        "--max-llm-validation-iterations",
        type=int,
        default=int(os.getenv("MAX_LLM_VALIDATION_ITERATIONS", "2")),
        help="Max reviewer iterations for test-case LLM loop",
    )
    parser.add_argument(
        "--fixture-bootstrap",
        action=argparse.BooleanOptionalAction,
        default=_as_bool(os.getenv("FIXTURE_BOOTSTRAP", "true")),
        help="After first execution pass, probe simple GET list endpoints to substitute placeholder IDs and retry",
    )
    parser.add_argument(
        "--fixture-bootstrap-max-probes",
        type=int,
        default=int(os.getenv("FIXTURE_BOOTSTRAP_MAX_PROBES", "25")),
        help="Max bootstrap GET probes to run when fixture bootstrap is enabled",
    )
    parser.add_argument(
        "--llm-rank-operations",
        action="store_true",
        help="Use an LLM to suggest an execution order for selected operations before test generation",
    )
    parser.add_argument(
        "--auth-scheme-allowlist",
        default=os.getenv("AUTH_SCHEME_ALLOWLIST", ""),
        help=(
            "Comma-separated OpenAPI security scheme keys to allow when credentials are present "
            "(e.g. 'http:bearer,oauth2'). Empty uses a conservative default for bearer tokens."
        ),
    )
    parser.add_argument(
        "--exclude-path-prefix",
        action="append",
        dest="exclude_path_prefixes",
        default=[],
        help=(
            "Exclude operations whose path starts with this prefix (repeatable). "
            "Alternatively set EXCLUDE_PATH_PREFIXES as a comma-separated list."
        ),
    )
    parser.add_argument(
        "--include-auth-contrast",
        action=argparse.BooleanOptionalAction,
        default=_as_bool(os.getenv("INCLUDE_AUTH_CONTRAST", "true")),
        help=(
            "When credentials are present and the spec declares multiple incompatible auth schemes, "
            "try to include one secured endpoint that is unlikely to succeed with the provided credential "
            "(useful for wrong-auth behavior signals)."
        ),
    )
    return parser


def load_config(args: argparse.Namespace) -> Config:
    """Merge CLI values and environment variables into Config."""
    mode = args.mode
    cli_budget = int(args.max_selected_operations)
    max_selected = None if cli_budget <= 0 else cli_budget

    azure_ep = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    azure_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    if azure_ep and azure_key:
        azure_deployment = (
            os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
            or os.getenv("MODEL", "").strip()
            or os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
        )
        azure_api_version = (
            os.getenv("AZURE_OPENAI_API_VERSION", "").strip()
            or os.getenv("API_VERSION", "").strip()
            or "2024-08-01-preview"
        )
    else:
        azure_ep = None
        azure_key = None
        azure_deployment = None
        azure_api_version = None

    exclude_prefix_env = os.getenv("EXCLUDE_PATH_PREFIXES", "").strip()
    exclude_prefixes: List[str] = list(getattr(args, "exclude_path_prefixes", []) or [])
    if exclude_prefix_env:
        exclude_prefixes.extend([p.strip() for p in exclude_prefix_env.split(",") if p.strip()])

    return Config(
        spec_path=Path(args.spec),
        base_url=args.base_url,
        output_dir=Path(args.output_dir),
        auth_token=args.auth_token or os.getenv("AUTH_TOKEN"),
        max_selected_operations=max_selected,
        mode=mode,
        enable_llm=bool(args.enable_llm),
        timeout=max(1, int(args.timeout)),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        llm_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        azure_openai_endpoint=azure_ep or None,
        azure_openai_api_key=azure_key or None,
        azure_openai_deployment=azure_deployment or None,
        azure_openai_api_version=azure_api_version or None,
        llm_generate_tests=bool(args.llm_generate_tests),
        llm_validate_test_cases=bool(args.llm_validate_test_cases),
        max_llm_validation_iterations=max(1, int(args.max_llm_validation_iterations)),
        fixture_bootstrap=bool(args.fixture_bootstrap),
        fixture_bootstrap_max_probes=max(0, int(args.fixture_bootstrap_max_probes)),
        llm_rank_operations=bool(args.llm_rank_operations),
        auth_scheme_allowlist=str(args.auth_scheme_allowlist or ""),
        exclude_path_prefixes=exclude_prefixes,
        include_auth_contrast=bool(args.include_auth_contrast),
    )
