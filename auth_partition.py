"""Partition operations by whether declared OpenAPI security matches provided credentials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from models import OperationSpec

from auth_scheme_filter import infer_allowlist_from_credentials, parse_allowlist_csv, scheme_keys_by_name


@dataclass
class AuthPartition:
    public_ops: List[OperationSpec]
    satisfiable_secured: List[OperationSpec]
    contrast_secured: List[OperationSpec]


def _alt_keys(alt: Dict[str, List[str]], scheme_for_name: Dict[str, str]) -> Set[str]:
    keys: Set[str] = set()
    for scheme_name in alt.keys():
        k = scheme_for_name.get(str(scheme_name))
        if k:
            keys.add(k.lower())
    return keys


def op_satisfiable_with_allowlist(
    op: OperationSpec,
    *,
    scheme_for_name: Dict[str, str],
    allow: Set[str],
) -> bool:
    if not op.requires_auth:
        return True
    if not op.security:
        return True
    for alt in op.security:
        if not alt:
            return True
        keys = _alt_keys(alt, scheme_for_name)
        if keys and keys.issubset(allow):
            return True
    return False


def op_has_only_unknown_schemes(op: OperationSpec, scheme_for_name: Dict[str, str]) -> bool:
    if not op.requires_auth or not op.security:
        return False
    for alt in op.security:
        for scheme_name in alt.keys():
            if scheme_name not in scheme_for_name:
                return True
    return False


def partition_operations(
    operations: List[OperationSpec],
    raw_spec: Dict[str, Any],
    *,
    has_credentials: bool,
    allowlist_csv: str,
) -> AuthPartition:
    scheme_for_name = scheme_keys_by_name(raw_spec)
    public: List[OperationSpec] = []
    secured: List[OperationSpec] = []
    for op in operations:
        if op.requires_auth:
            secured.append(op)
        else:
            public.append(op)

    if not scheme_for_name:
        # Spec missing scheme components: fall back to coarse auth flag.
        if has_credentials:
            return AuthPartition(public_ops=public, satisfiable_secured=secured, contrast_secured=[])
        return AuthPartition(public_ops=public, satisfiable_secured=[], contrast_secured=[])

    if allowlist_csv.strip():
        allow: Optional[Set[str]] = parse_allowlist_csv(allowlist_csv)
    else:
        allow = infer_allowlist_from_credentials(has_credentials)
        if allow is None:
            return AuthPartition(public_ops=public, satisfiable_secured=secured, contrast_secured=[])

    satisfiable: List[OperationSpec] = []
    contrast: List[OperationSpec] = []
    for op in secured:
        if not has_credentials:
            continue
        if op_has_only_unknown_schemes(op, scheme_for_name):
            satisfiable.append(op)
            continue
        if op_satisfiable_with_allowlist(op, scheme_for_name=scheme_for_name, allow=allow):
            satisfiable.append(op)
        else:
            contrast.append(op)

    return AuthPartition(public_ops=public, satisfiable_secured=satisfiable, contrast_secured=contrast)


def merge_selector_candidates(partition: AuthPartition) -> List[OperationSpec]:
    return list(partition.public_ops) + list(partition.satisfiable_secured)
