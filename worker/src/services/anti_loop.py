"""Anti-Loop Guard Service protecting autopilot and triage from mail robot loops.

Invariants:
1. Detects automated out-of-office / bounce mail replies (Russian & English).
2. Prevents infinite ping-pong by filtering bot's own events (author_id == bot_user_id).
3. Enforces dialogue limit: no more than 2 clarification rounds before escalating to human.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel

logger = logging.getLogger("worker.services.anti_loop")

# Mail robot & auto-reply regex patterns
AUTO_REPLY_PATTERNS = [
    # English patterns
    re.compile(r"\b(?:out\s+of\s+office|auto[- ]?reply|automatic\s+reply)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:undelivered\s+mail|delivery\s+status\s+notification|failure\s+notice|mail\s+delivery\s+subsystem)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:returned\s+to\s+sender|vacation\s+responder|i\s+am\s+out\s+of\s+the\s+office|i\s+will\s+be\s+away)\b", re.IGNORECASE),
    # Russian patterns
    re.compile(
        r"\b(?:автоматический\s+ответ|автоответ|в\s+отпуске|нахожусь\s+в\s+отпуске)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:отсутствую\s+на\s+рабочем\s+месте|до\s+моего\s+возвращения|меня\s+нет\s+на\s+месте|буду\s+отсутствовать)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:сообщение\s+не\s+доставлено|недоставленное\s+сообщение|уведомление\s+о\s+недоставке|отчет\s+о\s+недоставке)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:автоматическое\s+уведомление|письмо\s+сгенерировано\s+автоматически)\b",
        re.IGNORECASE,
    ),
]


class AntiLoopDecision(BaseModel):
    """Decision outcome of anti-loop validation."""

    can_proceed: bool
    reason: str
    is_auto_reply: bool = False
    is_bot: bool = False
    rounds_count: int = 0


class AntiLoopGuard:
    """Anti-loop protection and dialogue turn limiter."""

    def __init__(self, max_clarification_rounds: int = 2) -> None:
        self.max_clarification_rounds = max_clarification_rounds

    def is_auto_reply(self, text: Optional[str], subject: Optional[str] = None) -> bool:
        """Detect automated email robot responses, vacation notices or delivery failures."""
        combined = f"{subject or ''} {text or ''}".strip()
        if not combined:
            return False

        for pattern in AUTO_REPLY_PATTERNS:
            if pattern.search(combined):
                logger.debug("Auto-reply detected matching pattern '%s'", pattern.pattern)
                return True

        return False

    def is_bot_author(self, author_id: Optional[Union[int, str]], bot_user_id: Optional[Union[int, str]]) -> bool:
        """Verify whether the author of the event is the service bot itself."""
        if author_id is None or bot_user_id is None:
            return False
        try:
            return int(author_id) == int(bot_user_id)
        except (ValueError, TypeError):
            return str(author_id).strip() == str(bot_user_id).strip()

    def count_clarification_rounds(
        self,
        events_or_comments: List[Union[Dict[str, Any], Any]],
        bot_user_id: Optional[Union[int, str]] = None,
    ) -> int:
        """Count how many clarification questions/rounds the bot has initiated in this ticket.

        A round is counted when the bot posts a question or sets status to 6 ('Приостановлена').
        """
        rounds = 0
        for item in events_or_comments:
            # Check comment or lifetime event
            if isinstance(item, dict):
                user_id = item.get("UserId") or item.get("EditorId") or item.get("AuthorId") or item.get("author_id")
                text = str(item.get("Comment") or item.get("Text") or item.get("text") or "")
                new_status = item.get("NewStatusId") or item.get("StatusId")
                is_private = item.get("IsPrivate") or item.get("IsPrivateComment") or False
            else:
                user_id = getattr(item, "user_id", None) or getattr(item, "author_id", None) or getattr(item, "editor_id", None)
                text = str(getattr(item, "comment", "") or getattr(item, "text", "") or "")
                new_status = getattr(item, "status_id", None) or getattr(item, "new_status_id", None)
                is_private = getattr(item, "is_private", False)

            # Ignore private internal audit notes from bot
            if is_private:
                continue

            # If message was authored by bot or transitioned to status 6 (Suspended)
            is_from_bot = self.is_bot_author(user_id, bot_user_id) if bot_user_id else False
            if is_from_bot:
                # Bot asked question or paused ticket
                if "?" in text or new_status in (6, "6"):
                    rounds += 1
            elif new_status in (6, "6"):
                rounds += 1

        return rounds

    def check_clarification_limit(
        self,
        events_or_comments: List[Union[Dict[str, Any], Any]],
        bot_user_id: Optional[Union[int, str]] = None,
    ) -> bool:
        """Return True if ticket has reached or exceeded max clarification rounds (escalate to human)."""
        rounds = self.count_clarification_rounds(events_or_comments, bot_user_id=bot_user_id)
        return rounds >= self.max_clarification_rounds

    def evaluate(
        self,
        text: Optional[str] = None,
        subject: Optional[str] = None,
        author_id: Optional[Union[int, str]] = None,
        bot_user_id: Optional[Union[int, str]] = None,
        events_or_comments: Optional[List[Union[Dict[str, Any], Any]]] = None,
    ) -> AntiLoopDecision:
        """Run all anti-loop checks and return a typed decision."""
        if self.is_bot_author(author_id, bot_user_id):
            return AntiLoopDecision(
                can_proceed=False,
                reason="bot_self_message",
                is_bot=True,
            )

        if self.is_auto_reply(text, subject):
            return AntiLoopDecision(
                can_proceed=False,
                reason="auto_reply_detected",
                is_auto_reply=True,
            )

        rounds = 0
        if events_or_comments:
            rounds = self.count_clarification_rounds(events_or_comments, bot_user_id=bot_user_id)
            if rounds >= self.max_clarification_rounds:
                return AntiLoopDecision(
                    can_proceed=False,
                    reason=f"clarification_rounds_exceeded (count={rounds}, max={self.max_clarification_rounds})",
                    rounds_count=rounds,
                )

        return AntiLoopDecision(
            can_proceed=True,
            reason="passed",
            rounds_count=rounds,
        )
