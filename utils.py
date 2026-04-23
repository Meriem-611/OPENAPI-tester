"""General utility helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urljoin

import yaml


def load_json_or_yaml(path: Path) -> Dict[str, Any]:
    """Load and parse JSON or YAML file by extension."""
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    if suffix == ".json":
        return json.loads(text)
    raise ValueError(f"Unsupported spec format for file: {path}")


def ensure_dir(path: Path) -> None:
    """Create output directory if needed."""
    path.mkdir(parents=True, exist_ok=True)


def shorten_text(value: Any, max_len: int = 500) -> str:
    """Create compact textual excerpt for logs/reports."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=True)
    else:
        text = str(value)
    return text if len(text) <= max_len else f"{text[:max_len]}..."


def build_url(base_url: str, path: str) -> str:
    """Join base URL and endpoint path safely."""
    if not base_url.endswith("/"):
        base_url = f"{base_url}/"
    return urljoin(base_url, path.lstrip("/"))


def first_or_none(values: Iterable[Any]) -> Optional[Any]:
    """Return first item from iterable or None."""
    for item in values:
        return item
    return None
