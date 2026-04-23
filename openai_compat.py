"""Shared chat completion helper: OpenAI-compatible API and Azure OpenAI."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def chat_completion_content(
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    timeout: int = 120,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    azure_endpoint: Optional[str] = None,
    azure_deployment: Optional[str] = None,
    azure_api_version: Optional[str] = None,
) -> Optional[str]:
    """
    Return assistant message text, or None on failure.

    If ``azure_endpoint``, ``azure_deployment``, ``azure_api_version``, and ``api_key``
    are set, calls Azure OpenAI (``api-key`` header, deployment in URL).

    Otherwise uses OpenAI-compatible ``POST {base_url}/chat/completions`` with Bearer auth.
    """
    import requests

    if not api_key:
        return None

    if azure_endpoint and azure_deployment and azure_api_version:
        url = (
            f"{azure_endpoint.rstrip('/')}/openai/deployments/{azure_deployment}"
            "/chat/completions"
        )
        headers = {"api-key": api_key, "Content-Type": "application/json"}
        body: Dict[str, Any] = {"messages": messages, "temperature": temperature}
        params = {"api-version": azure_api_version}
        try:
            response = requests.post(
                url, headers=headers, json=body, params=params, timeout=timeout
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
        except Exception:
            return None

    if base_url and model:
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {"model": model, "messages": messages, "temperature": temperature}
        try:
            response = requests.post(url, headers=headers, json=body, timeout=timeout)
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
        except Exception:
            return None

    return None


def post_chat_json(
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    timeout: int = 120,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    azure_endpoint: Optional[str] = None,
    azure_deployment: Optional[str] = None,
    azure_api_version: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Call chat completions and parse first message content as JSON object."""
    content = chat_completion_content(
        messages,
        temperature=temperature,
        timeout=timeout,
        api_key=api_key,
        base_url=base_url,
        model=model,
        azure_endpoint=azure_endpoint,
        azure_deployment=azure_deployment,
        azure_api_version=azure_api_version,
    )
    if content is None:
        return None
    return _strip_and_parse_json(content)


def _strip_and_parse_json(raw_text: str) -> Optional[Dict[str, Any]]:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return None
    except json.JSONDecodeError:
        return None


def parse_llm_json_content(raw_text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON from an assistant message (handles ``` fences)."""
    return _strip_and_parse_json(raw_text)
