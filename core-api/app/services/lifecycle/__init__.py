"""
Пакет сервисов управления автономным жизненным циклом заявок (Lifecycle FSM).
"""

from app.services.lifecycle.models import (
    IntentAnalysisResult,
    LifecycleStepResult,
    TicketLifecycleState,
    UserReplyIntent,
)
from app.services.lifecycle.intent_analyzer import IntentAnalyzer
from app.services.lifecycle.state_machine import LifecycleStateMachine
from app.services.lifecycle.orchestrator import (
    AutonomousTicketOrchestrator,
    get_ticket_orchestrator,
)

__all__ = [
    "IntentAnalysisResult",
    "LifecycleStepResult",
    "TicketLifecycleState",
    "UserReplyIntent",
    "IntentAnalyzer",
    "LifecycleStateMachine",
    "AutonomousTicketOrchestrator",
    "get_ticket_orchestrator",
]
