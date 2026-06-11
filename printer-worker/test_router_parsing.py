import sys
import os
import json

# Добавляем путь printer-worker в pythonpath
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.schemas import KnowledgeBase, PrintJob
from orchestrator.router import JobRouter


async def test_router_parsing():
    print("=== Запуск тестов парсинга адресов принтеров ===")

    # 1. Загрузка базы знаний
    kb_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "knowledge_base",
        "printers_knowledge_base.json",
    )
    with open(kb_path, "r", encoding="utf-8") as f:
        kb_data = json.load(f)

    kb = KnowledgeBase.model_validate(kb_data)
    print(f"Загруженные префиксы: {kb.printer_name_prefixes}")
    assert kb.printer_name_prefixes == ["ittp", "kzmp", "kmkp"], (
        "Неверные префиксы в БЗ"
    )

    router = JobRouter(kb)

    # 2. Тестирование _parse_printer_address
    test_cases = [
        # (входной текст, ожидаемый адрес)
        ("Установите принтер на 192.168.1.100", "192.168.1.100"),
        ("Подключите принтер ittp0000 для отдела бухгалтерии", "ittp0000"),
        ("Новый принтер kzmp0012 на складе", "kzmp0012"),
        ("Принтер kmkp-test-01", "kmkp-test-01"),
        ("Имя принтера ITTP9999 в верхнем регистре", "ittp9999"),
        ("Имя принтера KzMp-8888 в смешанном регистре", "kzmp-8888"),
        ("Неизвестный префикс abcd1234", None),
        ("Просто текст без принтеров и IP", None),
        ("В тексте есть PC-1234, но нет принтера", None),
    ]

    for text, expected in test_cases:
        result = router._parse_printer_address(text)
        print(f"Текст: {text!r} -> Ожидалось: {expected!r}, Получено: {result!r}")
        assert result == expected, (
            f"Ошибка парсинга для текста: {text!r}. Ожидалось {expected!r}, получено {result!r}"
        )

    print("Все тесты парсинга успешно пройдены!")

    # 3. Тестирование Fast-Track
    print("=== Тестирование Fast-Track ===")
    job = PrintJob(
        task_id=123,
        tg_user_id=456,
        raw_text="Установить принтер ittp0024 на компьютер PC-ADMIN",
        target_pc="PC-ADMIN",
        model_key="kyocera_ecosys_m2040dn",
    )

    routed_job = await router.route(job)
    print("Результат Fast-Track:")
    print(f"  model_key: {routed_job.model_key}")
    print(f"  connection_type: {routed_job.connection_type}")
    print(f"  printer_address: {routed_job.printer_address}")

    assert routed_job.printer_address == "ittp0024", (
        f"Неверный printer_address: {routed_job.printer_address}"
    )
    assert routed_job.driver_info is not None, "Драйвер не найден"
    assert routed_job.driver_info.model_key == "kyocera_ecosys_m2040dn", (
        "Неверный драйвер"
    )

    print("Fast-Track успешно протестирован!")

    # 4. Тестирование SNMP Auto-Discovery
    print("=== Тестирование SNMP Auto-Discovery ===")
    from unittest.mock import patch

    job_snmp = PrintJob(
        task_id=124,
        tg_user_id=456,
        raw_text="Установить сетевой принтер на компьютер ITT-USER",
        target_pc="ITT-USER",
        model_key="-",
        printer_address="192.168.1.50",
    )

    with patch("orchestrator.snmp.probe_printer_model") as mock_probe:
        mock_probe.return_value = "Kyocera ECOSYS M2040dn"

        routed_job_snmp = await router.route(job_snmp)

        print("Результат SNMP Auto-Discovery:")
        print(f"  model_key: {routed_job_snmp.model_key}")
        print(f"  connection_type: {routed_job_snmp.connection_type}")
        print(f"  printer_address: {routed_job_snmp.printer_address}")

        assert routed_job_snmp.model_key == "kyocera_ecosys_m2040dn", (
            f"Неверная модель: {routed_job_snmp.model_key}"
        )
        assert routed_job_snmp.connection_type == "tcpip", (
            f"Неверный тип подключения: {routed_job_snmp.connection_type}"
        )
        assert routed_job_snmp.driver_info is not None, "Драйвер должен быть найден"
        assert routed_job_snmp.driver_info.model_key == "kyocera_ecosys_m2040dn"

    print("=== Тестирование Приоритета SNMP над Fast-Track ===")
    job_priority = PrintJob(
        task_id=125,
        tg_user_id=456,
        raw_text="Установить принтер ittp-wrong на PC-TEST",
        target_pc="PC-TEST",
        model_key="wrong_model_key",
        printer_address="192.168.1.60",
    )

    with patch("orchestrator.snmp.probe_printer_model") as mock_probe:
        # SNMP находит правильную модель
        mock_probe.return_value = "Kyocera ECOSYS M2040dn"

        routed_job_priority = await router.route(job_priority)

        print("Результат теста приоритета:")
        print(
            f"  model_key (должен быть kyocera_ecosys_m2040dn): {routed_job_priority.model_key}"
        )

        assert routed_job_priority.model_key == "kyocera_ecosys_m2040dn", (
            f"SNMP должен был переопределить модель! Получено: {routed_job_priority.model_key}"
        )

    print("Приоритет SNMP успешно подтвержден!")


def test_llm_parse_result_null_handling():
    from orchestrator.schemas import LLMParseResult

    # Этот тест проверяет, что строки "null" или "none" корректно преобразуются
    # в None / unknown и не вызывают ValidationError при валидации
    payload = {
        "target_pc": "null",
        "model_key": "none",
        "connection_type": "null",
        "printer_address": "null",
        "confidence": "null",
    }
    result = LLMParseResult.model_validate(payload)
    assert result.target_pc == ""
    assert result.model_key == "unknown"
    assert result.connection_type is None
    assert result.printer_address is None
    assert result.confidence == 0.0
    print("Тест валидации 'null' и 'none' пройден!")


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_router_parsing())
    test_llm_parse_result_null_handling()
