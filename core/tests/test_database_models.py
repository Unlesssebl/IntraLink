"""Test SQLAlchemy 2.0 database models and schema definitions."""

import uuid

from core.database.models import (
    ActionPlanRecord,
    CaseDecisionRecord,
    CaseFeedbackRecord,
    CommandRecord,
    ExecutionFeedbackRecord,
    PlanFeedbackRecord,
    TaskKnowledgeBase,
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


def test_case_decision_record_model_instantiation():
    dec_id = uuid.uuid4()
    frame_id = uuid.uuid4()
    record = CaseDecisionRecord(
        id=dec_id,
        task_id=777,
        snapshot_hash="f" * 64,
        frame_id=frame_id,
        frame_version="case-frame-v1",
        router_version="case-router-v1",
        state="selected",
        primary_case_type="printer_connection_request",
        case_frame_json={"id": str(frame_id), "task_id": 777},
        decision_json={"state": "selected", "primary_case_type": "printer_connection_request"},
    )
    assert record.id == dec_id
    assert record.frame_id == frame_id
    assert record.primary_case_type == "printer_connection_request"
    assert record.case_frame_json["task_id"] == 777


def test_stage_specific_feedback_models():
    feedback_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    feedback = CaseFeedbackRecord(
        id=feedback_id,
        case_decision_id=decision_id,
        task_id=777,
        snapshot_hash="f" * 64,
        operator_username="supervisor",
        verdict="corrected",
        original_case_type="printing_incident",
        corrected_case_type="wireless_access_request",
        corrected_frame_json={"password": "supersecretpassword"},
        reason_tag="misclassification",
        notes="Исправлен тип обращения",
    )
    assert feedback.id == feedback_id
    assert feedback.case_decision_id == decision_id
    assert feedback.corrected_case_type == "wireless_access_request"


def test_action_plan_feedback_and_execution_are_plan_bound():
    decision_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    command_id = uuid.uuid4()
    plan = ActionPlanRecord(
        id=plan_id,
        workflow_plan_id=workflow_id,
        case_decision_id=decision_id,
        task_id=777,
        snapshot_hash="f" * 64,
        workflow_key="wireless_access",
        workflow_version="1",
        state="ready",
        disposition="execute",
        plan_hash="a" * 64,
        plan_json={"actions": []},
    )
    plan_feedback = PlanFeedbackRecord(
        action_plan_id=plan_id,
        task_id=777,
        operator_username="supervisor",
        verdict="approved",
        original_plan_hash="a" * 64,
    )
    execution = ExecutionFeedbackRecord(
        action_plan_id=plan_id,
        command_id=command_id,
        task_id=777,
        action_id="grant_wlan",
        capability_key="add_wlan_group_member",
        outcome="succeeded",
        result_json={"verified": True},
    )
    assert plan.plan_hash == "a" * 64
    assert plan_feedback.action_plan_id == plan.id
    assert execution.capability_key == "add_wlan_group_member"
