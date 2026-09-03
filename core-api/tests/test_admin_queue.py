import pytest
from unittest.mock import patch, AsyncMock
from app.services.triage_service import TriageService
from app.services.triage_session import TriageSessionManager
from app.routers.triage import ApplyTriageRequest, apply_triage_action, get_triage_batch


@pytest.mark.asyncio
@patch("app.services.intraservice.get_tasks_by_filter")
async def test_prepare_triage_batch(mock_get_tasks):
    mock_get_tasks.return_value = [
        {
            "Id": 101,
            "Name": "Настройка Wi-Fi для ноутбука",
            "Description": "Прошу дать доступ к сети WLAN",
            "ServiceName": "01. Доступ к сети Wi-Fi",
            "ServiceId": 10,
            "Creator": "Иванов Иван",
            "StatusId": 26,
            "StatusName": "Новая",
            "Data": '<field id="1089">WS-IVANOV</field>',
            "_field_meta": {"pc_name": "WS-IVANOV", "phone": "49-87"},
        }
    ]

    mock_db = AsyncMock()
    with patch("app.routers.triage.search_knowledge_base", return_value=[]):
        res = await TriageService.prepare_triage_batch(
            service_auth_b64="dXNlcjpwYXNz",
            db=mock_db,
            filter_id=984,
            limit=5,
        )

    assert res["total_open"] == 1
    assert len(res["tasks"]) == 1
    task_item = res["tasks"][0]
    assert task_item["task_id"] == 101
    assert task_item["suggested_action"]["template_key"] == "wifi_access"
    assert task_item["pc_name"] == "WS-IVANOV"


@pytest.mark.asyncio
@patch("app.routers.triage.get_redis_client")
async def test_triage_session_manager(mock_get_redis):
    mock_redis = AsyncMock()
    mock_redis.smembers.return_value = {"101", "102"}
    mock_get_redis.return_value = mock_redis

    skipped = await TriageSessionManager.get_skipped_task_ids("operator_1")
    assert skipped == {101, 102}

    count = await TriageSessionManager.skip_tasks([103, 104], operator_id="operator_1")
    assert count == 2
    assert mock_redis.sadd.called


@pytest.mark.asyncio
@patch("app.services.intraservice.get_single_task")
@patch("app.services.intraservice.update_task_full")
@patch("app.services.intraservice.add_task_expenses")
async def test_apply_triage_resolution(mock_add_expenses, mock_update_task, mock_get_task):
    mock_get_task.return_value = {"Id": 101, "StatusId": 26, "Name": "Тест"}
    mock_update_task.return_value = True
    mock_add_expenses.return_value = True

    mock_db = AsyncMock()
    results = await TriageService.apply_triage_resolution(
        service_auth_b64="dXNlcjpwYXNz",
        db=mock_db,
        task_ids=[101],
        status_id=29,
        comment="Доступ к WLAN предоставлен",
        expenses=10,
    )

    assert len(results) == 1
    assert results[0]["task_id"] == 101
    assert results[0]["status"] == "success"
    assert mock_update_task.called
    assert mock_add_expenses.called


@pytest.mark.asyncio
@patch("app.routers.admin._check_host_ping_and_ports")
async def test_get_host_diagnostics(mock_check):
    from app.routers.admin import get_host_diagnostics
    mock_check.return_value = {
        "host": "WS-TEST-01",
        "is_online": True,
        "avg_rtt": "2ms",
        "smb_ok": True,
        "winrm_ok": True,
        "status_label": "🟢 2ms",
    }
    res = await get_host_diagnostics("WS-TEST-01")
    assert res["is_online"] is True
    assert res["avg_rtt"] == "2ms"


@pytest.mark.asyncio
@patch("app.services.intraservice.get_single_task")
@patch("app.services.intraservice.get_task_lifetime")
async def test_get_task_card_details(mock_lifetime, mock_task):
    mock_task.return_value = {
        "Id": 202,
        "Name": "Сбой 1С ЗУП",
        "Description": "Подробное описание проблемы",
        "Creator": "Петров Петр",
        "ServiceName": "1-я линия техподдержки",
        "ServiceId": 1,
        "StatusId": 27,
        "StatusName": "В работе",
    }
    mock_lifetime.return_value = [
        {"Id": 1, "UserName": "Петров", "Created": "2026-08-25", "Comment": "Жду решения"}
    ]

    mock_db = AsyncMock()
    with patch("app.routers.triage.search_knowledge_base", return_value=[]):
        with patch("app.routers.triage.synthesize_triage_resolution", return_value=None):
            card = await TriageService.get_task_card_details(
                service_auth_b64="dXNlcjpwYXNz",
                db=mock_db,
                task_id=202,
            )

    assert card is not None
    assert card["task"]["Id"] == 202
    assert len(card["history"]) == 1
    assert card["history"][0]["Comment"] == "Жду решения"
