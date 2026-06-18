import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.ai_classifier import AIClassifier, ClassifierResult

@pytest.mark.asyncio
async def test_ai_classifier_none_action():
    # Мокаем AsyncOpenAI
    mock_response = MagicMock()
    mock_parsed = ClassifierResult(
        action="none",
        confidence=1.0,
        correct_service_id=-1,
        correct_service_name="",
        comment_text="",
        reason="Раздел выбран верно"
    )
    mock_response.choices = [MagicMock(message=MagicMock(parsed=mock_parsed))]
    
    mock_llm_client = MagicMock()
    mock_llm_client.beta.chat.completions.parse = AsyncMock(return_value=mock_response)
    
    with (
        patch("app.services.ai_classifier.AsyncOpenAI", return_value=mock_llm_client),
        patch("app.services.ai_classifier.get_redis_client") as mock_redis_func,
        patch.object(AIClassifier, "get_similar_cases", return_value="Похожих кейсов не найдено") as mock_similar_cases
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis_func.return_value = mock_redis
        
        classifier = AIClassifier()
        
        task = {
            "Id": 12345,
            "Name": "Создать почту",
            "Description": "Прошу создать почту для нового сотрудника",
            "ServiceName": "Создание электронной почты",
            "ServiceId": 63
        }
        
        result = await classifier.classify_task(task)
        
        assert result.action == "none"
        assert result.correct_service_id == -1
        assert result.correct_service_name == ""
        assert result.comment_text == ""
        mock_similar_cases.assert_called_once()
        
@pytest.mark.asyncio
async def test_ai_classifier_redirect_action():
    # Мокаем AsyncOpenAI
    mock_response = MagicMock()
    mock_parsed = ClassifierResult(
        action="redirect",
        confidence=1.0,
        correct_service_id=70,
        correct_service_name="Разблокировка электронной почты",
        comment_text="Заявка отменена, так как создана в неверном разделе. Пересоздайте в Разблокировка электронной почты.",
        reason="Ошибочный раздел"
    )
    mock_response.choices = [MagicMock(message=MagicMock(parsed=mock_parsed))]
    
    mock_llm_client = MagicMock()
    mock_llm_client.beta.chat.completions.parse = AsyncMock(return_value=mock_response)
    
    with (
        patch("app.services.ai_classifier.AsyncOpenAI", return_value=mock_llm_client),
        patch("app.services.ai_classifier.get_redis_client") as mock_redis_func,
        patch.object(AIClassifier, "get_similar_cases", return_value="Кейс #132437 (Раздел: Создание электронной почты, Статус: Отменена):\nПроблема: восстановить пароль. Решение: отменить и перенаправить.") as mock_similar_cases
    ):
        mock_redis = AsyncMock()
        mock_redis.get = MagicMock()
        mock_redis.get = AsyncMock(return_value='[{"id": 70, "name": "Разблокировка электронной почты", "parent_id": 50}]')
        mock_redis_func.return_value = mock_redis
        
        classifier = AIClassifier()
        
        task = {
            "Id": 12345,
            "Name": "Восстановить пароль от почты",
            "Description": "Забыла пароль",
            "ServiceName": "Создание электронной почты",
            "ServiceId": 63
        }
        
        result = await classifier.classify_task(task)
        
        assert result.action == "redirect"
        assert result.correct_service_id == 70
        assert result.correct_service_name == "Разблокировка электронной почты"
        assert "пересоздайте" in result.comment_text.lower()
        mock_similar_cases.assert_called_once()
