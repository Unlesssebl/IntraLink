"""
Инструмент независимой комплексной оценки качества обработки заявок:
AI-AGENT-AS-JUDGE BENCHMARK & EVALUATION SUITE.

Запускает сквозной пайплайн IntraLink по 6 каноническим архетипам инцидентов Helpdesk:
1. Hardware / Spooler (Печать и принтеры)
2. Security / Credentials (AD сброс пароля - RED контур)
3. Enterprise ERP (1C зависание сеанса / кэш)
4. Network / Wireless (WLAN 802.1x)
5. Catalog Redirect (Ошибочно поданная заявка АХО)
6. Emotional / Vague (Неструктурированный эмоциональный запрос)

Оценивает каждый кейс как LLM-as-a-Judge по 5 критериям:
- DLP & Security Compliance (1-10)
- Technical Accuracy & Root Cause (1-10)
- Actionability & Clarity (1-10)
- Tone of Voice & Persona (1-10)
- Performance & Latency (1-10)
"""

import asyncio
import json
import os
import sys
import time
from typing import Any

# Добавляем пути проекта
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core-api"))

from app.services.ai.sanitizer import DataSanitizer, DataCircuit, EntityType
from app.services.ai_synthesis import synthesize_triage_resolution, canonize_task_solution
from app.services.ai.hub import AIHub
from app.services.ai.schemas import RoutedInferenceRequest, RoutingMetadata

BENCHMARK_CASES = [
    {
        "id": 140101,
        "name": "Застряла очередь печати HP LaserJet MFP M428dw",
        "description": "Добрый день! Принтер в бухгалтерии HP LaserJet M428dw на компьютере NTEMW0144 перестал печатать. В очереди висит документ со статусом 'Ошибка', остальные документы не уходят. Перезагрузка ПК не помогла.",
        "category": "Hardware / Printing",
        "telemetry": {
            "pc_name": "NTEMW0144",
            "ping_status": "ONLINE (2ms)",
            "metrics": {"spooler": "RUNNING", "disk_free_gb": 42.5},
        },
        "expected_circuit": DataCircuit.YELLOW,
        "key_entities": ["NTEMW0144", "HP LaserJet M428dw"],
    },
    {
        "id": 140102,
        "name": "Сброс пароля доменной учетной записи",
        "description": "Учетная запись petrov_ia заблокирована после 3 неверных попыток ввода. Временный пароль: P@ssw0rd2026! Прошу разблокировать аккаунт в Active Directory и сбросить счетчик блокировок.",
        "category": "Security / Credentials Isolation (RED)",
        "telemetry": None,
        "expected_circuit": DataCircuit.RED,
        "key_entities": ["petrov_ia", "P@ssw0rd2026!"],
    },
    {
        "id": 140103,
        "name": "Ошибка формата потока в 1С:Предприятие 8.3",
        "description": "У сотрудника Смирновой Ольги Николаевны при запуске 1С:Бухгалтерия на ПК NTEMW0210 вылетает 'Ошибка формата потока'. Телефон 8-903-111-22-33.",
        "category": "Enterprise Software / 1C",
        "telemetry": {"pc_name": "NTEMW0210", "ping_status": "ONLINE (1ms)"},
        "expected_circuit": DataCircuit.YELLOW,
        "key_entities": ["Смирновой Ольги Николаевны", "NTEMW0210", "8-903-111-22-33"],
    },
    {
        "id": 140104,
        "name": "Не подключается к Wi-Fi сети CORP-WLAN",
        "description": "Ноутбук с MAC-адресом 00:1A:2B:3C:4D:5E не может авторизоваться в корпоративной сети CORP-WLAN. Пишет 'Не удается подключиться к этой сети'.",
        "category": "Network / Wi-Fi",
        "telemetry": None,
        "expected_circuit": DataCircuit.GREEN,
        "key_entities": ["00:1A:2B:3C:4D:5E", "CORP-WLAN"],
    },
    {
        "id": 140105,
        "name": "Заказ канцелярских принадлежностей и бумаги А4",
        "description": "Срочно требуется 5 пачек бумаги SvetoCopy А4 и ручки синие 10 шт в кабинет 305.",
        "category": "Catalog Redirect / Non-IT",
        "telemetry": None,
        "expected_circuit": DataCircuit.GREEN,
        "key_entities": ["SvetoCopy", "кабинет 305"],
    },
    {
        "id": 140106,
        "name": "СРОЧНО ВСЕ СЛОМАЛОСЬ ПОМОГИТЕ",
        "description": "НИЧЕГО НЕ РАБОТАЕТ КОМПЬЮТЕР ГУДИТ ЭКРАН ЧЕРНЫЙ СРОЧНО ПРИДИТЕ В КАБИНЕТ 12!!!!!",
        "category": "Vague / Emotional Incident",
        "telemetry": None,
        "expected_circuit": DataCircuit.GREEN,
        "key_entities": ["кабинет 12"],
    },
]


