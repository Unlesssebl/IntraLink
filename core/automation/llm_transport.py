"""Strict JSON transport for the bounded automation LLM calls."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol, runtime_checkable

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError, RateLimitError

logger = logging.getLogger("core.automation.llm_transport")


class LLMTransportError(Exception):
    pass


class LLMTransportTimeoutError(LLMTransportError):
    pass


class LLMTransportUnavailableError(LLMTransportError):
    pass


@runtime_checkable
class LLMTransport(Protocol):
    async def complete_json(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> str: ...


class LiteLLMTransport:
    """OpenAI-compatible LiteLLM adapter that never logs request or response bodies."""

    def __init__(
        self,
        ai_client: AsyncOpenAI | None = None,
        base_url: str = "http://localhost:4000/v1",
        api_key: str = "sk-intralink-dev",
    ) -> None:
        self._client = ai_client or AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=15.0,
            # LiteLLM owns provider retries and fallbacks. A second retry layer
            # makes the caller deadline expire while the gateway is still working.
            max_retries=0,
        )

    async def complete_json(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
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
            logger.warning("LLM timeout for model '%s'", model_alias)
            raise LLMTransportTimeoutError("LLM call timed out") from exc
        except (APIConnectionError, InternalServerError, RateLimitError, ConnectionError) as exc:
            logger.warning("LLM unavailable for model '%s': %s", model_alias, type(exc).__name__)
            raise LLMTransportUnavailableError("LLM gateway unavailable") from exc
        except Exception as exc:
            logger.warning("LLM transport failure for model '%s': %s", model_alias, type(exc).__name__)
            raise LLMTransportError("LLM transport failure") from exc
        choice = response.choices[0] if response.choices else None
        if not choice or not choice.message or choice.message.content is None:
            raise LLMTransportError("Empty LLM completion response")
        return choice.message.content


# Names used by the extraction/verifier services remain explicit about their role.
VerifierTransport = LLMTransport
VerifierTransportError = LLMTransportError
VerifierTransportTimeoutError = LLMTransportTimeoutError
VerifierTransportUnavailableError = LLMTransportUnavailableError
LiteLLMVerifierTransport = LiteLLMTransport
