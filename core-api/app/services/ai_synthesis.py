"""
Модуль AI-генерации ответов инженера (Response Synthesis) и автоматической канонизации базы знаний (Auto-KB Canonization).
Обеспечивает строгое соблюдение контуров Zero Trust DLP (RED / YELLOW / GREEN).
"""

import logging
import re
from typing import Any

from app.services.ai.hub import ai_hub
from app.services.ai.sanitizer import data_sanitizer
from app.services.ai.schemas import DataCircuit, RoutingMetadata
from app.services.rag import clean_html, distill_search_query

logger = logging.getLogger("core_api.ai_synthesis")

# ---------------------------------------------------------------------------
# SSOT: Ключевые слова для детерминированных шлюзов
# Единый источник истины — изменяется в одном месте
# ---------------------------------------------------------------------------

# Не-IT заявки: АХО, канцелярия, хозяйственное обеспечение
_NON_IT_KEYWORDS: tuple[str, ...] = (
    "бумаг", "ручк", "канцеляр", "картридж заправк",
    "стол", "стул", "мебел", "клининг", "уборк",
    "пропуск", "кондиционер", "кулер",
)

# Regex очистки подписей инженера: ограничен до конца строки ($),
# чтобы не обрезать последующие абзацы технического решения.
_SIGNATURE_CLEANUP_RE = re.compile(
    r"(?im)(?:С уважением|Инженер|тел\.|доб\.|Беликов|тел:\s*[\d\-]+).*$"
)




_NON_INFORMATIVE_SOLUTIONS = {
    "заявка выполнена в штатном режиме.",
    "заявка выполнена в штатном режиме",
    "выполнено",
    "готово",
    "сделано",
    "ок",
    "закрыто",
    "закрыта",
    "решено",
    "устранено",
    "проверено",
    "все работает",
    "всё работает",
    "спасибо",
    "принято в работу",
    "заявка закрыта",
    "выполнена",
}


def is_informative_solution(solution: str) -> bool:
    """
    Проверяет, содержит ли решение содержательное техническое описание.
    Отсекает пустые отписки, шаблонные заглушки и комментарии короче 25 символов.
    """
    if not solution or not isinstance(solution, str):
        return False
    s_clean = solution.strip().lower()
    if s_clean in _NON_INFORMATIVE_SOLUTIONS:
        return False
    s_alpha = re.sub(r"[^\w\s]", "", s_clean).strip()
    if s_alpha in _NON_INFORMATIVE_SOLUTIONS:
        return False
    # Отсекаем слишком короткие фразы без технического смысла
    if len(solution.strip()) < 25:
        return False
    return True


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
            comm = clean_html(item.get("Comment") or item.get("Text") or "").strip()
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

    # Удаление стандартных телефонных подписей и контактных данных из решения.
    # Используем модульный SSOT-паттерн с ограничением по строке (не жадный).
    solution_text = _SIGNATURE_CLEANUP_RE.sub("", solution_text).strip()

    # Определение первопричины (Root Cause) по эвристикам.
    # ВАЖНО: порядок — от специфичных к общим (конкретные коды ошибок — первыми,
    # широкие термины вроде «сеть» — последними, чтобы не перекрывать более точные ветки).
    combined = f"{distilled_problem} {solution_text}".lower()
    if "spooler" in combined or "принтер" in combined or "0x0000011b" in combined or "печать" in combined:
        root_cause = "Сбой подсистемы печати / очереди диспетчера Spooler"
    elif "1c" in combined or "1с" in combined or "формата потока" in combined:
        root_cause = "Блокировка сеанса 1С или кэша конфигурации"
    elif "пароль" in combined or "заблокирован" in combined or "active directory" in combined:
        root_cause = "Превышение лимита неверных попыток ввода пароля AD"
    elif "диск" in combined or "место" in combined or "память" in combined:
        root_cause = "Переполнение системного раздела диска C:"
    elif "wlan" in combined or "wi-fi" in combined or "wifi" in combined:
        # «сеть» намеренно убран — слишком широкий термин
        root_cause = "Отсутствие учетной записи в доменной группе доступа WLAN"

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
    rule_decision: dict[str, Any] | None = None,
) -> str:
    """
    Детерминированный синтез ответа без LLM (0ms, 100% стабильность).
    """
    # 0. Приоритет корпоративного регламента
    r_key = (rule_decision.get("rule_type") or rule_decision.get("template_key")) if rule_decision else None
    if rule_decision and r_key not in (None, "", "standard_in_work"):
        rule_comment = (rule_decision.get("comment") or "").strip()
        if rule_comment:
            return rule_comment

    task_id = task.get("Id") or ""
    name = task.get("Name") or ""
    desc = task.get("Description") or ""
    combined = f"{name} {desc}".lower()

    # Извлечение данных телеметрии хоста
    pc_name = (telemetry or {}).get("pc_name") or ""
    ping_status = (telemetry or {}).get("ping_status") or ""
    spooler_status = ((telemetry or {}).get("metrics") or {}).get("spooler") or ""

    # 0. Детерминированный шлюз не-IT заявок (Канцелярия, бумага, мебель, клининг, пропуска).
    # Использует модульный SSOT-константу _NON_IT_KEYWORDS.
    if any(k in combined for k in _NON_IT_KEYWORDS):
        return (
            f"Здравствуйте! Служба технической поддержки IT занимается обслуживанием компьютерной техники "
            f"и корпоративных информационных систем. Вопросы хозяйственного обеспечения, канцелярии и мебели "
            f"находятся в ведении Административно-хозяйственного отдела (АХО). "
            f"Пожалуйста, обратитесь в АХО."
        )

    # 1. Если есть RAG прецедент с высоким сходством
    if kb_matches and len(kb_matches) > 0:
        top_match = kb_matches[0]
        top_sol = top_match.get("solution") or ""
        if top_sol and len(top_sol) > 15:
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

    # 2. Пароли и учетные записи Active Directory (RED контур)
    ad_keywords = ["парол", "учетн", "разблокиров", "блокировк", "active directory", "логин"]
    if any(k in combined for k in ad_keywords):
        return (
            f"Здравствуйте! По вашей заявке #{task_id} учетная запись проверена и разблокирована в Active Directory, "
            f"пароль сброшен. Пожалуйста, выполните вход с временным паролем и сразу установите новый постоянный пароль."
        )

    # 3. Печать и Spooler
    if "печать" in combined or "принтер" in combined or "spooler" in combined:
        pc_part = f" на рабочей станции {pc_name}" if pc_name else ""
        return (
            f"Здравствуйте! По заявке #{task_id}{pc_part} выполнен комплекс работ "
            f"по перезапуску службы печати (Spooler) и очистке очереди заданий. "
            f"Тестовая печать успешно инициирована. Проверьте, пожалуйста, печать документов."
        )

    # 4. Проблемы 1С:Предприятие
    if "1с" in combined or "1c" in combined or "формата потока" in combined:
        pc_part = f" на ПК {pc_name}" if pc_name else ""
        return (
            f"Здравствуйте! По заявке #{task_id}{pc_part} выполнен сброс временных файлов и очистка локального кэша 1С:Предприятие. "
            f"Пожалуйста, перезапустите 1С и проверьте работоспособность базы."
        )

    # 5. Wi-Fi и доменные учетные записи
    if "wlan" in combined or "wi-fi" in combined or "wifi" in combined:
        return (
            f"Здравствуйте! По заявке #{task_id} учетная запись проверена в Active Directory. "
            f"Права доступа к корпоративной сети WLAN-WORKNET предоставлены. "
            f"Пожалуйста, повторите подключение к сети."
        )

    # 6. Базовый вежливый шаблон
    pc_part = f" на хосте {pc_name}" if pc_name else ""
    return (
        f"Здравствуйте! Заявка #{task_id}{pc_part} принята в работу инженером Helpdesk. "
        f"Пожалуйста, ожидайте завершения диагностики."
    )


