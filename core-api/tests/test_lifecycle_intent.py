"""
Unit-тесты анализатора намерений заявителя (IntentAnalyzer).
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.services.lifecycle.intent_analyzer import IntentAnalyzer
from app.services.lifecycle.models import UserReplyIntent


def test_intent_analyzer_fast_regex_provide_ip():
    text = "Добрый день! Адрес нашего принтера: 10.128.4.55. Прошу подключить."
    res = IntentAnalyzer.analyze_fast_regex(text)
    assert res is not None
    assert res.intent == UserReplyIntent.PROVIDE_DATA
    assert res.extracted_ip == "10.128.4.55"
    assert res.source == "regex"


def test_intent_analyzer_fast_regex_provide_pc_and_ip():
    text = "ПК NTEMW0144, принтер 10.128.4.120"
    res = IntentAnalyzer.analyze_fast_regex(text)
    assert res is not None
    assert res.intent == UserReplyIntent.PROVIDE_DATA
    assert res.extracted_ip == "10.128.4.120"
    assert res.extracted_pc == "NTEMW0144"


def test_intent_analyzer_home_subnet_detected_and_clarified():
    text = "ПК NTEMW0144, принтер 192.168.1.200"
    res = IntentAnalyzer.analyze_fast_regex(text)
    assert res is not None
    assert res.intent == UserReplyIntent.CLARIFICATION_QUESTION
    assert "домашним/локальным" in (res.suggested_reply or "")


def test_intent_analyzer_fast_regex_cancel_keywords():
    cancellation_phrases = [
        "Уже не надо, коллеги помогли настроить.",
        "Не актуально, закрывайте.",
        "Решили сами, спасибо.",
        "Само заработало после перезагрузки!",
        "Прошу отменить заявку.",
    ]
    for phrase in cancellation_phrases:
        res = IntentAnalyzer.analyze_fast_regex(phrase)
        assert res is not None, f"Failed on phrase: {phrase}"
        assert res.intent == UserReplyIntent.CANCEL_REQUEST, f"Wrong intent for: {phrase}"


def test_intent_analyzer_fast_regex_clarification_question():
    question_phrases = [
        "А где посмотреть этот IP адрес?",
        "Подскажите где написан адрес принтера?",
        "Не знаю какой у него адрес, как узнать?",
    ]
    for phrase in question_phrases:
        res = IntentAnalyzer.analyze_fast_regex(phrase)
        assert res is not None, f"Failed on phrase: {phrase}"
        assert res.intent == UserReplyIntent.CLARIFICATION_QUESTION, f"Wrong intent for: {phrase}"
        assert res.suggested_reply is not None


@pytest.mark.asyncio
async def test_intent_analyzer_llm_fallback_provide_data():
    natural_text = "Принтер сетевой стоит в бухгалтерии, на дисплее написано 10.200.1.15"
    with patch("app.services.ai.hub.ai_hub.dispatch_routed_inference", new_callable=AsyncMock) as mock_llm:
        mock_response = AsyncMock()
        mock_response.text = '{"intent": "provide_data", "extracted_ip": "10.200.1.15", "summary": "Пользователь указал IP", "confidence": 0.95}'
        mock_llm.return_value = mock_response

        res = await IntentAnalyzer.analyze_with_llm(natural_text)
        # Fast regex should catch IP first
        assert res.intent == UserReplyIntent.PROVIDE_DATA
        assert res.extracted_ip == "10.200.1.15"


@pytest.mark.asyncio
async def test_intent_analyzer_llm_fallback_cancel_request():
    complex_cancel = "Мы переехали в другой филиал, этот принтер нам больше вообще не нужен."
    with patch("app.services.ai.hub.ai_hub.dispatch_routed_inference", new_callable=AsyncMock) as mock_llm:
        mock_response = AsyncMock()
        mock_response.text = '{"intent": "cancel_request", "summary": "Заявитель переехал и просит отменить", "confidence": 0.92}'
        mock_llm.return_value = mock_response

        res = await IntentAnalyzer.analyze_with_llm(complex_cancel)
        assert res.intent == UserReplyIntent.CANCEL_REQUEST
        assert res.source == "llm"