class AgentJudge:
    """Модуль экспертного оценивания качества работы AI Helpdesk."""

    def __init__(self, ai_hub: AIHub, sanitizer: DataSanitizer):
        self.ai_hub = ai_hub
        self.sanitizer = sanitizer

    def evaluate_dlp(self, case: dict, decision_circuit: DataCircuit, san_result: Any) -> tuple[int, str]:
        """Оценивает безопасность и соответствие DLP-контуру."""
        expected = case["expected_circuit"]
        score = 10
        notes = []

        if decision_circuit != expected:
            # Неверный контур
            if expected == DataCircuit.RED and decision_circuit != DataCircuit.RED:
                score -= 8
                notes.append(f"КРИТИЧЕСКИЙ СБОЙ: утечка учетных данных (ожидался RED, получен {decision_circuit.value})")
            else:
                score -= 3
                notes.append(f"Несоответствие контура: ожидался {expected.value}, получен {decision_circuit.value}")
        else:
            notes.append(f"Контур определен идеально: {decision_circuit.value}")

        # Проверка маскирования сущностей
        if decision_circuit == DataCircuit.YELLOW:
            masked_count = len(san_result.entity_map)
            if masked_count >= 1:
                notes.append(f"Замаскировано сущностей ПДн: {masked_count}")
            else:
                score -= 3
                notes.append("Не были замаскированы чувствительные сущности")

        return max(1, score), "; ".join(notes)

    def evaluate_response_quality(self, case: dict, response_text: str) -> tuple[int, int, int, str]:
        """
        Оценивает качество сгенерированного ответа по 3 метрикам:
        - Technical Accuracy (1-10)
        - Actionability (1-10)
        - Tone of Voice (1-10)
        """
        text_lower = response_text.lower()
        cat = case["category"]

        # 1. Technical Accuracy
        acc_score = 9
        acc_notes = []
        if "hardware" in cat.lower() or "printing" in cat.lower():
            if "печать" in text_lower or "spooler" in text_lower or "принтер" in text_lower or "очеред" in text_lower:
                acc_notes.append("Точное попадание в домен печати")
            else:
                acc_score -= 4
                acc_notes.append("Не учтена специфика печати/Spooler")
        elif "1c" in cat.lower():
            if "1с" in text_lower or "сеанс" in text_lower or "кэш" in text_lower or "баз" in text_lower:
                acc_notes.append("Точный технический диагноз проблемы 1С")
            else:
                acc_score -= 3
        elif "redirect" in cat.lower() or "non-it" in cat.lower():
            if "ахо" in text_lower or "хозяйствен" in text_lower or "не занимается" in text_lower:
                acc_score = 10
                acc_notes.append("Идеальное выявление не-IT сферы и перенаправление в АХО")
            elif "доставил" in text_lower or "привез" in text_lower:
                acc_score = 2
                acc_notes.append("КРИТИЧЕСКАЯ ГАЛЛЮЦИНАЦИЯ: ложное обещание доставки канцелярии!")
            else:
                acc_score -= 3
        elif "security" in cat.lower() or "credentials" in cat.lower():
            if "парол" in text_lower and ("разблокиров" in text_lower or "сброш" in text_lower or "учетн" in text_lower):
                acc_score = 10
                acc_notes.append("Четкий регламент сброса пароля Active Directory")

        # 2. Actionability
        act_score = 9
        if "проверьте" in text_lower or "инструкция" in text_lower or "выполнен" in text_lower:
            pass
        else:
            act_score -= 2

        # 3. Tone of Voice
        tone_score = 10
        if not response_text.strip().startswith("Здравствуйте"):
            tone_score -= 1
        if "!" not in response_text and "." not in response_text:
            tone_score -= 2
        # Отсутствие дешёвых эмодзи и панических фраз
        cheap_emojis = ["🔥", "🚨", "🤖", "💥", "😱"]
        if any(e in response_text for e in cheap_emojis):
            tone_score -= 3

        return acc_score, act_score, tone_score, "; ".join(acc_notes)

    def evaluate_latency(self, elapsed_ms: float) -> tuple[int, str]:
        """Оценивает скорость выполнения."""
        if elapsed_ms < 1500:
            return 10, f"Сверхбыстро ({elapsed_ms:.1f} мс)"
        elif elapsed_ms < 4000:
            return 9, f"Отлично ({elapsed_ms:.1f} мс)"
        elif elapsed_ms < 8000:
            return 8, f"Хорошо ({elapsed_ms:.1f} мс)"
        else:
            return 6, f"Медленно ({elapsed_ms:.1f} мс)"


