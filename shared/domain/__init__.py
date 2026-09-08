"""Strict domain contracts shared by decisioning and execution services."""

from shared.domain.models import (
    ActionProposed,
    ClarificationRequired,
    CreateUserParameters,
    DecisionOutcome,
    DecisionOutcomeAdapter,
    Evidence,
    ExtractedPersonCandidate,
    ExtractedTicketFacts,
    ManualReviewRequired,
    NoMatch,
    PersonCandidate,
    ResolutionProposed,
    TicketFacts,
    ValidPerson,
    ValidationError,
)
from shared.domain.person import normalize_person_name, validate_person_candidate

__all__ = [
    "ActionProposed",
    "ClarificationRequired",
    "CreateUserParameters",
    "DecisionOutcome",
    "DecisionOutcomeAdapter",
    "Evidence",
    "ExtractedPersonCandidate",
    "ExtractedTicketFacts",
    "ManualReviewRequired",
    "NoMatch",
    "PersonCandidate",
    "ResolutionProposed",
    "TicketFacts",
    "ValidPerson",
    "ValidationError",
    "normalize_person_name",
    "validate_person_candidate",
]
