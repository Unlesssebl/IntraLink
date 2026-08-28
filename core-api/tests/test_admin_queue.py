import pytest
from unittest.mock import patch, AsyncMock
from app.routers.admin import (
    _classify_queue_task,
    _parse_task_custom_fields,
    get_triage_queue,
    apply_task_action,
    ApplyActionRequest,
)


def test_classify_queue_task_wifi():
    task = {
        "Name": "Доступ к Wi-Fi для нового ноутбука",
        "Description": "Прошу предоставить доступ к беспроводной сети wlan",
        "ServiceName": "Wi-Fi доступ",
    }
    res = _classify_queue_task(task)
    assert res["rule_type"] == "wlan_access"
    assert res["target_status_id"] == 29
    assert res["score"] >= 9


def test_classify_queue_task_1c():
    task = {
        "Name": "Ошибка в 1С ЗУП",
        "Description": "Не открывается база ЗУП",
        "ServiceName": "1-я линия техподдержки",
    }
    res = _classify_queue_task(task)
    assert res["rule_type"] == "redirect_1c"
    assert res["target_status_id"] == 30


def test_classify_queue_task_hardware_repair():
    task = {
        "Name": "Сильно тормозит и греется ноутбук",
        "Description": "Компьютер зависает, шумит вентилятор, нужна чистка",
        "ServiceName": "Компьютеры",
    }
    res = _classify_queue_task(task)
    assert res["rule_type"] == "hardware_repair"
    assert res["target_status_id"] == 48


def test_parse_task_custom_fields():
    data_xml = '<fields><field id="1089">WS-OFFICE-01</field><field id="1088">49-87</field><field id="1087">112</field></fields>'
    parsed = _parse_task_custom_fields(data_xml)
    assert parsed["pc_name"] == "WS-OFFICE-01"
    assert parsed["phone"] == "49-87"
    assert parsed["room"] == "112"


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
@patch("app.services.crypto.decrypt_token")
@patch("app.services.intraservice.get_tasks")
async def test_get_triage_queue(mock_get_tasks, mock_decrypt, mock_get_redis):
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "encrypted_auth"
    mock_get_redis.return_value = mock_redis
    mock_decrypt.return_value = "dXNlcjpwYXNz"

    mock_get_tasks.return_value = {
        "Tasks": [
            {
                "Id": 101,
                "Name": "Настройка Wi-Fi",
                "Description": "Прошу дать доступ к сети WLAN",
                "ServiceName": "Wi-Fi",
                "Creator": "Иванов Иван",
                "Data": '<field id="1089">WS-IVANOV</field>',
            }
        ]
    }

    res = await get_triage_queue(filter_id=984, limit=10)
    assert res["total"] == 1
    assert res["tasks"][0]["id"] == 101
    assert res["tasks"][0]["rule_type"] == "wlan_access"
    assert res["tasks"][0]["pc_name"] == "WS-IVANOV"


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
@patch("app.services.crypto.decrypt_token")
@patch("app.services.intraservice.get_single_task")
@patch("app.services.intraservice.update_task_full")
@patch("app.services.intraservice.add_task_expenses")
async def test_apply_task_action(
    mock_add_expenses, mock_update_task, mock_get_task, mock_decrypt, mock_get_redis
):
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "encrypted_auth"
    mock_get_redis.return_value = mock_redis
    mock_decrypt.return_value = "dXNlcjpwYXNz"

    mock_get_task.return_value = {"Id": 101, "StatusId": 1}
    mock_update_task.return_value = True
    mock_add_expenses.return_value = True

    req = ApplyActionRequest(
        status_id=29,
        comment="Доступ к WLAN предоставлен",
        minutes=10,
        executor_ids="8664,10502",
    )

    res = await apply_task_action(task_id=101, payload=req)
    assert res["success"] is True
    assert res["task_id"] == 101
    assert res["final_status_id"] == 29
    assert mock_update_task.called
    assert mock_add_expenses.called


@pytest.mark.asyncio
async def test_get_templates_catalog():
    from app.routers.admin import get_templates_catalog
    res = await get_templates_catalog()
    assert "templates" in res
    assert "map" in res
    assert len(res["templates"]) >= 5
    assert "wifi_access" in res["map"]


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
@patch("app.routers.admin.get_redis_client")
@patch("app.services.crypto.decrypt_token")
@patch("app.services.intraservice.get_single_task")
@patch("app.services.intraservice.get_task_lifetime")
async def test_get_task_details(mock_lifetime, mock_task, mock_decrypt, mock_redis):
    from app.routers.admin import get_task_details
    r_mock = AsyncMock()
    r_mock.get.return_value = "encrypted_auth"
    mock_redis.return_value = r_mock
    mock_decrypt.return_value = "auth_str"
    mock_task.return_value = {
        "Id": 202,
        "Name": "Сбой 1С ЗУП",
        "Description": "Подробное описание проблемы",
        "Creator": "Петров Петр",
        "ServiceName": "1-я линия техподдержки",
        "StatusId": 27,
        "StatusName": "В работе",
        "Data": '<field id="1089">WS-PETROV</field>',
        "Attachments": [{"Id": 501, "Name": "error.png", "Size": 1024}],
    }
    mock_lifetime.return_value = [
        {"Id": 1, "UserName": "Петров", "Created": "2026-08-25", "Comment": "Жду решения"}
    ]

    res = await get_task_details(202)
    assert res["id"] == 202
    assert res["pc_name"] == "WS-PETROV"
    assert len(res["attachments"]) == 1
    assert len(res["comments"]) == 1
    assert res["comments"][0]["text"] == "Жду решения"


@pytest.mark.asyncio
@patch("app.routers.admin.apply_task_action")
async def test_bulk_apply_tasks(mock_apply):
    from app.routers.admin import bulk_apply_tasks, BulkApplyRequest, BulkApplyItem
    mock_apply.return_value = {"success": True, "task_id": 101}
    payload = BulkApplyRequest(
        tasks=[
            BulkApplyItem(task_id=101, status_id=29, comment="Готово"),
            BulkApplyItem(task_id=102, status_id=30, comment="Редирект"),
        ]
    )
    res = await bulk_apply_tasks(payload)
    assert res["total"] == 2
    assert res["success_count"] == 2
    assert len(res["applied"]) == 2

