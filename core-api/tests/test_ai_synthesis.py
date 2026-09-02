"""
Юнит-тесты для Фазы 5: AI-генерация ответов инженера (Response Synthesis)
и автоматическая канонизация базы знаний (Auto-KB Canonization).
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.ai.schemas import DataCircuit, RoutedInferenceResponse
from app.services.ai_synthesis import (
    _synthesize_deterministic_fallback,
    canonize_task_solution,
    synthesize_triage_resolution,
)
from app.services.rag import sync_historical_closed_tasks


# ===========================================================================
# 1. Тесты канонизации базы знаний (Auto-KB Canonization)
# ===========================================================================


def test_canonize_task_solution_noise_stripping_and_root_cause():
    """Проверка очистки от шума, подписей и определения первопричины сбоя."""
    task = {
        "Id": 12345,
        "Name": "Срочно помогите пожалуйста!",
        "Description": "Здравствуйте! Не печатает принтер Kyocera M2040dn, ошибка 0x0000011b.",
    }
    lifetime = [
        {"Comment": "Статус изменен на 'В работе'"},
        {"Comment": "Назначен исполнитель: Беликов Ален"},
        {
            "Comment": (
                "Удалил проблемное обновление KB5005565 и прописал ключ реестра RpcAuthnLevelExemption=0. "
                "Служба Spooler перезапущена. С уважением, инженер Беликов Ален, тел. 12-34"
            )
        },
    ]

    canon = canonize_task_solution(task, lifetime)

    assert "Здравствуйте" not in canon["problem"]
    assert "пожалуйста" not in canon["problem"]
    assert "Kyocera M2040dn" in canon["problem"]
    assert "0x0000011b" in canon["problem"]

    # Решение очищено от телефонной подписи
    assert "KB5005565" in canon["solution"]
    assert "RpcAuthnLevelExemption" in canon["solution"]
    assert "С уважением" not in canon["solution"]
    assert "тел. 12-34" not in canon["solution"]

    # Первопричина
    assert "Spooler" in canon["root_cause"] or "печати" in canon["root_cause"]
    assert "Первопричина:" in canon["canonical_summary"]


# ===========================================================================
# 2. Тесты детерминированного синтеза ответа (Deterministic Fallback)
# ===========================================================================


def test_synthesize_deterministic_fallback_printing():
    """Проверка формирования ответа по печати с учетом телеметрии хоста."""
    task = {
        "Id": 9988,
        "Name": "Не печатает принтер",
        "Description": "Зависла очередь печати",
    }
    telemetry = {
        "pc_name": "NTEMW0144",
        "ping_status": "ONLINE",
        "metrics": {"spooler": "RUNNING", "disk_free_gb": 45.0},
    }

    resp = _synthesize_deterministic_fallback(task=task, telemetry=telemetry)
    assert "Здравствуйте!" in resp
    assert "#9988" in resp
    assert "NTEMW0144" in resp
    assert "Spooler" in resp
    assert "Проверьте, пожалуйста" in resp


def test_synthesize_deterministic_fallback_rag_match():
    """Проверка формирования ответа на основе RAG прецедента."""
    task = {
        "Id": 7766,
        "Name": "Ошибка 1С",
        "Description": "Ошибка блокировки сеанса",
    }
    kb_matches = [
        {
            "solution": "Очищен локальный кэш пользователя в %LOCALAPPDATA%\\1C и завершен зависший сеанс."
        }
    ]

    resp = _synthesize_deterministic_fallback(task=task, kb_matches=kb_matches)
    assert "Здравствуйте!" in resp
    assert "#7766" in resp
    assert "Очищен локальный кэш" in resp
    assert "Пожалуйста, проверьте" in resp


# ===========================================================================
# 3. Тесты Response Synthesis с контурами DLP Zero Trust
# ===========================================================================


@pytest.mark.asyncio
async def test_synthesize_triage_resolution_red_circuit_isolation():
    """Проверка, что для RED контура (пароли/секреты) облачный AI Hub не вызывается."""
    task = {
        "Id": 5544,
        "Name": "Сброс пароля",
        "Description": "Пользователь забыл пароль: SecretPassword123",
        "ServiceId": 42,
    }

    with patch.object(
        __import__("app.services.ai_synthesis", fromlist=["ai_hub"]).ai_hub,
        "dispatch_routed_inference",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        resp = await synthesize_triage_resolution(
            task=task,
            circuit=DataCircuit.RED,
        )

        assert "Здравствуйте!" in resp
        assert "#5544" in resp
        # RED контур: dispatch_routed_inference НЕ вызывался
        mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_triage_resolution_green_ai_hub():
    """Проверка генерации ответа через AI Hub с вызовом dispatch_routed_inference."""
    task = {
        "Id": 3322,
        "Name": "Установка шрифтов",
        "Description": "Требуется установить корпоративные шрифты",
        "ServiceId": 44,
    }

    mock_resp = RoutedInferenceResponse(
        circuit=DataCircuit.GREEN,
        model=settings.GEMINI_MODEL,
        text="Здравствуйте! Шрифты успешно установлены. Проверьте отображение.",
        sanitized_entities_count=0,
        cached=False,
        execution_time_ms=150.0,
    )

    with patch.object(
        __import__("app.services.ai_synthesis", fromlist=["ai_hub"]).ai_hub,
        "dispatch_routed_inference",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ) as mock_dispatch:
        resp = await synthesize_triage_resolution(
            task=task,
            circuit=DataCircuit.GREEN,
        )

        assert "Шрифты успешно установлены" in resp
        mock_dispatch.assert_called_once()


# ===========================================================================
# 4. Тест синхронизации закрытых заявок с канонизацией
# ===========================================================================


@pytest.mark.asyncio
async def test_sync_historical_closed_tasks_canonization():
    """Проверка интеграции канонизации при выгрузке закрытых заявок в RAG."""
    mock_db = AsyncMock(spec=AsyncSession)

    tasks_mock = [
        {
            "Id": 8801,
            "Name": "Срочно помогите!",
            "Description": "Не работает печать на HP LaserJet",
            "StatusId": 29,
            "StatusName": "Выполнена",
            "ServiceId": 44,
            "ServiceName": "03. Печать",
        }
    ]

    lifetime_mock = [
        {"Comment": "Статус изменен"},
        {"Comment": "Служба Spooler перезапущена, пробная страница напечатана."},
    ]

    with patch(
        "app.services.intraservice.get_tasks",
        new_callable=AsyncMock,
        return_value=tasks_mock,
    ), patch(
        "app.services.intraservice.get_task_lifetime",
        new_callable=AsyncMock,
        return_value=lifetime_mock,
    ), patch(
        "app.services.rag.index_task_knowledge",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_index:
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_res

        sync_res = await sync_historical_closed_tasks(
            auth_b64="bW9ja19hdXRo",
            db=mock_db,
            days=30,
            limit=5,
        )

        assert sync_res["indexed"] == 1
        mock_index.assert_called_once()

        call_kwargs = mock_index.call_args[1]
        assert call_kwargs["task_id"] == 8801
        assert "HP LaserJet" in call_kwargs["problem"]
        assert "Срочно помогите" not in call_kwargs["problem"]
        assert "Spooler" in call_kwargs["solution"]
        assert "root_cause" in call_kwargs["classification_data"]