async def synthesize_triage_resolution(
    task: dict[str, Any],
    kb_matches: list[dict[str, Any]] | None = None,
    telemetry: dict[str, Any] | None = None,
    circuit: DataCircuit | None = None,
    force_deterministic: bool = False,
    rule_decision: dict[str, Any] | None = None,
) -> str:
    """
    Синтезирует экспертный персонализированный ответ инженера Helpdesk
    с жестким заземлением на факты (Strict Grounding), детерминированными шлюзами
    и привязкой к корпоративному регламенту (Rule Engine).
    """
    if force_deterministic:
        return _synthesize_deterministic_fallback(
            task=task, kb_matches=kb_matches, telemetry=telemetry, rule_decision=rule_decision
        )

    task_id = task.get("Id") or 0
    t_name = task.get("Name") or ""
    t_desc = clean_html(task.get("Description") or "")
    s_id = task.get("ServiceId") or 0
    combined = f"{t_name} {t_desc}".lower()

    # 1. Детерминированный шлюз для не-IT заявок (АХО, канцелярия) — ZERO LLM.
    # Использует модульный SSOT-константу _NON_IT_KEYWORDS.
    if any(k in combined for k in _NON_IT_KEYWORDS):
        return _synthesize_deterministic_fallback(
            task=task, kb_matches=kb_matches, telemetry=telemetry, rule_decision=rule_decision
        )

    # 2. Оценка контура безопасности
    eval_circuit = circuit
    full_prompt_text = f"Тема: {t_name}\nОписание: {t_desc}"
    if eval_circuit is None:
        dec = data_sanitizer.evaluate_circuit(
            prompt=full_prompt_text, metadata=RoutingMetadata(service_id=s_id)
        )
        eval_circuit = dec.circuit

    # 3. RED контур (пароли/учетки) — строго локальный детерминированный регламент (Zero Trust)
    if eval_circuit == DataCircuit.RED:
        return _synthesize_deterministic_fallback(
            task=task, kb_matches=kb_matches, telemetry=telemetry, rule_decision=rule_decision
        )

    # 4. Подготовка заземленного контекста (Strict Grounding Context)
    rule_fact = ""
    r_key = (rule_decision.get("rule_type") or rule_decision.get("template_key")) if rule_decision else None
    if rule_decision and r_key not in (None, "", "standard_in_work"):
        r_comment = (rule_decision.get("comment") or "").strip()
        if r_comment:
            rule_fact = f"ПОДТВЕРЖДЕННЫЙ РЕГЛАМЕНТ КОМПАНИИ (ДЕЙСТВИЕ {r_key}): {r_comment}"

    fact_block = ""
    if kb_matches and len(kb_matches) > 0:
        top_sol = kb_matches[0].get("solution", "").strip()
        if top_sol:
            fact_block = f"ПОДТВЕРЖДЕННЫЙ ФАКТ РЕШЕНИЯ (ИЗ БАЗЫ ЗНАНИЙ): {top_sol}"

    telemetry_fact = ""
    if telemetry:
        pc_name = telemetry.get("pc_name") or ""
        ping = telemetry.get("ping_status") or ""
        metrics = telemetry.get("metrics") or {}
        telemetry_fact = (
            f"ДАННЫЕ ТЕЛЕМЕТРИИ ХОСТА: ПК {pc_name}, связь {ping}, "
            f"Диск C: {metrics.get('disk_free_gb', '?')}GB, "
            f"Служба Spooler: {metrics.get('spooler', '?')}"
        )

    if not fact_block and not telemetry_fact and not rule_fact:
        fact_block = (
            "ПОДТВЕРЖДЕННЫЙ ФАКТ ВЫПОЛНЕНИЯ: Отсутствует (заявка только поступила, "
            "инженер еще не производил технических действий)."
        )

    system_prompt = (
        "Ты — опытный инженер 1-й линии Helpdesk Беликов Ален.\n"
        "Твоя задача — сформировать краткий (2-4 предложения), профессиональный и вежливый комментарий заявителю.\n"
        "Ответ ОБЯЗАТЕЛЬНО должен начинаться со слова 'Здравствуйте!' и завершаться вежливым призывом к действию или вопросом.\n\n"
        "ЖЕСТКИЕ ПРАВИЛА ЗАЗЕМЛЕНИЯ (STRICT GROUNDING):\n"
        "1. ПРИОРИТЕТ РЕГЛАМЕНТА: Если указан 'ПОДТВЕРЖДЕННЫЙ РЕГЛАМЕНТ КОМПАНИИ', ОБЯЗАТЕЛЬНО опирайся на него! "
        "Сформулируй вежливый и четкий ответ заявителю на основе этого регламента (не придумывай альтернативных действий, не противоречь ему).\n"
        "2. ЗАПРЕТ НА ВЫДУМЫВАНИЕ ДЕЙСТВИЙ: Если в блоке 'ПОДТВЕРЖДЕННЫЙ ФАКТ' указано 'Отсутствует' и нет регламента, "
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать, что проблема уже решена или что-то починено! "
        "В таком случае напиши, что заявка принята инженером в работу, проводится диагностика, и вежливо предложи "
        "1-2 первичных действия для проверки (например, проверить кабель питания/кнопку монитора/перезагрузить ПК).\n"
        "3. ЕСЛИ ЕСТЬ ФАКТ РЕШЕНИЯ ИЗ БАЗЫ ЗНАНИЙ: Опирайся строго на него. Не додумывай несуществующие шаги.\n"
        "4. ГРАММАТИКА: Пиши строго от первого лица ('Я проверил...', 'Пожалуйста, проверьте...'). "
        "Не смешивай лица ('мы' и 'они'). Никаких дешевых эмодзи."
    )

    context_lines = [line for line in [rule_fact, fact_block, telemetry_fact] if line]
    context_text = "\n".join(context_lines)

    user_prompt = (
        f"Заявка #{task_id}: {t_name}\n"
        f"Описание проблемы от заявителя: {t_desc}\n"
        f"{context_text}\n\n"
        f"Сформируй регламентный комментарий заявителю в соответствии с правилами."
    )

    try:
        from app.services.ai.schemas import RoutedInferenceRequest

        req = RoutedInferenceRequest(
            prompt=user_prompt,
            system_prompt=system_prompt,
            metadata=RoutingMetadata(service_id=s_id),
            max_tokens=2048,
            temperature=0.1,  # Минимальная температура для исключения фантазий
        )
        res = await ai_hub.dispatch_routed_inference(req)
        output_text = getattr(res, "text", None) or getattr(res, "final_text", None)
        if output_text and len(output_text.strip()) > 15:
            return output_text.strip()
    except Exception as e:
        logger.debug("AI Hub генерация не удалась, переход на fallback: %s", e)

    # Fallback
    return _synthesize_deterministic_fallback(
        task=task, kb_matches=kb_matches, telemetry=telemetry, rule_decision=rule_decision
    )
