"""
Скрипт для живого (без моков) тестирования нейросетей:
1. Cloud Gemini (Google Generative AI API) с маскированием PII (YELLOW контур)
2. Локальная Ollama (RED контур)
Запуск: uv run python tools/test_live_ai.py
"""

import asyncio
import os
import sys
import time

# Добавляем пути к модулям
sys.path.insert(0, os.path.abspath("core-api"))
sys.path.insert(0, os.path.abspath("."))

from dotenv import load_dotenv

# Загружаем переменные из core-api/.env и .env
load_dotenv("core-api/.env")
load_dotenv(".env")

from app.config import settings
from app.services.ai.hub import ai_hub
from app.services.ai.schemas import RoutedInferenceRequest, RoutingMetadata
from tests.fixtures import get_mock_task_by_id


async def main():
    print("=" * 70)
    print("🚀 ЖИВОЙ ТЕСТ ИНФЕРЕНСА НЕЙРОСЕТЕЙ (БЕЗ МОКОВ / NO MOCKS)")
    print("=" * 70)

    # 1. Проверка конфигурации
    print(f"\n[Конфигурация]")
    print(f"• GEMINI_MODEL: {settings.GEMINI_MODEL}")
    key_status = "✅ Присутствует" if settings.GEMINI_API_KEY else "❌ Отсутствует"
    print(f"• GEMINI_API_KEY: {key_status}")
    print(f"• OLLAMA_BASE_URL: {settings.OLLAMA_BASE_URL}")
    print(f"• OLLAMA_MODEL: {settings.OLLAMA_MODEL}")

    # 2. Проверка статуса здоровья (Healthcheck и GPU детектор)
    print(f"\n[Проверка доступности и аппаратного ускорения]")
    health = await ai_hub.get_health()
    print(f"• Ollama ({health.ollama_url}) доступна: {'✅ ДА' if health.ollama_available else '⚠️ НЕТ (сервис не запущен)'}")
    if health.gpu_detected:
        gpu_info = f"{health.gpu_name} [{health.gpu_backend}]"
        if health.vram_allocated_bytes:
            vram_mb = round(health.vram_allocated_bytes / (1024 * 1024), 1)
            gpu_info += f" — VRAM: {vram_mb} MB"
        print(f"• Аппаратное GPU-ускорение: ⚡ {gpu_info}")
    else:
        print(f"• Аппаратное ускорение: ℹ️ CPU Fallback (Vulkan / DirectML / CUDA не обнаружены)")
    print(f"• Cloud/Gemini доступен: {'✅ ДА' if health.litellm_available else '❌ НЕТ'}")

    # 3. Тест живого вызова Cloud Gemini
    print("\n" + "-" * 70)
    print("🧪 ТЕСТ 1: ЖИВОЙ ВЫЗОВ CLOUD GEMINI (прямой запрос)")
    print("-" * 70)

    test_prompt = (
        "Ты инженер 1-й линии Helpdesk. Пользователь пишет: "
        "'Не печатает принтер HP LaserJet в бухгалтерии, в очереди висит документ'. "
        "Дай краткую (2-3 пункта) инструкцию первой помощи на русском языке."
    )
    print(f"Промпт:\n{test_prompt}\n")

    t0 = time.perf_counter()
    response = await ai_hub.generate_cloud_completion(
        prompt=test_prompt,
        system_prompt="Отвечай профессионально и лаконично как инженер техподдержки.",
        max_tokens=300,
        temperature=0.2,
    )
    dt = round((time.perf_counter() - t0) * 1000, 2)

    if response:
        print(f"✅ ОТВЕТ ОТ GEMINI (время ответа: {dt} мс):")
        print("-" * 40)
        print(response)
        print("-" * 40)
    else:
        print(f"❌ Gemini не вернула ответ (ошибка соединения или неверный ключ).")

    # 4. Тест сквозного контура с маскированием PII (YELLOW Circuit)
    print("\n" + "-" * 70)
    print("🧪 ТЕСТ 2: СКВОЗНОЙ ТЕСТ DLP + GEMINI (Мок-заявка #140004 с ПДн)")
    print("-" * 70)

    ticket = get_mock_task_by_id(140004)
    if ticket:
        raw_ticket_text = (
            f"Заявка #{ticket['Id']}: {ticket['Name']}\n"
            f"Описание: {ticket['Description']}\n\n"
            f"Напиши вежливый ответ заявителю с подтверждением, что заявка принята в работу."
        )
        print(f"Исходный текст заявки (содержит ФИО, IP 10.244.15.82, ПК NTEMW0144):\n{raw_ticket_text}\n")

        t0 = time.perf_counter()
        routed_resp = await ai_hub.dispatch_routed_inference(
            RoutedInferenceRequest(
                prompt=raw_ticket_text,
                system_prompt="Ты вежливый оператор Helpdesk IntraLink.",
                bypass_cache=True,
            )
        )
        dt = round((time.perf_counter() - t0) * 1000, 2)

        if routed_resp:
            print(f"• Определенный контур: {routed_resp.circuit.value.upper()}")
            print(f"• Использованная модель: {routed_resp.model}")
            print(f"• Замаскировано сущностей: {routed_resp.sanitized_entities_count}")
            print(f"• Время исполнения: {routed_resp.execution_time_ms} мс")
            print(f"\n✅ РЕЗУЛЬТАТ С ДЕАНОНИМИЗАЦИЕЙ (исходные данные возвращены на место):")
            print("-" * 40)
            print(routed_resp.text)
            print("-" * 40)
        else:
            print("❌ Сбой диспетчера routed inference.")

    # 5. Тест локальной Ollama (если доступна)
    print("\n" + "-" * 70)
    print("🧪 ТЕСТ 3: ЛОКАЛЬНАЯ НЕЙРОСЕТЬ OLLAMA (RED Circuit)")
    print("-" * 70)

    if health.ollama_available:
        print(f"Отправка запроса в Ollama ({settings.OLLAMA_MODEL})...")
        t0 = time.perf_counter()
        ollama_resp = await ai_hub.generate_ollama_completion(
            prompt="Пользователь забыл пароль от учетной записи Windows. Напиши 2 шага регламента первой линии Helpdesk по верификации личности и сбросу.",
            max_tokens=200,
        )
        dt = round((time.perf_counter() - t0) * 1000, 2)
        if ollama_resp:
            words = len(ollama_resp.split())
            approx_tokens = int(words * 1.3)
            tps = round(approx_tokens / (dt / 1000.0), 1) if dt > 0 else 0
            gpu_label = health.gpu_name or "GPU"
            print(f"✅ ОТВЕТ ОТ OLLAMA [{gpu_label}] (время: {dt} мс, ~{tps} токенов/сек):")
            print("-" * 40)
            print(ollama_resp)
            print("-" * 40)
        else:
            print("❌ Ollama вернула пустой ответ.")
    else:
        print(
            f"ℹ️ Сервер Ollama сейчас не запущен на {settings.OLLAMA_BASE_URL}.\n"
            f"Для запуска локальной модели выполните:\n"
            f"  ollama run {settings.OLLAMA_MODEL}\n"
            f"или в Docker:\n"
            f"  docker compose up -d ollama"
        )

    await ai_hub.close()
    print("\n" + "=" * 70)
    print("🏁 ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
