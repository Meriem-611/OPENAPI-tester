# Failure-Aware OpenAPI Tester

Failure-Aware OpenAPI Tester is a Python framework for evaluating REST APIs from OpenAPI/Swagger specs with representative operation selection, LLM-assisted test generation (optional), fixture-aware execution, and structured failure analysis.

## Features

- Parses OpenAPI 3.x and Swagger 2.0 specs.
- Selects representative operations using behavioral partitioning.
- Supports auth-aware filtering (public + credential-compatible secured ops).
- Optionally generates and validates tests with an LLM.
- Uses fixture bootstrap + targeted retries to reduce invalid-ID noise.
- Produces machine-readable and markdown reports with categorized failures.

## Project Layout

```text
failure_aware_openapi_tester/
├── main.py
├── config.py
├── spec_parser.py
├── operation_selector.py
├── test_generator.py
├── executor.py
├── fixture_bootstrap.py
├── heuristic_analyzer.py
├── llm_*.py
├── report_generator.py
├── requirements.txt
├── .env.example
├── evaluation_suite/
└── tests/
```

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Copy env template and fill secrets locally:

```bash
cp .env.example .env
```

3. Edit `.env` and set your real values (do not commit `.env`).

## Basic Usage

```bash
python main.py \
  --spec "evaluation_suite/input_specs/weather/openapi.yaml" \
  --output-dir "sample_outputs/weather_run"
```

## Useful Flags

- `--base-url`: override spec server URL.
- `--auth-token`: bearer token for secured endpoints.
- `--max-selected-operations`: representative selection budget (`0` = auto sample size).
- `--llm-generate-tests`: use LLM for test plan generation.
- `--llm-validate-test-cases`: reviewer/refiner pass for generated tests.
- `--llm-rank-operations`: optional LLM-based operation ordering.
- `--fixture-bootstrap` / `--no-fixture-bootstrap`: enable/disable fixture discovery.
- `--exclude-path-prefix`: skip endpoint families.

## Outputs

Each run writes artifacts such as:

- `report.json`
- `report.md`
- optional LLM artifacts (`llm_generated_test_plan.json`, reviewer notes, operation rank)
- optional fixture artifacts (`fixture_store*.json`, probe logs)

## Security and Git Hygiene

- `.env` is ignored by `.gitignore`.
- Generated run outputs are ignored by `.gitignore`.
- Keep only source code, docs, and input specs in version control.

If secrets were ever committed before cleanup, rotate them immediately (API keys/tokens).

## Tests

```bash
python -m unittest discover -s tests -v
```
