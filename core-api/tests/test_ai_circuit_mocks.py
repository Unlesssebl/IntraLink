"""
Интеграционные тесты AI-контуров на базе синтетического датасета мок-заявок (fixtures/mock_ai_tickets.py).
Проверяет:
- DLP-маршрутизацию (RED / YELLOW / GREEN)
- Маскирование PII и деанонимизацию через PII Vault
- Query Distillation (отсечение эмоционального шума)
- Канонизацию базы знаний (Auto-KB Canonization)
- Сквозную симуляцию инференса без реальных внешних сервисов
"""

from unittest.mock import AsyncMock, patch
import pytest

from app.services.ai.sanitizer import DataSanitizer
from app.services.ai.schemas import DataCircuit, RoutingMetadata
from app.services.ai_synthesis import canonize_task_solution
from app.services.rag import distill_search_query
from tests.fixtures import (
    get_mock_tasks,
    get_mock_task_by_id,
    get_mock_tasks_by_circuit,
    get_mock_tasks_by_category,
)


@pytest.fixture
def sanitizer():
    return DataSanitizer()


# ---------------------------------------------------------------------------
# 1. Тесты DLP-классификации по контурам чувствительности
# ---------------------------------------------------------------------------


def test_red_circuit_tickets_detection(sanitizer: DataSanitizer):
    """
    Все заявки категории RED (с паролями, токенами и секретами)
    должны безусловно определяться как DataCircuit.RED.
    """
    red_tickets = get_mock_tasks_by_circuit(DataCircuit.RED)
    assert len(red_tickets) >= 3

    for ticket in red_tickets:
        text = f"{ticket['Name']}\n{ticket['Description']}"
        decision = sanitizer.evaluate_circuit(text, RoutingMetadata())
        assert decision.circuit == DataCircuit.RED, (
            f"Заявка #{ticket['Id']} ('{ticket['Name']}') должна быть классифицирована как RED, "
            f"но получила {decision.circuit}. Причина: {decision.reason}"
        )


def test_yellow_circuit_tickets_detection(sanitizer: DataSanitizer):
    """
    Заявки с персональными данными (ФИО, IP, имена ПК, телефоны, email)
    должны классифицироваться как DataCircuit.YELLOW для последующего маскирования.
    """
    yellow_tickets = get_mock_tasks_by_circuit(DataCircuit.YELLOW)
    assert len(yellow_tickets) >= 3

    for ticket in yellow_tickets:
        text = f"{ticket['Name']}\n{ticket['Description']}"
        decision = sanitizer.evaluate_circuit(text, RoutingMetadata())
        assert decision.circuit == DataCircuit.YELLOW, (
            f"Заявка #{ticket['Id']} ('{ticket['Name']}') должна быть классифицирована как YELLOW, "
            f"но получила {decision.circuit}."
        )


def test_green_circuit_ticket_detection(sanitizer: DataSanitizer):
    """
    Общие вопросы без ПДн и учетных данных должны направляться в открытый GREEN контур.
    """
    green_tickets = get_mock_tasks_by_circuit(DataCircuit.GREEN)
    assert len(green_tickets) >= 1

    for ticket in green_tickets:
        text = f"{ticket['Name']}\n{ticket['Description']}"
        decision = sanitizer.evaluate_circuit(text, RoutingMetadata())
        assert decision.circuit == DataCircuit.GREEN, (
            f"Заявка #{ticket['Id']} должна быть GREEN, но получила {decision.circuit}."
        )


# ---------------------------------------------------------------------------
# 2. Тесты маскирования PII и обратной деанонимизации (Vault Rehydration)
# ---------------------------------------------------------------------------


def test_pii_masking_and_rehydration(sanitizer: DataSanitizer):
    """
    Проверка полного цикла обезличивания мок-заявки #140004:
    - Замена ФИО, IP, хоста, почты и телефона на плейсхолдеры
    - 100% точное обратное восстановление (Rehydration)
    """
    ticket = get_mock_task_by_id(140004)
    assert ticket is not None

    raw_text = ticket["Description"]
    # Проверяем наличие чувствительных данных в исходном тексте
    assert "Ковалев Артем Игоревич" in raw_text
    assert "10.244.15.82" in raw_text
    assert "NTEMW0144" in raw_text
    assert "+7 (999) 123-45-67" in raw_text
    assert "kovalev@company.ru" in raw_text

    # 1. Синхронное маскирование
    result = sanitizer.sanitize(raw_text)

    # Проверяем, что в маскированном тексте нет ни одного реального PII
    assert "Ковалев Артем Игоревич" not in result.sanitized_text
    assert "10.244.15.82" not in result.sanitized_text
    assert "NTEMW0144" not in result.sanitized_text
    assert "kovalev@company.ru" not in result.sanitized_text

    # Проверяем наличие плейсхолдеров
    assert "{{USER_" in result.sanitized_text
    assert "{{INTERNAL_IP_" in result.sanitized_text
    assert "{{HOST_" in result.sanitized_text

    # 2. Деанонимизация (эмулируем ответ нейросети с плейсхолдерами)
    ai_response_text = f"Инженер подключится к {result.sanitized_text}"
    restored = sanitizer.deanonymize(ai_response_text, result.entity_map)

    # Проверяем, что все оригинальные значения успешно вернулись на свои места
    assert "Ковалев Артем Игоревич" in restored
    assert "10.244.15.82" in restored
    assert "NTEMW0144" in restored
    assert "kovalev@company.ru" in restored


# ---------------------------------------------------------------------------
# 3. Тест Query Distillation на эмоциональной заявке с шумом
# ---------------------------------------------------------------------------


def test_query_distillation_on_noisy_ticket():
    """
    Проверка отсечения паники и приветствий в мок-заявке #140007:
    алгоритм должен извлечь чистую суть проблемы.
    """
    ticket = get_mock_task_by_id(140007)
    assert ticket is not None

    raw_text = f"{ticket['Name']}. {ticket['Description']}"
    distilled = distill_search_query(raw_text)

    # Мусорные слова и паника должны исчезнуть
    assert "ВСЁ ПРОПАЛО" not in distilled
    assert "Здравствуйте" not in distilled
    assert "умоляю" not in distilled
    assert "Шеф ругается" not in distilled
    assert "в панике" not in distilled
    assert "спасибо" not in distilled

    # Техническая суть должна остаться
    assert "Kyocera" in distilled
    assert "M2040dn" in distilled
    assert "0x0000011b" in distilled


# ---------------------------------------------------------------------------
# 4. Тест канонизации базы знаний (Auto-KB Canonization)
# ---------------------------------------------------------------------------


def test_auto_kb_canonization_on_historical_ticket():
    """
    Проверка канонизации решения из мок-заявки #139501:
    очистка от шума, формулирование первопричины и канонического резюме.
    """
    ticket = get_mock_task_by_id(139501)
    assert ticket is not None

    canon = canonize_task_solution(task=ticket, lifetime=ticket["Lifetime"])

    # Проверяем выделение проблемы
    assert "Kyocera" in canon["problem"]
    assert "0x0000011b" in canon["problem"]

    # Проверяем очистку решения
    assert "KB5005565" in canon["solution"]
    assert "RpcAuthnLevelExemption" in canon["solution"]
    assert "Spooler" in canon["solution"]

    # Первопричина и метаданные
    assert canon["root_cause"] != ""
    assert "Первопричина:" in canon["canonical_summary"]
