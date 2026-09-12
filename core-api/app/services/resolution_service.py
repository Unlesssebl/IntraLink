"""Strict resolution-policy and response-template resolver."""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined, meta
from jinja2.sandbox import ImmutableSandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import ResolutionPolicy, ResponseTemplate


import re

MAX_RENDERED_LENGTH = 8_000
HTML_TAG_RE = re.compile(r"<[^>]+>")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PLACEHOLDER_RE = re.compile(r"(?:\b(?:unknown|null|undefined|n/?a)\b|\{\{[^}]+\}\})", re.IGNORECASE)

_environment = ImmutableSandboxedEnvironment(
    undefined=StrictUndefined,
    autoescape=False,
    enable_async=False,
)
_environment.globals.clear()
_environment.filters.clear()


class ResolutionUnavailable(ValueError):
    pass


def template_variables(template_text: str) -> list[str]:
    ast = _environment.parse(template_text)
    return sorted(meta.find_undeclared_variables(ast))


def _sanitize_string_value(val: str, max_len: int = 1000) -> str:
    cleaned = HTML_TAG_RE.sub("", str(val))
    cleaned = CONTROL_CHARS_RE.sub("", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


def render_response_template(
    template: ResponseTemplate,
    context: dict[str, Any],
) -> str:
    declared = set(
        template.required_variables or template_variables(template.template_text)
    )
    # Поддержка обратной совместимости алиасов для целевого сервиса
    augmented_context = dict(context)
    if "target_service" in declared and "target_service" not in augmented_context and "target_service_name" in augmented_context:
        augmented_context["target_service"] = augmented_context["target_service_name"]
    if "target_service_name" in declared and "target_service_name" not in augmented_context and "target_service" in augmented_context:
        augmented_context["target_service_name"] = augmented_context["target_service"]

    missing = sorted(
        key for key in declared
        if augmented_context.get(key) in (None, "")
        or (isinstance(augmented_context.get(key), str) and not augmented_context[key].strip())
    )
    unexpected = sorted(set(augmented_context) - declared) if declared else sorted(augmented_context)
    if missing:
        raise ResolutionUnavailable(
            f"required_template_variables_missing:{template.key}:{','.join(missing)}"
        )
    # Templates receive only their declared allowlist. Extra rule/LLM data never
    # becomes an implicit capability inside an administrator-authored template.
    safe_context = {}
    for key in declared:
        if key in augmented_context:
            val = augmented_context[key]
            safe_context[key] = _sanitize_string_value(val) if isinstance(val, str) else val

    try:
        rendered = (
            _environment.from_string(template.template_text)
            .render(safe_context)
            .strip()
        )
    except Exception as exc:
        raise ResolutionUnavailable(
            f"required_template_invalid:{template.key}"
        ) from exc
    if not rendered:
        raise ResolutionUnavailable(f"required_template_empty:{template.key}")
    if len(rendered) > MAX_RENDERED_LENGTH:
        raise ResolutionUnavailable(f"required_template_too_large:{template.key}")
    if PLACEHOLDER_RE.search(rendered):
        raise ResolutionUnavailable(f"required_template_unresolved_placeholder:{template.key}")
    # Retain this local for diagnostics without including any values.
    _ = unexpected
    return rendered


async def resolve_outcome(
    session: AsyncSession,
    outcome_key: str,
    context: dict[str, Any],
    *,
    expected_kind: str | None = None,
    expected_action: str | None = None,
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(ResolutionPolicy, ResponseTemplate)
            .join(
                ResponseTemplate,
                ResolutionPolicy.template_id == ResponseTemplate.id,
                isouter=True,
            )
            .where(
                ResolutionPolicy.outcome_key == outcome_key,
                ResolutionPolicy.is_active.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        raise ResolutionUnavailable(
            f"required_resolution_policy_unavailable:{outcome_key}"
        )
    policy, template = row
    if expected_kind is not None and policy.outcome_kind != expected_kind:
        raise ResolutionUnavailable(
            f"required_resolution_policy_kind_mismatch:{outcome_key}"
        )
    if expected_action is not None and policy.action_id != expected_action:
        raise ResolutionUnavailable(
            f"required_resolution_policy_action_mismatch:{outcome_key}"
        )
    if template is None or not template.is_active:
        raise ResolutionUnavailable(f"required_template_unavailable:{outcome_key}")
    return {
        "outcome_key": policy.outcome_key,
        "outcome_kind": policy.outcome_kind,
        "policy_version": policy.version,
        "template_key": template.key,
        "template_version": template.version,
        "status_id": policy.target_status_id,
        "status_name": policy.status_name,
        "expenses": policy.expenses,
        "action_id": policy.action_id,
        "risk_level": policy.risk_level,
        "requires_approval": policy.requires_approval,
        "comment": render_response_template(template, context),
    }
