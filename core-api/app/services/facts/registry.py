"""Registry of fact contracts used by scenario requirements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from shared.domain import FactSensitivity
from shared.normalizer import normalize_pc_name


Normalizer = Callable[[Any], Any]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_pc(value: Any) -> str:
    raw = _clean_text(value)
    return normalize_pc_name(raw) or raw.upper()


def _identity(value: Any) -> Any:
    return value


@dataclass(frozen=True, slots=True)
class FactSpec:
    key: str
    normalizer: Normalizer = _clean_text
    sensitivity: FactSensitivity = FactSensitivity.INTERNAL
    ttl_seconds: int | None = None
    clarification_key: str | None = None
    allow_llm: bool = True

    def normalize(self, value: Any) -> Any:
        return self.normalizer(value)


class FactRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, FactSpec] = {}

    def register(self, spec: FactSpec) -> None:
        if spec.key in self._specs:
            raise ValueError(f"duplicate_fact_spec:{spec.key}")
        self._specs[spec.key] = spec

    def get(self, key: str) -> FactSpec | None:
        return self._specs.get(key)

    def require(self, key: str) -> FactSpec:
        spec = self.get(key)
        if spec is None:
            raise KeyError(f"unknown_fact:{key}")
        return spec

    def all(self) -> tuple[FactSpec, ...]:
        return tuple(self._specs.values())


def _build_default_registry() -> FactRegistry:
    registry = FactRegistry()
    for key in ("task_id", "service_id"):
        registry.register(
            FactSpec(key=key, normalizer=lambda value: int(value), allow_llm=False)
        )
    for key in ("subject", "description", "service_name", "target_service"):
        registry.register(FactSpec(key=key, allow_llm=key != "service_name"))
    for key in ("surname", "name", "patronymic", "company", "department", "title", "phone"):
        registry.register(
            FactSpec(
                key=key,
                sensitivity=FactSensitivity.PERSONAL,
                clarification_key=f"clarify_{key}",
            )
        )
    registry.register(
        FactSpec(
            key="pc_name",
            normalizer=_normalize_pc,
            sensitivity=FactSensitivity.INTERNAL,
            ttl_seconds=3600,
            clarification_key="clarify_pc_name",
        )
    )
    for key in (
        "printer_address",
        "printer_name",
        "printer_connection_type",
        "file_path",
        "identity",
        "clarification_answer",
        "attachments_state",
    ):
        registry.register(
            FactSpec(
                key=key,
                sensitivity=(
                    FactSensitivity.PERSONAL if key == "identity" else FactSensitivity.INTERNAL
                ),
                clarification_key=f"clarify_{key}",
            )
        )
    registry.register(
        FactSpec(key="printer_targets", normalizer=_identity, allow_llm=False)
    )
    registry.register(FactSpec(key="issue_summary"))
    return registry


_REGISTRY = _build_default_registry()


def get_fact_registry() -> FactRegistry:
    return _REGISTRY
