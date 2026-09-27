"""Transport abstraction and LiteLLM OpenAI adapter for LLM Grey Zone Verifier.

Enforces strict json_object response format, zero temperature, explicit timeouts,
and zero logging of ticket texts, messages, or raw response bodies.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

logger = logging.getLogger("core.routing.verifier.transport")


class VerifierTransportError(Exception):
    """Base exception for verifier transport failures."""


class VerifierTransportTimeoutError(VerifierTransportError):
    """Raised when LLM call exceeds explicit timeout."""


class VerifierTransportUnavailableError(VerifierTransportError):
    """Raised when LLM gateway is down or unreachable."""


@runtime_checkable
class VerifierTransport(Protocol):
    """Protocol for LLM Verifier transport implementations."""

    async def complete_json(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        payload: Dict[str, Any],
        timeout_seconds: float,
    ) -> str:
        """Execute structured JSON completion without logging payload or response body."""
        ...


class LiteLLMVerifierTransport:
    """Production AsyncOpenAI transport connecting to LiteLLM AI Gateway."""

    def __init__(
        self,
        ai_client: Optional[AsyncOpenAI] = None,
        base_url: str = "http://localhost:4000/v1",
        api_key: str = "sk-intralink-dev",
    ) -> None:
        if ai_client is not None:
            self._client = ai_client
        else:
            self._client = AsyncOpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=15.0,
                max_retries=1,
            )

    async def complete_json(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        payload: Dict[str, Any],
        timeout_seconds: float,
    ) -> str:
        """Call LiteLLM proxy requesting strict json_object output."""
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": serialized_payload},
        ]

        logger.debug(
            "LiteLLMVerifierTransport: invoking model '%s' (timeout=%.1fs)",
            model_alias,
            timeout_seconds,
        )

        try:
            response = await self._client.chat.completions.create(
                model=model_alias,
                messages=messages,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=1024,
                timeout=timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError, APITimeoutError) as exc:
            logger.warning(
                "LiteLLMVerifierTransport: timeout calling model '%s' after %.1fs",
                model_alias,
                timeout_seconds,
            )
            raise VerifierTransportTimeoutError("LLM call timed out") from exc
        except (APIConnectionError, InternalServerError, RateLimitError, ConnectionError) as exc:
            logger.warning(
                "LiteLLMVerifierTransport: connection/availability failure for model '%s': %s",
                model_alias,
                type(exc).__name__,
            )
            raise VerifierTransportUnavailableError("LLM gateway unavailable") from exc
        except Exception as exc:
            logger.warning(
                "LiteLLMVerifierTransport: unexpected error calling model '%s': %s",
                model_alias,
                type(exc).__name__,
            )
            raise VerifierTransportError("LLM transport failure") from exc

        choice = response.choices[0] if response.choices else None
        if not choice or not choice.message or choice.message.content is None:
            raise VerifierTransportError("Empty LLM completion response")

        return choice.message.content