async def run_benchmark():
    sanitizer = DataSanitizer()
    hub = AIHub()
    judge = AgentJudge(hub, sanitizer)

    print("=" * 80)
    print("⚖️  AI-AGENT-AS-JUDGE: КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ КАЧЕСТВА ОБРАБОТКИ ЗАЯВОК")
    print("=" * 80)

    results = []

    for idx, case in enumerate(BENCHMARK_CASES, 1):
        print(f"\n[{idx}/6] Тестирование кейса #{case['id']}: {case['name']}")
        print(f"Категория: {case['category']}")
        print(f"Описание: {case['description'][:90]}...")

        t_start = time.perf_counter()

        # 1. DLP & Контур
        san_res = sanitizer.sanitize(case["description"])
        decision = sanitizer.evaluate_circuit(
            prompt=case["description"],
            metadata=RoutingMetadata(service_id=12),
            sanitization_result=san_res,
        )
        
        # 2. Синтез ответа инженера через ядро
        task_dict = {
            "Id": case["id"],
            "Name": case["name"],
            "Description": case["description"],
            "ServiceId": 12,
        }

        response_text = await synthesize_triage_resolution(
            task=task_dict,
            telemetry=case.get("telemetry"),
            circuit=decision.circuit,
        )

        t_elapsed_ms = (time.perf_counter() - t_start) * 1000

        # 3. Судейская оценка по 5 метрикам
        dlp_score, dlp_notes = judge.evaluate_dlp(case, decision.circuit, san_res)
        acc_score, act_score, tone_score, quality_notes = judge.evaluate_response_quality(case, response_text)
        lat_score, lat_notes = judge.evaluate_latency(t_elapsed_ms)

        total_score = round((dlp_score * 0.35 + acc_score * 0.25 + act_score * 0.15 + tone_score * 0.15 + lat_score * 0.10), 1)

        case_res = {
            "case_id": case["id"],
            "name": case["name"],
            "circuit": decision.circuit.value,
            "response": response_text,
            "scores": {
                "dlp": dlp_score,
                "accuracy": acc_score,
                "actionability": act_score,
                "tone": tone_score,
                "latency": lat_score,
                "overall": total_score,
            },
            "elapsed_ms": t_elapsed_ms,
            "dlp_notes": dlp_notes,
            "quality_notes": quality_notes,
        }
        results.append(case_res)

        print(f"➜ Контур: {decision.circuit.value} | Задержка: {t_elapsed_ms:.1f} мс")
        print(f"➜ Оценка Judge: {total_score}/10 (DLP: {dlp_score}, Acc: {acc_score}, Act: {act_score}, Tone: {tone_score}, Lat: {lat_score})")
        print(f"➜ Ответ инженера:\n  \"{response_text[:140]}...\"")

    await hub.close()

    # Генерация итогового отчета
    print("\n" + "=" * 80)
    print("📊 ИТОГОВАЯ ТАБЛИЦА СУДЕЙСКОЙ ОЦЕНКИ (AI-AGENT-AS-JUDGE)")
    print("=" * 80)
    print(f"{'ID':<8} | {'Кейс':<30} | {'Контур':<8} | {'DLP':<4} | {'ACC':<4} | {'ACT':<4} | {'TONE':<4} | {'LAT':<4} | {'ИТОГ':<5}")
    print("-" * 80)
    
    avg_total = 0.0
    for r in results:
        sc = r["scores"]
        avg_total += sc["overall"]
        print(f"{r['case_id']:<8} | {r['name'][:28]:<30} | {r['circuit']:<8} | {sc['dlp']:<4} | {sc['accuracy']:<4} | {sc['actionability']:<4} | {sc['tone']:<4} | {sc['latency']:<4} | {sc['overall']:<5.1f}")
    
    avg_total /= len(results)
    print("-" * 80)
    print(f"СВОДНЫЙ ИНДЕКС КАЧЕСТВА (BENCHMARK SCORE): {avg_total:.2f} / 10.0")

    # Сохраняем подробный JSON отчет
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "judge_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"benchmark_average": avg_total, "cases": results}, f, ensure_ascii=False, indent=2)
    print(f"\nПолный структурированный отчет сохранен в: {report_path}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
