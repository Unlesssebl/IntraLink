"""
Интеграционные и unit-тесты автономного оркестратора жизненного цикла (AutonomousTicketOrchestrator).
"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services.lifecycle.orchestrator import AutonomousTicketOrchestrator, get_ticket_orchestrator


@pytest.fixture
def mock_redis():
    class MockRedis:
        def __init__(self):
            self.store = {}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self.store:
                return None
            self.store[key] = value
            return True

        async def delete(self, *keys):
            for k in keys:
                self.store.pop(k, None)
            return True

        async def xadd(self, stream, fields, maxlen=None, approximate=True):
            return "1000-0"

    return MockRedis()


@pytest.mark.asyncio
async def test_orchestrator_disabled_in_config(mock_redis):
    orchestrator = AutonomousTicketOrchestrator()
    with patch("app.services.lifecycle.orchestrator.settings.AUTONOMOUS_LIFECYCLE_ENABLED", False):
        res = await orchestrator.process_assigned_tasks("auth")
        assert res == []


@pytest.mark.asyncio
async def test_orchestrator_skips_when_no_bot_user_id(mock_redis):
    orchestrator = AutonomousTicketOrchestrator()
    with patch("app.services.lifecycle.orchestrator.settings.AUTONOMOUS_LIFECYCLE_ENABLED", True), \
         patch("app.services.lifecycle.orchestrator.settings.INTRASERVICE_SERVICE_USER_ID", None):
        res = await orchestrator.process_assigned_tasks("auth")
        assert res == []


@pytest.mark.asyncio
async def test_orchestrator_processes_open_task_missing_ip(mock_redis):
    orchestrator = AutonomousTicketOrchestrator()
    task_31 = {
        "Id": 140301,
        "Name": "Подключить принтер",
        "Description": "Не печатает МФУ",
        "StatusId": settings.STATUS_OPEN_ID,
        "CustomFields": [
            {"CustomFieldId": settings.PRINTER_PC_CUSTOM_FIELD_ID, "Value": "NTEMW0144"}
        ],
    }

    with patch("app.services.lifecycle.orchestrator.settings.AUTONOMOUS_LIFECYCLE_ENABLED", True), \
         patch("app.services.lifecycle.orchestrator.settings.INTRASERVICE_SERVICE_USER_ID", 10001), \
         patch("app.services.lifecycle.orchestrator.get_redis_client", return_value=mock_redis), \
         patch("app.services.lifecycle.orchestrator.get_tasks", new_callable=AsyncMock) as mock_get_tasks, \
         patch("app.services.lifecycle.orchestrator.update_task_status", new_callable=AsyncMock) as mock_update_status, \
         patch("app.services.lifecycle.orchestrator.add_task_comment", new_callable=AsyncMock) as mock_add_comment:

        mock_get_tasks.return_value = {"Tasks": [task_31]}
        mock_update_status.return_value = True
        mock_add_comment.return_value = True

        results = await orchestrator.process_assigned_tasks("test_auth")

        assert len(results) == 1
        assert results[0].action_taken == "request_clarification"
        assert results[0].target_status_id == settings.STATUS_WAITING_ID
        mock_update_status.assert_called_once_with("test_auth", 140301, settings.STATUS_WAITING_ID)
        mock_add_comment.assert_called_once()


@pytest.mark.asyncio
async def test_orchestrator_processes_open_task_ready_for_execution(mock_redis):
    orchestrator = AutonomousTicketOrchestrator()
    task_31_ready = {
        "Id": 140302,
        "Name": "Установить принтер",
        "Description": "Подключение",
        "StatusId": settings.STATUS_OPEN_ID,
        "CustomFields": [
            {"CustomFieldId": settings.PRINTER_PC_CUSTOM_FIELD_ID, "Value": "NTEMW0144"},
            {"CustomFieldId": settings.PRINTER_IP_CUSTOM_FIELD_ID, "Value": "10.128.4.52"},
        ],
    }

    with patch("app.services.lifecycle.orchestrator.settings.AUTONOMOUS_LIFECYCLE_ENABLED", True), \
         patch("app.services.lifecycle.orchestrator.settings.INTRASERVICE_SERVICE_USER_ID", 10001), \
         patch("app.services.lifecycle.orchestrator.get_redis_client", return_value=mock_redis), \
         patch("app.services.lifecycle.orchestrator.get_tasks", new_callable=AsyncMock) as mock_get_tasks, \
         patch("app.services.lifecycle.orchestrator.update_task_status", new_callable=AsyncMock) as mock_update_status, \
         patch("app.services.lifecycle.orchestrator.add_task_comment", new_callable=AsyncMock) as mock_add_comment, \
         patch("app.services.lifecycle.orchestrator.AsyncSessionLocal") as mock_db_ctx:

        mock_db = AsyncMock()
        mock_db_ctx.return_value.__aenter__.return_value = mock_db

        mock_get_tasks.return_value = {"Tasks": [task_31_ready]}
        mock_update_status.return_value = True
        mock_add_comment.return_value = True

        results = await orchestrator.process_assigned_tasks("test_auth")

        assert len(results) == 1
        assert results[0].action_taken == "dispatch_execution"
        assert results[0].target_status_id == settings.STATUS_IN_PROGRESS_ID
        mock_update_status.assert_called_once_with("test_auth", 140302, settings.STATUS_IN_PROGRESS_ID)
        assert mock_redis.store.get("task:140302:execution_job") is not None


@pytest.mark.asyncio
async def test_orchestrator_processes_waiting_task_applicant_reply(mock_redis):
    orchestrator = AutonomousTicketOrchestrator()
    task_35 = {
        "Id": 140303,
        "Name": "Установить принтер",
        "StatusId": settings.STATUS_WAITING_ID,
        "CreatorId": 501,
    }
    comments = [
        {
            "Id": 999,
            "Comment": "Добрый день! Адрес нашего принтера 10.128.4.88",
            "EditorId": 501,
            "Created": "2026-09-04T00:10:00",
        }
    ]

    with patch("app.services.lifecycle.orchestrator.settings.AUTONOMOUS_LIFECYCLE_ENABLED", True), \
         patch("app.services.lifecycle.orchestrator.settings.INTRASERVICE_SERVICE_USER_ID", 10001), \
         patch("app.services.lifecycle.orchestrator.get_redis_client", return_value=mock_redis), \
         patch("app.services.lifecycle.orchestrator.get_tasks", new_callable=AsyncMock) as mock_get_tasks, \
         patch("app.services.lifecycle.orchestrator.get_task_comments", new_callable=AsyncMock) as mock_get_comments, \
         patch("app.services.lifecycle.orchestrator.update_task_custom_fields", new_callable=AsyncMock) as mock_update_cf, \
         patch("app.services.lifecycle.orchestrator.update_task_status", new_callable=AsyncMock) as mock_update_status, \
         patch("app.services.lifecycle.orchestrator.add_task_comment", new_callable=AsyncMock) as mock_add_comment:

        mock_get_tasks.return_value = {"Tasks": [task_35]}
        mock_get_comments.return_value = comments
        mock_update_cf.return_value = True
        mock_update_status.return_value = True
        mock_add_comment.return_value = True

        results = await orchestrator.process_assigned_tasks("test_auth")

        assert len(results) == 1
        assert results[0].action_taken == "resume_to_open"
        assert results[0].target_status_id == settings.STATUS_OPEN_ID
        mock_update_cf.assert_called_once()
        mock_update_status.assert_called_once_with("test_auth", 140303, settings.STATUS_OPEN_ID)


@pytest.mark.asyncio
async def test_orchestrator_processes_in_progress_task_job_success(mock_redis):
    orchestrator = AutonomousTicketOrchestrator()
    task_27 = {
        "Id": 140304,
        "Name": "Установить принтер",
        "StatusId": settings.STATUS_IN_PROGRESS_ID,
    }
    job_id = "job_123456"
    mock_redis.store["task:140304:execution_job"] = job_id
    mock_redis.store[f"execution_job:{job_id}"] = json.dumps({
        "job_id": job_id,
        "status": "success",
        "ticket_close_ok": False,
    })

    with patch("app.services.lifecycle.orchestrator.settings.AUTONOMOUS_LIFECYCLE_ENABLED", True), \
         patch("app.services.lifecycle.orchestrator.settings.INTRASERVICE_SERVICE_USER_ID", 10001), \
         patch("app.services.lifecycle.orchestrator.get_redis_client", return_value=mock_redis), \
         patch("app.services.lifecycle.orchestrator.get_tasks", new_callable=AsyncMock) as mock_get_tasks, \
         patch("app.services.lifecycle.orchestrator.update_task_status", new_callable=AsyncMock) as mock_update_status, \
         patch("app.services.lifecycle.orchestrator.add_task_comment", new_callable=AsyncMock) as mock_add_comment, \
         patch("app.services.lifecycle.orchestrator.add_task_expenses", new_callable=AsyncMock) as mock_add_expenses:

        mock_get_tasks.return_value = {"Tasks": [task_27]}
        mock_update_status.return_value = True
        mock_add_comment.return_value = True
        mock_add_expenses.return_value = True

        results = await orchestrator.process_assigned_tasks("test_auth")

        assert len(results) == 1
        assert results[0].action_taken == "complete_success"
        assert results[0].target_status_id == settings.STATUS_COMPLETED_ID
        mock_update_status.assert_called_once_with("test_auth", 140304, settings.STATUS_COMPLETED_ID)
        mock_add_expenses.assert_called_once_with("test_auth", 140304, 15, user_id=10001)
        assert mock_redis.store.get("task:140304:execution_job") is None
