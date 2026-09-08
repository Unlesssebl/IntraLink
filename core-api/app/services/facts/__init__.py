"""Typed fact collection and provenance merge for ticket scenarios."""

from app.services.facts.collectors import collect_structured, collect_ticket_observations
from app.services.facts.merger import merge_observations
from app.services.facts.registry import FactRegistry, FactSpec, get_fact_registry
from app.services.facts.store import TicketFactStore

__all__ = [
    "FactRegistry",
    "FactSpec",
    "TicketFactStore",
    "collect_ticket_observations",
    "collect_structured",
    "get_fact_registry",
    "merge_observations",
]
