import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json


@pytest.mark.asyncio
async def test_sync_service_catalog():
    from app.services.worker import sync_service_catalog
    mock_services = [
        {"Id": 63, "Name": "Создание электронной почты", "ParentId": 42, "Path": "42|63|"},
        {"Id": 70, "Name": "Разблокировка электронной почты", "ParentId": 50, "Path": "50|70|"}
    ]
    
    with (
        patch("app.services.worker.get_redis_client") as mock_redis_func,
        patch("app.services.worker.get_services", new_callable=AsyncMock) as mock_get_services
    ):
        mock_redis = AsyncMock()
        mock_redis_func.return_value = mock_redis
        mock_get_services.return_value = mock_services
        
        # Симулируем учетные данные
        mock_redis.get = AsyncMock(return_value="ZW5jcnlwdGVkX2F1dGg=") # base64
        
        await sync_service_catalog()
        
        mock_get_services.assert_awaited_once()
        mock_redis.set.assert_awaited_once()
        
        # Проверяем, что в Redis записались правильные данные
        args, kwargs = mock_redis.set.call_args
        assert args[0] == "worker:service_catalog"
        catalog_data = json.loads(args[1])
        assert len(catalog_data) == 2
        assert catalog_data[0]["id"] == 63
        assert catalog_data[1]["name"] == "Разблокировка электронной почты"

@pytest.mark.asyncio
async def test_worker_ai_classifier_integration():
    from app.services.worker import check_updates
    from app.services.ai_classifier import ClassifierResult
    mock_tasks = [
        {
            "Id": 132437,
            "Name": "Восстановление пароля почты",
            "Description": "Забыла пароль",
            "ServiceName": "Создание электронной почты",
            "ServiceId": 63,
            "StatusId": 1
        }
    ]
    
    mock_response = ClassifierResult(
        action="redirect",
        confidence=1.0,
        correct_service_id=70,
        correct_service_name="Разблокировка электронной почты",
        comment_text="Заявка отменена, пересоздайте в Разблокировка электронной почты.",
        reason="Неверный раздел"
    )
    
    with (
        patch("app.services.worker.get_redis_client") as mock_redis_func,
        patch("app.services.worker.get_tasks", new_callable=AsyncMock) as mock_get_tasks,
        patch("app.services.worker.add_task_comment", new_callable=AsyncMock) as mock_add_comment,
        patch("app.services.worker.update_task_status", new_callable=AsyncMock) as mock_update_status,
        patch("app.services.worker.check_waiting_printer_tasks", new_callable=AsyncMock),
        patch("app.services.ai_classifier.AIClassifier.classify_task", new_callable=AsyncMock) as mock_classify,
        patch("app.services.worker.AsyncSessionLocal") as mock_db_session,
        patch("app.services.worker.settings.INTRASERVICE_SERVICE_USER_ID", 9999)
    ):
        mock_redis = AsyncMock()
        mock_redis_func.return_value = mock_redis
        
        # Симулируем, что задача не была обработана классификатором
        mock_redis.get.side_effect = lambda key: None if "ai_classified" in key else "2026-06-17 12:00:00"
        
        # Симулируем новые задачи
        mock_get_tasks.side_effect = [
            {"Tasks": mock_tasks, "Statuses": [{"Id": 1, "Name": "Открыта"}]}, # new tasks
            {"Tasks": [], "Statuses": []} # updated tasks
        ]
        
        mock_classify.return_value = mock_response
        mock_add_comment.return_value = True
        mock_update_status.return_value = True
        
        # Симулируем БД пользователей
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result
        mock_db_session.return_value.__aenter__.return_value = mock_db
        
        await check_updates()
        
        # Проверяем, что классификатор вызвался для новой задачи
        mock_classify.assert_awaited_once()
        # Проверяем, что добавился комментарий
        mock_add_comment.assert_awaited_once_with(
            "2026-06-17 12:00:00", 132437, mock_response.comment_text
        )
        # Проверяем, что статус обновился на 30 (Отменена)
        mock_update_status.assert_awaited_once_with(
            "2026-06-17 12:00:00", 132437, 30
        )
        # Проверяем, что в Redis записалась пометка ai_classified:132437
        mock_redis.set.assert_any_call("ai_classified:132437", "1", ex=604800)
