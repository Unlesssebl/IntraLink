"""
Unit-тесты конечного автомата жизненного цикла (LifecycleStateMachine).
"""

from app.config import settings
from app.services.lifecycle.models import IntentAnalysisResult, UserReplyIntent
from app.services.lifecycle.state_machine import LifecycleStateMachine


def test_fsm_evaluate_open_task_missing_ip():
    task = {
        "Id": 140200,
        "Name": "Подключить принтер в кабинете 204",
        "Description": "Прошу установить принтер HP",
        "StatusId": settings.STATUS_OPEN_ID,
        "CustomFields": [
            {"CustomFieldId": settings.PRINTER_PC_CUSTOM_FIELD_ID, "Value": "NTEMW0144"}
        ],
    }
    step = LifecycleStateMachine.evaluate_open_task(task)
    assert step.action_taken == "request_clarification"
    assert step.target_status_id == settings.STATUS_WAITING_ID
    assert "IP-адрес" in (step.comment or "")


def test_fsm_evaluate_open_task_ready_for_execution():
    task = {
        "Id": 140201,
        "Name": "Установка сетевого принтера",
        "Description": "Прошу подключить принтер",
        "StatusId": settings.STATUS_OPEN_ID,
        "CustomFields": [
            {"CustomFieldId": settings.PRINTER_PC_CUSTOM_FIELD_ID, "Value": "NTEMW0144"},
            {"CustomFieldId": settings.PRINTER_IP_CUSTOM_FIELD_ID, "Value": "10.128.4.52"},
        ],
    }
    step = LifecycleStateMachine.evaluate_open_task(task)
    assert step.action_taken == "dispatch_execution"
    assert step.target_status_id == settings.STATUS_IN_PROGRESS_ID


def test_fsm_evaluate_waiting_task_user_provided_data():
    task = {"Id": 140202, "StatusId": settings.STATUS_WAITING_ID}
    intent_res = IntentAnalysisResult(
        intent=UserReplyIntent.PROVIDE_DATA,
        extracted_ip="10.128.4.60",
        extracted_pc="NTEMW0144",
        source="regex",
    )
    step = LifecycleStateMachine.evaluate_waiting_task(task, intent_res)
    assert step.action_taken == "resume_to_open"
    assert step.target_status_id == settings.STATUS_OPEN_ID


def test_fsm_evaluate_waiting_task_user_cancelled():
    task = {"Id": 140203, "StatusId": settings.STATUS_WAITING_ID}
    intent_res = IntentAnalysisResult(
        intent=UserReplyIntent.CANCEL_REQUEST,
        source="regex",
        summary="Заявитель решил проблему сам",
    )
    step = LifecycleStateMachine.evaluate_waiting_task(task, intent_res)
    assert step.action_taken == "cancel_by_user"
    assert step.target_status_id == settings.STATUS_CANCELLED_ID


def test_fsm_evaluate_waiting_task_unsupported_escalates():
    task = {"Id": 140204, "StatusId": settings.STATUS_WAITING_ID}
    intent_res = IntentAnalysisResult(
        intent=UserReplyIntent.UNSUPPORTED,
        source="fallback",
    )
    step = LifecycleStateMachine.evaluate_waiting_task(task, intent_res)
    assert step.action_taken == "escalate_to_human"
    assert step.target_status_id == settings.STATUS_OPEN_ID
    assert step.escalated_to_human is True


def test_fsm_evaluate_execution_result_success():
    task = {"Id": 140205, "StatusId": settings.STATUS_IN_PROGRESS_ID}
    step = LifecycleStateMachine.evaluate_execution_result(task, is_success=True)
    assert step.action_taken == "complete_success"
    assert step.target_status_id == settings.STATUS_COMPLETED_ID
    assert step.expenses == settings.AUTONOMOUS_AUTO_EXPENSES_MINUTES
    assert "успешно установлен" in (step.comment or "")


def test_fsm_evaluate_execution_result_pc_offline():
    task = {"Id": 140206, "StatusId": settings.STATUS_IN_PROGRESS_ID}
    step = LifecycleStateMachine.evaluate_execution_result(
        task, is_success=False, error_message="Host NTEMW0144 is offline (ping failed 100% packet loss)"
    )
    assert step.action_taken == "request_pc_power_on"
    assert step.target_status_id == settings.STATUS_WAITING_ID
    assert "компьютер выключен" in (step.comment or "")


def test_fsm_evaluate_execution_result_driver_error_escalates():
    task = {"Id": 140207, "StatusId": settings.STATUS_IN_PROGRESS_ID}
    step = LifecycleStateMachine.evaluate_execution_result(
        task, is_success=False, error_message="WinRM error: Add-PrinterDriver failed with code 0x80070002"
    )
    assert step.action_taken == "escalate_technical_error"
    assert step.target_status_id == settings.STATUS_OPEN_ID
    assert step.escalated_to_human is True
