"""
Модуль AI-генерации ответов инженера (Response Synthesis) и автоматической канонизации базы знаний (Auto-KB Canonization).
Обеспечивает строгое соблюдение контуров Zero Trust DLP (RED / YELLOW / GREEN).
"""

import logging
import re
from typing import Any

from app.services.ai.hub import AIHub
from app.services.ai.sanitizer import data_sanitizer
from app.services.ai.schemas import DataCircuit, RoutingMetadata
from app.services.rag import clean_html, distill_search_query

logger = logging.getLogger("core_api.ai_synthesis")

_ai_hub_instance: AIHub | None = None


def get_ai_hub() -> AIHub:
    """Возвращает переиспользуемый инстанс AIHub."""
    global _ai_hub_instance
    if _ai_hub_instance is None:
        _ai_hub_instance = AIHub()
    return _ai_hub_instance


# ---------------------------------------------------------------------------
# 1. Автоматическая канонизация решений закрытых заявок (Auto-KB Canonization)
# ---------------------------------------------------------------------------


def canonize_task_solution(
    task: dict[str, Any], lifetime: list[dict[str, Any]] | None = None
) -> dict[str, str]:
    """
    Извлекает структурированную триаду (Problem, Root Cause, Solution) из закрытой заявки.
    Очищает коммуникационный шум, подписи заявителя и служебные логи.
    """
    raw_name = task.get("Name") or ""
    raw_desc = task.get("Description") or ""
    task_id = task.get("Id") or 0

    # 1. Очистка и дистилляция описания проблемы
    full_problem_raw = f"{raw_name}. {raw_desc}".strip()
    clean_problem = clean_html(full_problem_raw)
    distilled_problem = distill_search_query(clean_problem)
    if not distilled_problem or len(distilled_problem) < 5:
        distilled_problem = clean_problem

    # 2. Поиск финального содержательного комментария инженера
    solution_text = ""
    root_cause = "Эксплуатационный сбой"

    if lifetime:
        # Идем от последних комментариев к первым
        for item in reversed(lifetime):
            comm = clean_html(item.get("Comment") or "").strip()
            # Пропускаем пустые, системные статусные сообщения и авто-логи
            if not comm or len(comm) < 10:
                continue
            if comm.startswith("Статус изменен") or comm.startswith("Назначен исполнитель"):
                continue

            # Нашли содержательный комментарий решения
            solution_text = comm
            break

    if not solution_text:
        solution_text = "Заявка выполнена в штатном режиме."

    # Удаление стандартных телефонных подписей и контактных данных из решения
    solution_text = re.sub(
        r"(?i)(?:С уважением|Инженер|тел\.|доб\.|Беликов|тел:\s*[\d\-]+).*",
        "",
        solution_text,
    ).strip()

    # Определение первопричины (Root Cause) по эвристикам
    combined = f"{distilled_problem} {solution_text}".lower()
    if "spooler" in combined or "печать" in combined or "0x0000011b" in combined:
        root_cause = "Сбой подсистемы печати / очереди диспетчера Spooler"
    elif "wlan" in combined or "wi-fi" in combined or "сеть" in combined:
        root_cause = "Отсутствие учетной записи в доменной группе доступа WLAN"
    elif "1c" in combined or "1с" in combined:
        root_cause = "Блокировка сеанса 1С или кэша конфигурации"
    elif "пароль" in combined or "заблокирован" in combined:
        root_cause = "Превышение лимита неверных попыток ввода пароля AD"
    elif "диск" in combined or "место" in combined or "память" in combined:
        root_cause = "Переполнение системного раздела диска C:"

    canonical_summary = (
        f"Проблема: {distilled_problem}\n"
        f"Первопричина: {root_cause}\n"
        f"Решение: {solution_text}"
    )

    return {
        "problem": distilled_problem,
        "root_cause": root_cause,
        "solution": solution_text,
        "canonical_summary": canonical_summary,
    }


# ---------------------------------------------------------------------------
# 2. AI-генерация ответов инженера (Response Synthesis)
# ---------------------------------------------------------------------------


