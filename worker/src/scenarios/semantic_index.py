"""Backwards-compatibility re-exports for SemanticPrototypeIndex (canonical: core.scenarios.semantic_index)."""

from core.scenarios.semantic_index import (
    SCENARIO_PROTOTYPES,
    SemanticPrototypeIndex,
    _cosine_similarity,
)

__all__ = [
    "SCENARIO_PROTOTYPES",
    "SemanticPrototypeIndex",
    "_cosine_similarity",
]
