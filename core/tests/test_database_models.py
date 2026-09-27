"""Test SQLAlchemy 2.0 database models and schema definitions."""

import uuid

from core.database.models import (
    CommandRecord,
    RoutingDecisionRecord,
    RoutingFeedbackRecord,
    TaskKnowledgeBase,
    TriageAudit,
    User,
)


def test_task_knowledge_base_model_instantiation():
    record = TaskKnowledgeBase(
        task_id=12345,
        original_name="Сбой печати HP LaserJet",
        problem="Принтер выдает ошибку замятия",
        solution="Перезапущен Spooler, извлечен лист бумаги",
        service_id=233,
        service_name="Установка и настройка оборудования",
        service_path="ИТ / Оборудование / Принтеры",
        status_name="Закрыта",
        classification_data={"root_cause": "hardware"},
        embedding=[0.1] * 1024,
        quality_score=0.95,
    )
    assert record.task_id == 12345
    assert record.quality_score == 0.95
    assert len(record.embedding) == 1024
    assert record.is_blacklisted is False


def test_user_model_instantiation():
    user = User(
        username="belikov.a",
        is_user_id=42,
        tg_user_id=123456789,
        is_admin=True,
    )
    assert user.username == "belikov.a"
    assert user.is_admin is True
    assert user.is_active is True


def test_command_record_model_instantiation():
    cmd_id = uuid.uuid4()
    cmd = CommandRecord(
        id=cmd_id,
        idempotency_key="cmd-idem-001",
        action="install_printer",
        executor="worker",
        target_json={"host": "PC-TEST-01"},
        params_json={"driver": "HP LaserJet P2035"},
        status="pending",
        initiator="belikov.a",
        task_id=9876,
    )
    assert cmd.id == cmd_id
    assert cmd.action == "install_printer"
    assert cmd.executor == "worker"
    assert cmd.status == "pending"


def test_triage_audit_model_instantiation():
    audit_id = uuid.uuid4()
    audit = TriageAudit(
        id=audit_id,
        task_id=54321,
        action="auto_classify",
        model_used="helpdesk-fast",
        confidence=0.98,
        prompt_tokens=350,
        completion_tokens=42,
        context_snapshot={"title": "Ошибка Directum"},
        decision_json={"suggested_service_id": 234},
        applied=True,
        applied_by="system",
    )
    assert audit.id == audit_id
    assert audit.task_id == 54321
    assert audit.confidence == 0.98
    assert audit.applied is True


def test_routing_decision_record_model_instantiation():
    dec_id = uuid.uuid4()
    record = RoutingDecisionRecord(
        id=dec_id,
        task_id=777,
        snapshot_hash="f" * 64,
        router_version="2.0.0",
        prompt_version="verifier-v1",
        state="selected",
        selected_scenario="install_printer",
        selected_scenario_version="1.0.0",
        snapshot_json={"task_id": 777, "title": "Установка принтера"},
        candidates_json=[{"scenario_key": "install_printer"}],
        evidence_json=[{"id": "ev-1", "candidate_key": "install_printer"}],
        verifier_result_json=[{"scenario_key": "install_printer", "verdict": "supported"}],
        missing_facts_json=[],
        degradation_reason=None,
        decision_reason_codes_json=["direct_exact_service_id"],
        degraded_components_json={"semantic": "provider_timeout"},
        verifier_trace_json={"status": "success", "prompt_version": "verifier-v1"},
    )
    assert record.id == dec_id
    assert record.task_id == 777
    assert record.snapshot_hash == "f" * 64
    assert record.router_version == "2.0.0"
    assert record.prompt_version == "verifier-v1"
    assert record.state == "selected"
    assert record.selected_scenario == "install_printer"
    assert record.selected_scenario_version == "1.0.0"
    assert record.snapshot_json["task_id"] == 777
    assert len(record.candidates_json) == 1
    assert len(record.evidence_json) == 1
    assert len(record.verifier_result_json) == 1
    assert record.missing_facts_json == []
    assert record.decision_reason_codes_json == ["direct_exact_service_id"]
    assert record.degraded_components_json == {"semantic": "provider_timeout"}
    assert record.verifier_trace_json["status"] == "success"


def test_routing_feedback_record_model_instantiation():
    feedback_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    feedback = RoutingFeedbackRecord(
        id=feedback_id,
        decision_id=decision_id,
        task_id=777,
        operator_username="supervisor",
        verdict="corrected",
        corrected_scenario="grant_wlan",
        corrected_params={"user_login": "ivanov.i", "temp_password": "supersecretpassword"},
        reason_tag="misclassification",
        notes="Пользователь просил Wi-Fi, а не принтер",
    )
    assert feedback.id == feedback_id
    assert feedback.decision_id == decision_id
    assert feedback.task_id == 777
    assert feedback.operator_username == "supervisor"
    assert feedback.verdict == "corrected"
    assert feedback.corrected_scenario == "grant_wlan"
    # Secrets in corrected_params must be sanitized automatically
    assert feedback.corrected_params["user_login"] == "ivanov.i"
    assert feedback.corrected_params["temp_password"] == "***REDACTED***"
    assert feedback.reason_tag == "misclassification"
    assert feedback.notes == "Пользователь просил Wi-Fi, а не принтер"


def test_terminal_feedback_unique_index_includes_corrected_verdict():
    index = next(
        idx
        for idx in RoutingFeedbackRecord.__table__.indexes
        if idx.name == "uq_routing_feedback_terminal_plan"
    )
    sqlite_predicate = str(index.dialect_options["sqlite"]["where"])
    postgres_predicate = str(index.dialect_options["postgresql"]["where"])
    assert "corrected" in sqlite_predicate
    assert "corrected" in postgres_predicate
