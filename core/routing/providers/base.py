"""CandidateProvider protocol for Evidence-Based Routing Cascade."""

from typing import List, Protocol, Sequence, runtime_checkable

from core.routing.contracts import RoutingEvidence, TicketSnapshot
from core.routing.profiles import ScenarioRoutingProfile


@runtime_checkable
class CandidateProvider(Protocol):
    """Protocol for atomic evidence collecting providers."""

    name: str

    async def collect(
        self,
        snapshot: TicketSnapshot,
        profiles: Sequence[ScenarioRoutingProfile],
    ) -> List[RoutingEvidence]:
        """Collect atomic evidence matching the given ticket snapshot against scenario profiles.

        Returns only atomic RoutingEvidence items. Does not decide winner or construct RoutingDecision.
        """
        ...
