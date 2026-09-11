"""Unit tests for capacity-aware context budgeting and safe facts isolation in ResponseContextBuilder."""

import json
import pytest

from app.config import settings
from app.services.response_context import ResponseContextBuilder
from shared.domain import ExecutionPlan, PlanStep, StepKind, StepStatus


def test_capacity_budgeting_local_vs_cloud_profiles():
    """Local profile must truncate RAG matches to AI_OLLAMA_MAX_RAG_MATCHES (1), cloud to AI_CLOUD_MAX_RAG_MATCHES (3)."""
    kb_matches = [
        {"task_id": 101, "problem": "Проблема 1", "solution": "Решение 1", "similarity_pct": 95},
        {"task_id": 102, "problem": "Проблема 2", "solution": "Решение 2", "similarity_pct": 88},
        {"task_id": 103, "problem": "Проблема 3", "solution": "Решение 3", "similarity_pct": 75},
        {"task_id": 104, "problem": "Проблема 4", "solution": "Решение 4", "similarity_pct": 60},
    ]

    facts_summary = {
        "pc_name": {"state": "valid", "value": "PC-FIN-01"},
        "secret_token": {"state": "valid", "value": "<redacted>"},
        "missing_fact": {"state": "missing", "value": None},
    }

    context = ResponseContextBuilder.build_context(
        decision_id="dec-1001",
        decision_version=1,
        scenario_key="hardware_repair",
        scenario_version=1,
        outcome_kind="repair_needed",
        policy_comment="Принесите ПК в кабинет 112 на диагностику.",
        facts_summary=facts_summary,
        evidence_refs=["diag:host:offline"],
        plan=None,
        kb_matches=kb_matches,
    )

    variants = ResponseContextBuilder.build_prompt_variants(context, tone="concise")
    assert len(variants) == 2

    local_var = next(v for v in variants if v.profile == "local")
    cloud_var = next(v for v in variants if v.profile == "cloud")

    # 1. Local profile must contain at most settings.AI_OLLAMA_MAX_RAG_MATCHES (1)
    assert len(local_var.rag_refs) == settings.AI_OLLAMA_MAX_RAG_MATCHES
    assert local_var.rag_refs == ["ticket:101"]
    local_data = json.loads(local_var.prompt)
    assert len(local_data["precedents"]) == 1
    assert local_data["precedents"][0]["task_id"] == 101
    assert "лаконичный ответ" in local_data["tone_instruction"]

    # 2. Cloud profile must contain up to settings.AI_CLOUD_MAX_RAG_MATCHES (3)
    assert len(cloud_var.rag_refs) == settings.AI_CLOUD_MAX_RAG_MATCHES
    assert cloud_var.rag_refs == ["ticket:101", "ticket:102", "ticket:103"]
    cloud_data = json.loads(cloud_var.prompt)
    assert len(cloud_data["precedents"]) == 3
    assert [p["task_id"] for p in cloud_data["precedents"]] == [101, 102, 103]


def test_safe_facts_isolation():
    """Redacted or invalid facts must be excluded from confirmed_facts."""
    facts_summary = {
        "pc_name": {"state": "valid", "value": "PC-ACC-01"},
        "auth_token": {"state": "valid", "value": "<redacted>"},
        "ip_address": {"state": "invalid", "value": "10.244.100.22"},
        "empty_fact": {"state": "valid", "value": ""},
    }

    context = ResponseContextBuilder.build_context(
        decision_id="dec-1002",
        decision_version=1,
        scenario_key="test_scenario",
        scenario_version=1,
        outcome_kind="resolved",
        policy_comment="Все проверено.",
        facts_summary=facts_summary,
        evidence_refs=[],
    )

    assert "pc_name" in context.confirmed_facts
    assert context.confirmed_facts["pc_name"] == "PC-ACC-01"
    assert "auth_token" not in context.confirmed_facts
    assert "ip_address" not in context.confirmed_facts
    assert "empty_fact" not in context.confirmed_facts


def test_public_plan_steps_filtering():
    """Engineer/windows internal steps must not leak to applicant as public steps."""
    plan = ExecutionPlan(
        scenario_key="test_scenario",
        scenario_version=1,
        phase="running",
        steps=[
            PlanStep(
                id="s1",
                title="SMB check port 445",
                kind=StepKind.CHECK,
                status=StepStatus.COMPLETED,
                evidence_refs=["diag:smb:fail"],
                executor="windows",  # Должен быть отфильтрован
            ),
            PlanStep(
                id="s2",
                title="Перезагрузка ПК заявителем",
                kind=StepKind.CHECK,
                status=StepStatus.NOT_STARTED,
                evidence_refs=[],
                executor="backend",  # Должен остаться в public_steps
            ),
        ],
    )

    context = ResponseContextBuilder.build_context(
        decision_id="dec-1003",
        decision_version=1,
        scenario_key="test_scenario",
        scenario_version=1,
        outcome_kind="in_progress",
        policy_comment="Пожалуйста, выполните шаг.",
        facts_summary={},
        evidence_refs=[],
        plan=plan,
    )

    assert len(context.public_plan_steps) == 1
    assert context.public_plan_steps[0]["title"] == "Перезагрузка ПК заявителем"
