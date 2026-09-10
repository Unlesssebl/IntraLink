"""Tests for TruthfulnessGuard: structural integrity and lexical claims validation."""

import pytest

from shared.domain import (
    ExecutionPlan,
    PlanStep,
    StepKind,
    StepStatus,
)
from app.services.truthfulness_guard import (
    TruthfulnessGuard,
    TruthfulnessViolation,
)


def test_truthfulness_structural_blocks_status_29_when_step_not_completed():
    """Uncompleted required_for_resolution steps block status 29."""
    plan = ExecutionPlan(
        schema_version=2,
        scenario_key="install_printer",
        scenario_version=2,
        fact_revision=1,
        steps=[
            PlanStep(
                id="check_params",
                title="Параметры",
                kind=StepKind.CHECK,
                status=StepStatus.COMPLETED,
                evidence_refs=["fact:pc_name:pc1"],
            ),
            PlanStep(
                id="install_printer",
                title="Установка",
                kind=StepKind.ACTION,
                status=StepStatus.RUNNING,
                required_for_resolution=True,
            ),
        ],
    )
    violations = TruthfulnessGuard.verify_plan_structural_integrity(plan, target_status_id=29)
    assert len(violations) > 0
    assert any("step:install_printer:running" in v for v in violations)

    # But status 27 is allowed
    violations_27 = TruthfulnessGuard.verify_plan_structural_integrity(plan, target_status_id=27)
    assert len(violations_27) == 0


def test_truthfulness_lexical_unsubstantiated_completion():
    """Claiming completion in planned phase is blocked."""
    violations = TruthfulnessGuard.verify_lexical_truthfulness(
        "Принтер успешно установлен на рабочую станцию.",
        phase="planned",
        evidence_refs=[],
    )
    assert len(violations) > 0
    assert any("unverified_claim_completion" in v for v in violations)


def test_truthfulness_lexical_user_instruction_is_allowed():
    """Instructions to user ('Убедитесь...', 'Проверьте...') are not false claims."""
    violations = TruthfulnessGuard.verify_lexical_truthfulness(
        "Пожалуйста, убедитесь, что кабель питания принтера подключен к розетке.",
        phase="planned",
        evidence_refs=[],
    )
    assert len(violations) == 0


def test_truthfulness_lexical_execution_claim():
    """'Приступаю к проверке' without dispatched/running evidence is blocked."""
    violations = TruthfulnessGuard.verify_lexical_truthfulness(
        "Приступаю к проверке сетевого порта принтера.",
        phase="planned",
        evidence_refs=[],
    )
    assert len(violations) > 0
    assert any("unverified_claim_execution" in v for v in violations)

    # Allowed when running
    violations_running = TruthfulnessGuard.verify_lexical_truthfulness(
        "Приступаю к проверке сетевого порта принтера.",
        phase="running",
        evidence_refs=["worker:command:1:started"],
    )
    assert len(violations_running) == 0


def test_truthfulness_lexical_dispatch_claim():
    """'Специалист направлен' without assignment event is blocked."""
    violations = TruthfulnessGuard.verify_lexical_truthfulness(
        "Специалист направлен на место для устранения неисправности.",
        phase="ready",
        evidence_refs=[],
    )
    assert len(violations) > 0
    assert any("unverified_claim_dispatch" in v for v in violations)


def test_validate_apply_triage_private_cannot_close():
    """Private comment cannot resolve ticket with status 29."""
    with pytest.raises(TruthfulnessViolation, match="Внутренний комментарий не может закрывать заявку"):
        TruthfulnessGuard.validate_apply_triage_payload(
            status_id=29,
            comment="Внутренняя сводка",
            is_private=True,
        )


def test_validate_apply_triage_blocks_unverified_close():
    """Attempting to close ticket 29 with incomplete plan raises TruthfulnessViolation."""
    plan = ExecutionPlan(
        schema_version=2,
        scenario_key="pc_performance",
        scenario_version=1,
        fact_revision=1,
        steps=[
            PlanStep(
                id="analyze_workload",
                title="Анализ",
                kind=StepKind.MANUAL,
                status=StepStatus.READY,
                required_for_resolution=True,
            )
        ],
    )
    with pytest.raises(TruthfulnessViolation, match="Перевод в статус 29 заблокирован"):
        TruthfulnessGuard.validate_apply_triage_payload(
            status_id=29,
            comment="Все готово",
            is_private=False,
            execution_plan=plan,
        )
