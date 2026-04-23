"""Filter operations using OpenAPI `components.securitySchemes` + operation `security`."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from models import OperationSpec


def _normalize_scheme_key(raw: Dict[str, Any]) -> str:
    """
    Map a securityScheme object to a coarse key used for allowlisting.

    Examples:
    - {type: http, scheme: bearer} -> http:bearer
    - {type: http, scheme: basic} -> http:basic
    - {type: apiKey, in: header, name: X-Api-Key} -> apiKey:header
    - {type: oauth2, ...} -> oauth2
    - {type: openIdConnect, ...} -> openIdConnect
    """
    st = str(raw.get("type", "")).lower()
    if st == "http":
        scheme = str(raw.get("scheme", "")).lower()
        return f"http:{scheme}" if scheme else "http"
    if st == "apiKey":
        loc = str(raw.get("in", "")).lower()
        return f"apiKey:{loc}" if loc else "apiKey"
    return st or "unknown"


def scheme_keys_by_name(raw_spec: Dict[str, Any]) -> Dict[str, str]:
    comps = raw_spec.get("components") or {}
    schemes = comps.get("securitySchemes") or {}
    if not isinstance(schemes, dict):
        return {}
    out: Dict[str, str] = {}
    for name, body in schemes.items():
        if isinstance(body, dict):
            out[str(name)] = _normalize_scheme_key(body)
    return out


def infer_allowlist_from_credentials(has_bearer_token: bool) -> Optional[Set[str]]:
    """
    When user did not specify an allowlist, infer a conservative default from what
    this runner can actually attach today: HTTP Bearer Authorization.

    Returns None meaning \"do not filter by scheme\" (spec missing securitySchemes).
    """
    if not has_bearer_token:
        return None
    return {"http:bearer", "oauth2", "openidconnect"}


def parse_allowlist_csv(value: str) -> Set[str]:
    parts = [p.strip().lower() for p in value.split(",") if p.strip()]
    return set(parts)


def filter_operations_by_security_allowlist(
    operations: List[OperationSpec],
    raw_spec: Dict[str, Any],
    *,
    has_credentials: bool,
    allowlist_csv: str,
) -> List[OperationSpec]:
    """
    Keep operations if:
    - public (`requires_auth` false), OR
    - optional-auth alternative exists (empty dict in security OR), OR
    - every scheme referenced in at least one OR-alternative is in the allowlist.

    If components.securitySchemes is missing, this is a no-op (OpenAPI incomplete).
    """
    scheme_for_name = scheme_keys_by_name(raw_spec)
    if not scheme_for_name:
        return operations

    allow: Optional[Set[str]]
    if allowlist_csv.strip():
        allow = parse_allowlist_csv(allowlist_csv)
    else:
        allow = infer_allowlist_from_credentials(has_credentials)
        if allow is None:
            return operations

    def alt_allowed(alt: Dict[str, List[str]]) -> bool:
        if not alt:
            return True
        keys = set()
        for scheme_name in alt.keys():
            key = scheme_for_name.get(str(scheme_name))
            if not key:
                return False
            keys.add(key.lower())
        return bool(keys) and keys.issubset(allow)

    def op_allowed(op: OperationSpec) -> bool:
        if not op.requires_auth:
            return True
        if not has_credentials:
            return False
        if not op.security:
            return True
        return any(alt_allowed(alt) for alt in op.security)

    return [op for op in operations if op_allowed(op)]


def filter_by_path_prefix_excludes(operations: List[OperationSpec], prefixes: List[str]) -> Tuple[List[OperationSpec], int]:
    if not prefixes:
        return operations, 0
    pfx = [p.strip().lower() for p in prefixes if p.strip()]
    if not pfx:
        return operations, 0
    kept: List[OperationSpec] = []
    dropped = 0
    for op in operations:
        path_l = op.path.lower()
        if any(path_l.startswith(pref) for pref in pfx):
            dropped += 1
            continue
        kept.append(op)
    return kept, dropped
