"""Deterministic provenance-aware fact resolution."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from shared.domain import FactBag, FactObservation, FactSource, FactState, ResolvedFact

from app.services.facts.registry import FactRegistry, get_fact_registry


SOURCE_PRIORITY: dict[FactSource, int] = {
    FactSource.OPERATOR: 0,
    FactSource.STRUCTURED_FIELD: 10,
    FactSource.DIRECTORY: 20,
    FactSource.DIAGNOSTIC: 30,
    FactSource.COMMENT: 40,
    FactSource.PARSER: 50,
    FactSource.LLM: 60,
}


def _identity(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _is_expired(observation: FactObservation, now: datetime) -> bool:
    if not observation.expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(observation.expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= now


def merge_observations(
    observations: Iterable[FactObservation],
    *,
    revision: int = 0,
    registry: FactRegistry | None = None,
    include_shadow: bool = False,
    now: datetime | None = None,
) -> FactBag:
    registry = registry or get_fact_registry()
    current_time = now or datetime.now(timezone.utc)
    grouped: dict[str, list[FactObservation]] = defaultdict(list)
    for observation in observations:
        registry.require(observation.key)
        if observation.metadata.get("shadow") and not include_shadow:
            continue
        grouped[observation.key].append(observation)

    resolved: dict[str, ResolvedFact] = {}
    for key, items in grouped.items():
        ordered = sorted(items, key=lambda item: SOURCE_PRIORITY[item.source])
        live = [item for item in ordered if not _is_expired(item, current_time)]
        if not live:
            resolved[key] = ResolvedFact(
                key=key,
                state=FactState.STALE,
                observations=ordered,
                conflict_reason="all_observations_expired",
            )
            continue

        operator = next(
            (
                item
                for item in live
                if item.source is FactSource.OPERATOR and item.state is FactState.VALID
            ),
            None,
        )
        if operator is not None:
            resolved[key] = ResolvedFact(
                key=key,
                value=operator.value,
                state=FactState.VALID,
                selected_source=operator.source,
                selected_source_ref=operator.source_ref,
                observations=ordered,
            )
            continue

        explicit_invalid = next(
            (
                item
                for item in live
                if item.source is FactSource.STRUCTURED_FIELD
                and item.state is FactState.INVALID
            ),
            None,
        )
        if explicit_invalid is not None:
            resolved[key] = ResolvedFact(
                key=key,
                value=explicit_invalid.value,
                state=FactState.INVALID,
                selected_source=explicit_invalid.source,
                selected_source_ref=explicit_invalid.source_ref,
                observations=ordered,
                conflict_reason="explicit_structured_value_invalid",
            )
            continue

        valid = [item for item in live if item.state is FactState.VALID]
        best_priority = min(
            (SOURCE_PRIORITY[item.source] for item in valid), default=None
        )
        authoritative = [
            item
            for item in valid
            if SOURCE_PRIORITY[item.source] == best_priority
        ]
        values = {_identity(item.value) for item in authoritative}
        if len(values) > 1:
            resolved[key] = ResolvedFact(
                key=key,
                state=FactState.CONFLICTING,
                observations=ordered,
                conflict_reason="different_valid_values",
            )
            continue
        if authoritative:
            selected = authoritative[0]
            resolved[key] = ResolvedFact(
                key=key,
                value=selected.value,
                state=FactState.VALID,
                selected_source=selected.source,
                selected_source_ref=selected.source_ref,
                observations=ordered,
            )
            continue

        selected = live[0]
        resolved[key] = ResolvedFact(
            key=key,
            value=selected.value,
            state=selected.state,
            selected_source=selected.source,
            selected_source_ref=selected.source_ref,
            observations=ordered,
        )

    return FactBag(revision=revision, facts=resolved)
