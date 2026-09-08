"""Strict resolution-policy and response-template resolver."""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined, meta
from jinja2.sandbox import ImmutableSandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import ResolutionPolicy, ResponseTemplate


MAX_RENDERED_LENGTH = 8_000
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


def render_response_template(
    template: ResponseTemplate,
    context: dict[str, Any],
) -> str:
    declared = set(
        template.required_variables or template_variables(template.template_text)
    )
    missing = sorted(key for key in declared if context.get(key) in (None, ""))
    unexpected = sorted(set(context) - declared) if declared else sorted(context)
    if missing:
        raise ResolutionUnavailable(
            f"required_template_variables_missing:{template.key}:{','.join(missing)}"
        )
    # Templates receive only their declared allowlist. Extra rule/LLM data never
    # becomes an implicit capability inside an administrator-authored template.
    safe_context = {key: context[key] for key in declared if key in context}
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
