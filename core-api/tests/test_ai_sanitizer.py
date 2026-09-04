"""
Тесты для модуля десенсибилизации (DLP/PII Sanitizer) и классификатора контуров.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.ai import (
    DataCircuit,
    EntityType,
    RoutingMetadata,
    data_sanitizer,
)


def test_sanitize_ip_addresses():
    """Проверка маскирования внутренних и публичных IP-адресов."""
    text = "Проверьте пинг до хоста 10.244.12.55 и шлюза 192.168.1.1, а также внешний 8.8.8.8"
    result = data_sanitizer.sanitize(text)

    assert "{{INTERNAL_IP_" in result.sanitized_text
    assert "10.244.12.55" not in result.sanitized_text
    assert "192.168.1.1" not in result.sanitized_text
    assert "8.8.8.8" not in result.sanitized_text
    assert EntityType.IP_ADDRESS.value in result.detected_types

    # Проверяем обратную деанонимизацию
    restored = data_sanitizer.deanonymize(result.sanitized_text, result.entity_map)
    assert restored == text


def test_sanitize_hostnames_and_users():
    """Проверка маскирования рабочих станций и ФИО сотрудников."""
    text = "Сотрудник Иванов Иван Иванович на ПК NTEMW0144 не может открыть 1С"
    result = data_sanitizer.sanitize(text)

    assert "Иванов Иван Иванович" not in result.sanitized_text
    assert "NTEMW0144" not in result.sanitized_text
    assert "{{USER_1}}" in result.sanitized_text
    assert "{{HOST_1}}" in result.sanitized_text
    assert EntityType.USER_NAME.value in result.detected_types
    assert EntityType.HOSTNAME.value in result.detected_types

    restored = data_sanitizer.deanonymize(result.sanitized_text, result.entity_map)
    assert restored == text


def test_sanitize_contacts_and_credentials():
    """Проверка маскирования email, телефонов и учетных данных."""
    text = "Связаться: support@company.ru или +7 (999) 123-45-67, временный пароль: TempPass2026!"
    result = data_sanitizer.sanitize(text)

    assert "support@company.ru" not in result.sanitized_text
    assert "+7 (999) 123-45-67" not in result.sanitized_text
    assert "TempPass2026!" not in result.sanitized_text
    assert "{{EMAIL_1}}" in result.sanitized_text
    assert "{{PHONE_1}}" in result.sanitized_text
    assert "{{CREDENTIAL_1}}" in result.sanitized_text

    restored = data_sanitizer.deanonymize(result.sanitized_text, result.entity_map)
    assert restored == text


def test_deanonymize_whitespace_tolerant():
    """Проверка толерантности деанонимизации к модификациям токенов от LLM."""
    entity_map = {
        "{{USER_1}}": "Сидоров Алексей",
        "{{INTERNAL_IP_1}}": "10.0.0.45",
    }
    llm_output = "Пользователь {{ USER_1 }} успешно подключился к серверу {INTERNAL_IP_1}."
    restored = data_sanitizer.deanonymize(llm_output, entity_map)

    assert "Сидоров Алексей" in restored
    assert "10.0.0.45" in restored
    assert "{{ USER_1 }}" not in restored
    assert "{INTERNAL_IP_1}" not in restored


def test_evaluate_circuit_red_by_credentials():
    """При наличии паролей в тексте или метаданных выбирается строго закрытый контур RED."""
    meta = RoutingMetadata(contains_credentials=True)
    decision = data_sanitizer.evaluate_circuit("Сброс пароля для почты", meta)
    assert decision.circuit == DataCircuit.RED
    assert decision.target_backend == "ollama"

    # Проверка пароля в теле текста
    meta_clean = RoutingMetadata()
    decision_pass = data_sanitizer.evaluate_circuit("Пользователь ввел пароль: SuperSecret123", meta_clean)
    assert decision_pass.circuit == DataCircuit.RED


def test_evaluate_circuit_yellow_by_pii():
    """При наличии PII или внутренних IP выбирается трансформируемый контур YELLOW."""
    meta = RoutingMetadata()
    prompt = "Настроить принтер для сотрудника Петров Петр Петрович на ПК NTEMW0099"
    decision = data_sanitizer.evaluate_circuit(prompt, meta)

    assert decision.circuit == DataCircuit.YELLOW
    assert decision.requires_sanitization is True
    assert decision.target_backend == "litellm_gemini"


def test_evaluate_circuit_green_clean():
    """При отсутствии чувствительных сущностей выбирается открытый контур GREEN."""
    meta = RoutingMetadata()
    prompt = "Как написать PowerShell скрипт для циклической очистки временных файлов в C:\\Windows\\Temp?"
    decision = data_sanitizer.evaluate_circuit(prompt, meta)

    assert decision.circuit == DataCircuit.GREEN
    assert decision.requires_sanitization is False
    assert decision.target_backend == "litellm_gemini"


def test_evaluate_circuit_force_override():
    """Принудительный force_circuit переопределяет автоматическое решение."""
    meta = RoutingMetadata(force_circuit=DataCircuit.RED)
    decision = data_sanitizer.evaluate_circuit("Как очистить кэш браузера?", meta)
    assert decision.circuit == DataCircuit.RED


def test_force_green_cannot_downgrade_sensitive_data():
    decision = data_sanitizer.evaluate_circuit(
        "Временный пароль: SecretPass2026!",
        RoutingMetadata(force_circuit=DataCircuit.GREEN),
    )
    assert decision.circuit == DataCircuit.RED


@pytest.mark.asyncio
async def test_redis_vault_save_and_load():
    """Проверка сохранения и загрузки маппинга из Redis PII Vault."""
    session_id = "test-session-12345"
    mapping = {"{{USER_1}}": "Иванов Иван", "{{IP_1}}": "192.168.1.100"}

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.get = AsyncMock(return_value='{"{{USER_1}}": "Иванов Иван", "{{IP_1}}": "192.168.1.100"}')

    with patch("app.services.ai.sanitizer.get_redis_client", return_value=mock_redis):
        save_ok = await data_sanitizer.save_vault(session_id, mapping, ttl_sec=300)
        assert save_ok is True
        mock_redis.set.assert_called_once()

        loaded = await data_sanitizer.load_vault(session_id)
        assert loaded["{{USER_1}}"] == "Иванов Иван"
        assert loaded["{{IP_1}}"] == "192.168.1.100"


def test_sanitize_empty_and_none_handling():
    """Проверка корректной обработки пустых строк."""
    res_empty = data_sanitizer.sanitize("")
    assert res_empty.sanitized_text == ""
    assert res_empty.entity_map == {}
    assert res_empty.detected_types == []

    deanon = data_sanitizer.deanonymize("", {})
    assert deanon == ""


def test_sanitize_duplicate_entities():
    """Проверка, что повторяющиеся сущности не создают дублирующихся токенов."""
    text = "Хост NTEMW0144 недоступен. Проверьте NTEMW0144 снова."
    res = data_sanitizer.sanitize(text)

    # Должен быть только один плейсхолдер {{HOST_1}} для NTEMW0144
    assert "{{HOST_1}}" in res.sanitized_text
    assert "{{HOST_2}}" not in res.sanitized_text
    assert len(res.entity_map) == 1
    assert data_sanitizer.deanonymize(res.sanitized_text, res.entity_map) == text


def test_sanitize_overlapping_ips():
    """Проверка защиты от коллизий подстрок при перекрывающихся IP адресах."""
    text = "Сервер 10.244.12.50 и шлюз 10.244.12.5"
    res = data_sanitizer.sanitize(text)

    # Оба IP должны быть корректно заменены без повреждения подстрок
    assert "10.244.12.50" not in res.sanitized_text
    assert "10.244.12.5" not in res.sanitized_text
    restored = data_sanitizer.deanonymize(res.sanitized_text, res.entity_map)
    assert restored == text


@pytest.mark.asyncio
async def test_redis_vault_graceful_error_handling():
    """Проверка устойчивости PII Vault при сбоях Redis."""
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=Exception("Redis Connection Refused"))
    mock_redis.get = AsyncMock(side_effect=Exception("Redis Connection Refused"))

    with patch("app.services.ai.sanitizer.get_redis_client", return_value=mock_redis):
        save_ok = await data_sanitizer.save_vault("sess-err", {"{{KEY}}": "val"})
        assert save_ok is False

        loaded = await data_sanitizer.load_vault("sess-err")
        assert loaded == {}

