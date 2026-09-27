"""Evidence-Based Routing Cascade domain package.

Exports canonical immutable routing contracts, verdicts, and the safe
TicketSnapshotFactory.
"""

from core.routing.cascade import ROUTER_VERSION, RoutingCascade
from core.routing.contracts import (
    CandidateVerification,
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    FeedbackVerdict,
    RoutingDecision,
    RoutingEvidence,
    RoutingState,
    ScenarioCandidate,
    SnapshotAttachment,
    SnapshotComment,
    TicketSnapshot,
    VerificationVerdict,
    VerifierTrace,
)
from core.routing.decision_policy import DecisionPolicy, GreyZonePolicy
from core.routing.exceptions import RoutingError, RoutingPersistenceError
from core.routing.persistence import RoutingDecisionRepository, RoutingDecisionService
from core.routing.readiness import FactReadinessPolicy
from core.routing.snapshot import TicketSnapshotFactory

__all__ = [
    "CandidateVerification",
    "DecisionPolicy",
    "EvidencePolarity",
    "EvidenceSource",
    "EvidenceStrength",
    "FactReadinessPolicy",
    "FeedbackVerdict",
    "GreyZonePolicy",
    "ROUTER_VERSION",
    "RoutingCascade",
    "RoutingDecision",
    "RoutingDecisionRepository",
    "RoutingDecisionService",
    "RoutingError",
    "RoutingEvidence",
    "RoutingPersistenceError",
    "RoutingState",
    "ScenarioCandidate",
    "SnapshotAttachment",
    "SnapshotComment",
    "TicketSnapshot",
    "TicketSnapshotFactory",
    "VerificationVerdict",
    "VerifierTrace",
]
