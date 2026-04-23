"""Representative operation selection using equivalence partitioning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from models import CoverageSummary, OperationSpec


@dataclass
class SelectionResult:
    """Selected operations and associated coverage summary."""

    selected_operations: List[OperationSpec]
    coverage: CoverageSummary


class OperationSelector:
    """Greedy selector balancing resource coverage and global behavior coverage."""

    def __init__(self, max_selected_operations: Optional[int]):
        self.max_selected_operations = (
            max_selected_operations if (max_selected_operations is not None and max_selected_operations > 0) else None
        )

    def select(
        self,
        operations: List[OperationSpec],
        auth_contrast_pool: Optional[List[OperationSpec]] = None,
        include_auth_contrast: bool = False,
    ) -> SelectionResult:
        """Select representative operations under configured budget."""
        if not operations:
            return SelectionResult(
                selected_operations=[],
                coverage=CoverageSummary(
                    resource_groups_found=[],
                    resource_groups_covered=[],
                    behavioral_partitions_covered=[],
                    selected_operations=[],
                ),
            )

        has_explicit_budget = self.max_selected_operations is not None
        budget = self.max_selected_operations if has_explicit_budget else len(operations)
        budget = min(max(1, budget), len(operations))

        selected: List[OperationSpec] = []
        selected_ids: Set[str] = set()
        covered_partitions: Set[str] = set()

        by_group: Dict[str, List[OperationSpec]] = {}
        for op in operations:
            by_group.setdefault(op.resource_group, []).append(op)

        # Step 1: ensure each resource group is represented once where budget allows.
        for group in sorted(by_group.keys()):
            if len(selected) >= budget:
                break
            best = self._best_operation_for_group(by_group[group], covered_partitions)
            if best.operation_id not in selected_ids:
                selected.append(best)
                selected_ids.add(best.operation_id)
                covered_partitions |= best.behavioral_partitions()

        # Step 2: greedily add operations that maximize uncovered partition gain.
        while len(selected) < budget:
            best_candidate, gain = self._best_global_candidate(operations, selected_ids, covered_partitions)
            if best_candidate is None or gain <= 0:
                break
            selected.append(best_candidate)
            selected_ids.add(best_candidate.operation_id)
            covered_partitions |= best_candidate.behavioral_partitions()

        # Step 3: only when user explicitly sets a budget, fill remaining slots with
        # high-diversity leftovers. Auto mode (no explicit budget) stops at representative set.
        if has_explicit_budget and len(selected) < budget:
            leftovers = [op for op in operations if op.operation_id not in selected_ids]
            leftovers.sort(
                key=lambda op: (
                    len(op.behavioral_partitions()),
                    not op.requires_auth,
                    op.operation_id,
                ),
                reverse=True,
            )
            for op in leftovers:
                if len(selected) >= budget:
                    break
                selected.append(op)
                selected_ids.add(op.operation_id)
                covered_partitions |= op.behavioral_partitions()

        secured_in_catalog = [op for op in operations if op.requires_auth]
        if secured_in_catalog and budget >= 2 and not any(op.requires_auth for op in selected):
            selected = self._ensure_at_least_one_secured(
                selected, selected_ids, secured_in_catalog, operations, budget
            )
            selected_ids = {op.operation_id for op in selected}
            covered_partitions = set()
            for op in selected:
                covered_partitions |= op.behavioral_partitions()

        if include_auth_contrast and auth_contrast_pool and budget >= 2:
            satisfiable_secured = [op for op in operations if op.requires_auth]
            selected = self._maybe_add_auth_contrast(
                selected,
                selected_ids,
                auth_contrast_pool,
                satisfiable_secured,
                budget,
            )
            selected_ids = {op.operation_id for op in selected}
            covered_partitions = set()
            for op in selected:
                covered_partitions |= op.behavioral_partitions()

        coverage = CoverageSummary(
            resource_groups_found=sorted(set(op.resource_group for op in operations)),
            resource_groups_covered=sorted(set(op.resource_group for op in selected)),
            behavioral_partitions_covered=sorted(covered_partitions),
            selected_operations=[op.operation_id for op in selected],
        )
        return SelectionResult(selected_operations=selected, coverage=coverage)

    @staticmethod
    def _ensure_at_least_one_secured(
        selected: List[OperationSpec],
        selected_ids: Set[str],
        secured_in_catalog: List[OperationSpec],
        _operations: List[OperationSpec],
        budget: int,
    ) -> List[OperationSpec]:
        """
        If the API declares secured operations and budget >= 2, ensure one secured op is selected.
        Budget 1 keeps a single slot (typically public-first); no forced secured slot.
        """
        best_secured = sorted(
            secured_in_catalog,
            key=lambda op: (len(op.behavioral_partitions()), op.operation_id),
            reverse=True,
        )[0]
        if best_secured.operation_id in selected_ids:
            return selected
        out = list(selected)
        if len(out) < budget:
            out.append(best_secured)
            return out
        out[-1] = best_secured
        return out

    @staticmethod
    def _maybe_add_auth_contrast(
        selected: List[OperationSpec],
        selected_ids: Set[str],
        contrast_pool: List[OperationSpec],
        satisfiable_secured: List[OperationSpec],
        budget: int,
    ) -> List[OperationSpec]:
        """
        If at least one satisfiable secured op is already selected, swap in one secured
        operation from `contrast_pool` (declared incompatible auth schemes vs provided creds).
        """
        if not contrast_pool:
            return selected
        if not any(op.operation_id in selected_ids for op in satisfiable_secured):
            return selected
        scored = sorted(
            contrast_pool,
            key=lambda op: (len(op.behavioral_partitions()), op.operation_id),
            reverse=True,
        )
        pick = next((op for op in scored if op.operation_id not in selected_ids), None)
        if pick is None:
            return selected
        out = list(selected)
        if len(out) < budget:
            out.append(pick)
            return out
        out[-1] = pick
        return out

    def _best_operation_for_group(
        self, candidates: List[OperationSpec], covered_partitions: Set[str]
    ) -> OperationSpec:
        scored = []
        for idx, op in enumerate(candidates):
            gain = len(op.behavioral_partitions() - covered_partitions)
            prefer_public = 1 if not op.requires_auth else 0
            # Include positional tie-breaker so sorting never falls back to comparing
            # OperationSpec objects directly when operation_ids are duplicated.
            scored.append((gain, prefer_public, op.is_state_changing, op.operation_id, -idx, op))
        scored.sort(reverse=True)
        return scored[0][-1]

    def _best_global_candidate(
        self,
        operations: List[OperationSpec],
        selected_ids: Set[str],
        covered_partitions: Set[str],
    ) -> Tuple[OperationSpec | None, int]:
        best: OperationSpec | None = None
        best_gain = -1
        best_tiebreak = ()
        for op in operations:
            if op.operation_id in selected_ids:
                continue
            gain = len(op.behavioral_partitions() - covered_partitions)
            tiebreak = (not op.requires_auth, op.is_state_changing, op.operation_id)
            if gain > best_gain or (gain == best_gain and tiebreak > best_tiebreak):
                best = op
                best_gain = gain
                best_tiebreak = tiebreak
        return best, best_gain