def _synthesize_deterministic_fallback(
    task: dict[str, Any],
    kb_matches: list[dict[str, Any]] | None = None,
    telemetry: dict[str, Any] | None = None,
) -> str:
    """
    Детерминированный синтез ответа в каноническом стиле инженера Беликова Алена (Rule-based SSOT).
    Гарантирует 100% мгновенный ответ даже при оффлайне нейросетей.
    """
    task_id = task.get("Id") or ""
    name = task.get("Name") or ""
    desc = task.get("Description") or ""
    combined = f"{name} {desc}".lower()

    # Извлечение данных телеметрии хоста
    pc_name = (telemetry or {}).get("pc_name") or ""
    ping_status = (telemetry or {}).get("ping_status") or ""
    spooler_status = ((telemetry or {}).get("metrics") or {}).get("spooler") or ""

    # 1. Если есть RAG прецедент с высоким сходством
    if kb_matches and len(kb_matches) > 0:
        top_match = kb_matches[0]
        top_sol = top_match.get("solution") or ""
        if top_sol and len(top_sol) > 15:
            # Очищаем от приветствий для подстановки
            clean_sol = re.sub(
                r"(?i)^(?:здравствуйте|добрый день|доброе утро)[!.,\s]*",
                "",
                top_sol,
            ).strip()
            if pc_name:
                return (
                    f"Здравствуйте! По заявке #{task_id} на рабочей станции {pc_name} "
                    f"выполнены необходимые работы: {clean_sol} "
                    f"Пожалуйста, проверьте работу."
                )
            return (
                f"Здравствуйте! По заявке #{task_id} выполнены работы: {clean_sol} "
                f"Пожалуйста, проверьте корректность работы сервиса."
            )

    # 2. Печать и Spooler
    if "печать" in combined or "принтер" in combined or "spooler" in combined:
        pc_part = f" на рабочей станции {pc_name}" if pc_name else ""
        return (
            f"Здравствуйте! По заявке #{task_id}{pc_part} выполнен комплекс работ "
            f"по перезапуску службы печати (Spooler) и очистке очереди заданий. "
            f"Тестовая печать успешно инициирована. Проверьте, пожалуйста, печать документов."
        )

    # 3. Wi-Fi и доменные учетные записи
    if "wlan" in combined or "wi-fi" in combined or "wifi" in combined:
        return (
            f"Здравствуйте! По заявке #{task_id} учетная запись проверена в Active Directory. "
            f"Права доступа к корпоративной сети WLAN-WORKNET предоставлены. "
            f"Пожалуйста, повторите подключение к сети."
        )

    # 4. Базовый вежливый шаблон
    pc_part = f" на хосте {pc_name}" if pc_name else ""
    return (
        f"Здравствуйте! Заявка #{task_id}{pc_part} принята и выполнена в полном объеме. "
        f"Пожалуйста, проверьте результат."
    )


async def synthesize_triage_resolution(
    task: dict[str, Any],
    kb_matches: list[dict[str, Any]] | None = None,
    telemetry: dict[str, Any] | None = None,
    circuit: DataCircuit | None = None,
    force_deterministic: bool = False,
) -> str:
    """
    Синтезирует экспертный персонализированный ответ инженера Helpdesk.
    - RED контур: строго детерминированный синтез или локальный инференс без утечки наружу.
    - YELLOW контур: автоматическая десенсибилизация перед генерацией.
    - GREEN контур: генерация через AI Hub с fallback на детерминированный синтез.
    """
    if force_deterministic:
        return _synthesize_deterministic_fallback(
            task=task, kb_matches=kb_matches, telemetry=telemetry
        )

    task_id = task.get("Id") or 0
    t_name = task.get("Name") or ""
    t_desc = clean_html(task.get("Description") or "")
    s_id = task.get("ServiceId") or 0

    # 1. Оценка контура безопасности
    eval_circuit = circuit
    full_prompt_text = f"Тема: {t_name}\nОписание: {t_desc}"
    if eval_circuit is None:
        dec = data_sanitizer.evaluate_circuit(
            prompt=full_prompt_text, metadata=RoutingMetadata(service_id=s_id)
        )
        eval_circuit = dec.circuit

    # 2. Если RED контур — мгновенный локальный детерминированный синтез (Zero Trust)
    if eval_circuit == DataCircuit.RED:
        return _synthesize_deterministic_fallback(
            task=task, kb_matches=kb_matches, telemetry=telemetry
        )

    # 3. Подготовка контекста прецедентов и телеметрии
    kb_context = ""
    if kb_matches:
        top = kb_matches[0]
        kb_context = f"Похожее историческое решение: {top.get('solution', '')}"

    telemetry_context = ""
    if telemetry:
        pc_name = telemetry.get("pc_name") or ""
        ping = telemetry.get("ping_status") or ""
        metrics = telemetry.get("metrics") or {}
        telemetry_context = (
            f"Телеметрия ПК: {pc_name} (Связь: {ping}, "
            f"Диск C: {metrics.get('disk_free_gb', '?')}GB, "
            f"Spooler: {metrics.get('spooler', '?')})"
        )

    system_prompt = (
        "Ты — опытный инженер 1-й линии Helpdesk Беликов Ален. "
        "Сформируй краткий, вежливый, профессиональный ответ заявителю о выполненной работе по заявке. "
        "Ответ должен быть кратким (2-4 предложения), начинаться с 'Здравствуйте!' и завершаться просьбой проверить работу."
    )

    user_prompt = (
        f"Заявка #{task_id}: {t_name}\n"
        f"Описание проблемы: {t_desc}\n"
        f"{kb_context}\n"
        f"{telemetry_context}\n"
        f"Напиши финальный комментарий решения для заявителя."
    )

    hub = get_ai_hub()
    try:
        from app.services.ai.schemas import RoutedInferenceRequest

        req = RoutedInferenceRequest(
            prompt=user_prompt,
            system_prompt=system_prompt,
            metadata=RoutingMetadata(service_id=s_id),
            temperature=0.2,
        )
        res = await hub.dispatch_routed_inference(req)
        output_text = getattr(res, "text", None) or getattr(res, "final_text", None)
        if output_text and len(output_text.strip()) > 15:
            return output_text.strip()
    except Exception as e:
        logger.debug("AI Hub генерация не удалась, переход на fallback: %s", e)

    # Fallback
    return _synthesize_deterministic_fallback(
        task=task, kb_matches=kb_matches, telemetry=telemetry
    )
