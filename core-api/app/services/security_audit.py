"""Persistent, PII-free audit trail for AI and execution safety gates."""

import logging
from typing import Any

from app.database.db import AsyncSessionLocal, SecurityAuditLog

logger = logging.getLogger("core_api.security_audit")


async def record_security_event(
    event_type: str, outcome: str, details: dict[str, Any] | None = None
) -> None:
    """Best-effort audit write that cannot turn a safety denial into an outage.

    Callers must pass only operational metadata (circuit, action, reason code),
    never ticket text, identities, targets, credentials or PII.
    """
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                SecurityAuditLog(
                    event_type=event_type[:80],
                    outcome=outcome[:20],
                    details_json=details or {},
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Could not persist security audit event type=%s", event_type)
