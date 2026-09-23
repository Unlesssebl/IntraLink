"""Test SQLAlchemy 2.0 database models and schema definitions."""

import uuid

from core.database.models import (
    CommandRecord,
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
