from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.routers.service_tasks import (
    ServiceTaskCommentRequest,
    ServiceTaskCustomFieldsRequest,
    ServiceTaskExpensesRequest,
    ServiceTaskStatusRequest,
    add_task_comment,
    add_task_expenses,
    get_task_by_id,
    update_task_custom_fields,
    update_task_status,
)


@pytest.mark.asyncio
@patch("app.routers.service_tasks.intraservice.get_single_task", new_callable=AsyncMock)
async def test_get_task_by_id_success(mock_get_task):
    mock_get_task.return_value = {"Id": 123, "Name": "Test Task"}

    response = await get_task_by_id(task_id=123, service_auth_b64="mocked_auth")

    assert response == {"Id": 123, "Name": "Test Task"}
    mock_get_task.assert_awaited_once_with("mocked_auth", 123)


@pytest.mark.asyncio
@patch("app.routers.service_tasks.intraservice.get_single_task", new_callable=AsyncMock)
async def test_get_task_by_id_failure(mock_get_task):
    mock_get_task.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await get_task_by_id(task_id=123, service_auth_b64="mocked_auth")

    assert exc_info.value.status_code == 502
    assert "Не удалось получить задачу 123" in exc_info.value.detail


@pytest.mark.asyncio
@patch("app.routers.service_tasks.intraservice.add_task_comment", new_callable=AsyncMock)
async def test_add_task_comment_success(mock_add_comment):
    mock_add_comment.return_value = True

    payload = ServiceTaskCommentRequest(comment="Test Comment")
    response = await add_task_comment(
        task_id=123, payload=payload, service_auth_b64="mocked_auth"
    )

    assert response == {"status": "success"}
    mock_add_comment.assert_awaited_once_with("mocked_auth", 123, "Test Comment")


@pytest.mark.asyncio
@patch("app.routers.service_tasks.intraservice.add_task_comment", new_callable=AsyncMock)
async def test_add_task_comment_failure(mock_add_comment):
    mock_add_comment.return_value = False

    payload = ServiceTaskCommentRequest(comment="Test Comment")
    with pytest.raises(HTTPException) as exc_info:
        await add_task_comment(
            task_id=123, payload=payload, service_auth_b64="mocked_auth"
        )

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
@patch("app.routers.service_tasks.intraservice.update_task_status", new_callable=AsyncMock)
async def test_update_task_status_success(mock_update_status):
    mock_update_status.return_value = True

    payload = ServiceTaskStatusRequest(status_id=31)
    response = await update_task_status(
        task_id=123, payload=payload, service_auth_b64="mocked_auth"
    )

    assert response == {"status": "success"}
    mock_update_status.assert_awaited_once_with("mocked_auth", 123, 31)


@pytest.mark.asyncio
@patch("app.routers.service_tasks.intraservice.add_task_expenses", new_callable=AsyncMock)
async def test_add_task_expenses_success(mock_add_expenses):
    mock_add_expenses.return_value = True

    payload = ServiceTaskExpensesRequest(minutes=15)
    response = await add_task_expenses(
        task_id=123, payload=payload, service_auth_b64="mocked_auth"
    )

    assert response == {"status": "success"}
    mock_add_expenses.assert_awaited_once_with("mocked_auth", 123, 15)


@pytest.mark.asyncio
@patch("app.routers.service_tasks.intraservice.update_task_custom_fields", new_callable=AsyncMock)
async def test_update_task_custom_fields_success(mock_update_fields):
    mock_update_fields.return_value = True

    payload = ServiceTaskCustomFieldsRequest(custom_field_values=[{"FieldId": 1112, "Value": "KZM1234"}])
    response = await update_task_custom_fields(
        task_id=123, payload=payload, service_auth_b64="mocked_auth"
    )

    assert response == {"status": "success"}
    mock_update_fields.assert_awaited_once_with("mocked_auth", 123, [{"FieldId": 1112, "Value": "KZM1234"}])


@pytest.mark.asyncio
@patch("app.routers.service_tasks.intraservice.update_task_custom_fields", new_callable=AsyncMock)
async def test_update_task_custom_fields_failure(mock_update_fields):
    mock_update_fields.return_value = False

    payload = ServiceTaskCustomFieldsRequest(custom_field_values=[{"FieldId": 1112, "Value": "KZM1234"}])
    with pytest.raises(HTTPException) as exc_info:
        await update_task_custom_fields(
            task_id=123, payload=payload, service_auth_b64="mocked_auth"
        )

    assert exc_info.value.status_code == 502

