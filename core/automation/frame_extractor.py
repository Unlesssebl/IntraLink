"""Safe deterministic and optional LLM extraction of CaseFrame."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Protocol

from core.automation.case_profiles import CaseProfileRegistry
from core.automation.contracts import (
    AssertionKind,
    CaseAssertion,
    CaseFrame,
    ExtractionMethod,
    TicketSnapshot,
)
from core.automation.llm_transport import (
    LLMTransportError,
    LLMTransportTimeoutError,
    LLMTransportUnavailableError,
)
from core.rag.sanitizer import PIISanitizer


class CaseFrameTransport(Protocol):
    async def complete_json(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> str: ...


_SECRET_RE = re.compile(r"(?i)\b(password|passwd|пароль|token|secret)\b\s*[:=]?\s*\S+")
_ALLOWED_KINDS = {kind.value: kind for kind in AssertionKind}


class CaseFrameExtractor:
    version = "case-frame-v1"
    prompt_version = "case-frame-extractor-v1"

    def __init__(
        self,
        transport: CaseFrameTransport | None = None,
        model_alias: str = "helpdesk-fast",
        profiles: CaseProfileRegistry | None = None,
    ) -> None:
        self.transport = transport
        self.model_alias = model_alias
        self._sanitizer = PIISanitizer(max_length=6000)
        profile_registry = profiles or CaseProfileRegistry()
        self._allowed_assertion_keys = frozenset(
            key for profile in profile_registry.list_all() for key in profile.assertion_keys
        )

    async def extract(self, snapshot: TicketSnapshot, *, include_llm: bool = True) -> CaseFrame:
        assertions = self._deterministic_assertions(snapshot)
        degraded: dict[str, str] = {}
        if include_llm and self.transport is not None:
            try:
                assertions.extend(await self._extract_llm(snapshot))
            except LLMTransportTimeoutError:
                degraded["case_frame_extractor"] = "extractor_timeout"
            except LLMTransportUnavailableError:
                degraded["case_frame_extractor"] = "extractor_unavailable"
            except json.JSONDecodeError:
                degraded["case_frame_extractor"] = "extractor_invalid_json"
            except (LLMTransportError, ValueError):
                degraded["case_frame_extractor"] = "extractor_invalid_response"
            except Exception:
                degraded["case_frame_extractor"] = "extractor_unexpected_error"
        return CaseFrame(
            task_id=snapshot.task_id,
            snapshot_hash=snapshot.snapshot_hash,
            frame_version=self.version,
            assertions=self._deduplicate(assertions),
            entities={key: value for key, value in snapshot.entities.items() if not self._is_secret_key(key)},
            degraded_components=degraded,
            llm_attempted=include_llm and self.transport is not None,
        )

    def _deterministic_assertions(self, snapshot: TicketSnapshot) -> list[CaseAssertion]:
        assertions: list[CaseAssertion] = []
        for key, value in snapshot.entities.items():
            if not value or self._is_secret_key(key):
                continue
            assertions.append(
                CaseAssertion(
                    id=self._assertion_id(snapshot.snapshot_hash, "entity", key, value, f"entity:{key}"),
                    kind=AssertionKind.entity,
                    key=key,
                    value=value,
                    source_ref=f"entity:{key}",
                    extraction_method=ExtractionMethod.deterministic,
                )
            )

        targets = [("title", snapshot.title), ("description", snapshot.description)]
        targets.extend((f"comment:{item.id}", item.text) for item in snapshot.public_comments if not item.is_private)
        patterns = {
            "stuck_print_queue": (AssertionKind.symptom, ("зависли документы", "очередь печати", "зависла печать")),
            "wrong_default_printer": (AssertionKind.symptom, ("не тот принтер", "по умолчанию", "основной принтер")),
            "connect_printer": (AssertionKind.intent, ("подключить принтер", "установить принтер", "добавить принтер")),
            "create_user": (
                AssertionKind.intent,
                ("создать пользователя", "создать учетную запись", "новый сотрудник"),
            ),
            "revoke_access": (AssertionKind.intent, ("заблокировать учетную запись", "закрыть доступ", "увольнение")),
            "install_software": (
                AssertionKind.intent,
                ("установить программу", "установить по", "настроить программу", "установка программы"),
            ),
        }
        for source_ref, text in targets:
            lowered = (text or "").casefold().replace("ё", "е")
            for key, (kind, phrases) in patterns.items():
                for phrase in phrases:
                    start = lowered.find(phrase)
                    if start < 0:
                        continue
                    span = (text or "")[start : start + len(phrase)]
                    assertions.append(
                        CaseAssertion(
                            id=self._assertion_id(snapshot.snapshot_hash, kind.value, key, "true", source_ref),
                            kind=kind,
                            key=key,
                            value="true",
                            source_ref=source_ref,
                            text_span=span,
                            extraction_method=ExtractionMethod.deterministic,
                        )
                    )
                    break
        return assertions

    async def _extract_llm(self, snapshot: TicketSnapshot) -> list[CaseAssertion]:
        sources = self._sanitized_sources(snapshot)
        payload = {
            "sources": sources,
            "allowed_kinds": sorted(_ALLOWED_KINDS),
            "allowed_keys": sorted(self._allowed_assertion_keys),
        }
        raw = await self.transport.complete_json(
            model_alias=self.model_alias,
            system_prompt=(
                "Extract only literal case assertions using one of the supplied allowed_keys. "
                "Return JSON {assertions:[{kind,key,value,source_ref,text_span,is_negated}]}. "
                "text_span must be an exact substring of the referenced source. Do not classify or propose actions."
            ),
            payload=payload,
            timeout_seconds=30.0,
        )
        parsed = json.loads(raw)
        items = parsed.get("assertions")
        if not isinstance(items, list):
            raise ValueError("Invalid extractor response")
        assertions: list[CaseAssertion] = []
        for item in items:
            if not isinstance(item, dict) or set(item) - {
                "kind",
                "key",
                "value",
                "source_ref",
                "text_span",
                "is_negated",
            }:
                raise ValueError("Invalid extractor assertion schema")
            source_ref = str(item.get("source_ref", ""))
            span = str(item.get("text_span", ""))
            if source_ref not in sources or not span or span not in sources[source_ref]:
                raise ValueError("LLM assertion span is not grounded")
            kind_value = str(item.get("kind", ""))
            if kind_value not in _ALLOWED_KINDS:
                raise ValueError("Unknown assertion kind")
            key = str(item.get("key", ""))
            if key not in self._allowed_assertion_keys:
                raise ValueError("Unknown assertion key")
            value = str(item.get("value", ""))
            assertions.append(
                CaseAssertion(
                    id=self._assertion_id(snapshot.snapshot_hash, kind_value, key, value, source_ref),
                    kind=_ALLOWED_KINDS[kind_value],
                    key=key,
                    value=value,
                    source_ref=source_ref,
                    text_span=span,
                    extraction_method=ExtractionMethod.llm,
                    is_negated=bool(item.get("is_negated", False)),
                )
            )
        return assertions

    def _sanitized_sources(self, snapshot: TicketSnapshot) -> dict[str, str]:
        result = {
            "title": self._sanitize(snapshot.title),
            "description": self._sanitize(snapshot.description),
        }
        for item in snapshot.public_comments:
            if not item.is_private:
                result[f"comment:{item.id}"] = self._sanitize(item.text)
        return result

    def _sanitize(self, text: str) -> str:
        sanitized = self._sanitizer.sanitize(text or "").sanitized_text
        return _SECRET_RE.sub("[SECRET]", sanitized)

    @staticmethod
    def _assertion_id(snapshot_hash: str, kind: str, key: str, value: str, source_ref: str) -> str:
        raw = json.dumps([snapshot_hash, kind, key, value, source_ref], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _deduplicate(assertions: list[CaseAssertion]) -> list[CaseAssertion]:
        return sorted({item.id: item for item in assertions}.values(), key=lambda item: item.id)

    @staticmethod
    def _is_secret_key(key: str) -> bool:
        lowered = key.casefold()
        return lowered in {"1489", "field1489", "it_password"} or any(
            marker in lowered for marker in ("password", "passwd", "пароль", "token", "secret")
        )
