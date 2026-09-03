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
# Zero-Emoji Policy & Brandbook Sanitizer (ООО «АйТи ТЭМПО»)
# ---------------------------------------------------------------------------

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Symbols & Pictographs
    "\U0001F680-\U0001F6FF"  # Transport & Map Symbols
    "\U0001F1E0-\U0001F1FF"  # Flags
    "\U00002702-\U000027B0"  # Dingbats
    "\U000024C2-\U0001F251"  # Enclosed Alphanumerics
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess Symbols, etc.
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U00002600-\U000026FF"  # Miscellaneous Symbols
    "\U00002B50"              # Star
    "\U0000200D"              # Zero-width joiner
    "\U0000FE0F"              # Variation Selector-16
    "]+",
    flags=re.UNICODE,
)


def strip_emojis(text: str) -> str:
    """
    Удаляет любые Unicode-эмодзи и пиктограммы, обеспечивая строгое соответствие
    корпоративному брендбуку и минималистичному стилю без эмодзи (Zero-Emoji Policy).
    """
    if not text:
        return ""
    cleaned = _EMOJI_PATTERN.sub("", text)
    cleaned = re.sub(r"[ \t]+([.,!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" \n", "\n", cleaned)
    return cleaned.strip()


def calculate_confidence_score(
    kb_matches: list[dict[str, Any]] | None = None,
    telemetry: dict[str, Any] | None = None,
    rule_decision: dict[str, Any] | None = None,
) -> float:
    """
    Рассчитывает композитный скор уверенности решения (Confidence Score: 0.00 - 1.00).
    Используется для защиты от слепого пакетного подтверждения (Rubber Stamping):
    тикеты со скором < 0.80 требуют индивидуальной ручной проверки оператором.
    """
    # 1. Анализ детерминированного правила
    rule_score = 0.40
    if rule_decision:
        r_type = rule_decision.get("rule_type") or rule_decision.get("template_key")
        if r_type == "duplicate_task":
            return 0.99
        if r_type in ("wlan", "create_user"):
            return 0.95
        if rule_decision.get("is_redirect") or r_type == "wrong_service":
            rule_score = 0.90
        elif r_type in ("password_reset", "hardware_repair"):
            rule_score = 0.90
        elif r_type in ("printer_issue", "1c_issue", "pc_offline"):
            rule_score = 0.80
        elif r_type not in ("standard_in_work", None):
            rule_score = 0.75

    # 2. Анализ RAG-прецедентов
    rag_score = 0.30
    if kb_matches and len(kb_matches) > 0:
        top_match = kb_matches[0]
        sim = top_match.get("similarity_pct")
        if sim is not None:
            rag_score = min(max(float(sim) / 100.0, 0.0), 1.0)
        else:
            dist = top_match.get("distance")
            if dist is not None:
                rag_score = max(0.0, min(1.0, 1.0 - float(dist)))
            elif top_match.get("solution"):
                rag_score = 0.85

    # 3. Анализ телеметрии хоста
    tel_score = 0.35
    if telemetry:
        status = str(
            telemetry.get("status") or telemetry.get("ping_status") or ""
        ).upper()
        has_metrics = bool(
            telemetry.get("metrics")
            or telemetry.get("disk_c")
            or telemetry.get("services")
        )
        if status == "ONLINE":
            tel_score = 0.95 if has_metrics else 0.85
        elif status == "OFFLINE":
            tel_score = 0.75  # Офлайн-статус является проверенным аппаратным фактом
        elif status:
            tel_score = 0.50

    # 4. Композитный расчет весов (RAG 0.45, Телеметрия 0.35, Регламент 0.20)
    score = (rag_score * 0.45) + (tel_score * 0.35) + (rule_score * 0.20)
    return round(max(0.10, min(0.99, score)), 2)


def extract_thread_context(comments_history: list[dict[str, Any]] | None) -> dict[str, Any]:
    """
    Извлекает содержательные комментарии переписки (до 5 последних),
    отсекает системные статусы и определяет текущую фазу диалога.
    """
    if not comments_history:
        return {"is_follow_up": False, "thread": [], "last_author": None, "last_comment": ""}

    meaningful = []
    for item in comments_history:
        raw_text = item.get("Comment") or item.get("Text") or item.get("Description") or ""
        text = clean_html(raw_text).strip()
        if not text or len(text) < 4:
            continue
        if any(text.startswith(prefix) for prefix in [
            "Статус изменен", "Назначен исполнитель", "Изменен приоритет", "Добавлен файл", "Создана заявка"
        ]):
            continue
        author = item.get("UserName") or item.get("Creator") or "Пользователь"
        meaningful.append({
            "author": author,
            "text": text,
            "created": item.get("Created") or item.get("EventDate") or "",
        })

    if not meaningful:
        return {"is_follow_up": False, "thread": [], "last_author": None, "last_comment": ""}

    recent = meaningful[-5:]
    last_msg = recent[-1]
    return {
        "is_follow_up": True,
        "thread": recent,
        "last_author": last_msg["author"],
        "last_comment": last_msg["text"],
    }


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
    execution_rule_types = {"wlan_access", "user_creation"}
    if (
        rule_decision
        and r_key not in (None, "", "standard_in_work")
        and r_key not in execution_rule_types
    ):
        rule_comment = (rule_decision.get("comment") or "").strip()
        if rule_comment:
            return rule_comment

    task_id = task.get("Id") or ""
    name = task.get("Name") or ""
    desc = task.get("Description") or ""
    combined = f"{name} {desc}".lower()

    # Извлечение данных телеметрии хоста
    pc_name = (telemetry or {}).get("pc_name") or ""
    ping_status = (
        (telemetry or {}).get("status")
        or (telemetry or {}).get("ping_status")
        or ""
    )
    services = (telemetry or {}).get("services") or {}
    spooler_status = (
        ((telemetry or {}).get("metrics") or {}).get("spooler")
        or services.get("spooler")
        or ""
    )

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
                    f"Здравствуйте! Для похожей проблемы на рабочей станции {pc_name} "
                    f"ранее применялось решение: {clean_sol} "
                    f"Я проверю его применимость к заявке #{task_id}."
                )
            return (
                f"Здравствуйте! Для похожей проблемы ранее применялось решение: {clean_sol} "
                f"Я проверю его применимость к заявке #{task_id}."
            )

    # 2. Пароли и учетные записи Active Directory (RED контур)
    ad_keywords = ["парол", "учетн", "разблокиров", "блокировк", "active directory", "логин"]
    if any(k in combined for k in ad_keywords):
        return (
            f"Здравствуйте! Заявка #{task_id} на проверку учетной записи принята в работу. "
            "Для сброса пароля, пожалуйста, свяжитесь с Helpdesk с рабочего телефона для подтверждения личности."
        )

    # 3. Печать и Spooler
    if "печать" in combined or "принтер" in combined or "spooler" in combined:
        pc_part = f" на рабочей станции {pc_name}" if pc_name else ""
        return (
            f"Здравствуйте! По заявке #{task_id}{pc_part} выполняю диагностику "
            "службы печати и очереди заданий. Сообщу результат после фактической проверки."
        )

    # 4. Проблемы 1С:Предприятие
    if "1с" in combined or "1c" in combined or "формата потока" in combined:
        pc_part = f" на ПК {pc_name}" if pc_name else ""
        return (
            f"Здравствуйте! По заявке #{task_id}{pc_part} выполняю диагностику локального кэша 1С:Предприятие. "
            "Сообщу результат после проверки."
        )

    # 5. Wi-Fi и доменные учетные записи
    if "wlan" in combined or "wi-fi" in combined or "wifi" in combined:
        return (
            f"Здравствуйте! Заявка #{task_id} на доступ к корпоративной сети WLAN-WORKNET принята в работу. "
            "Доступ будет подтвержден после проверки учетной записи в Active Directory."
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
    comments_history: list[dict[str, Any]] | None = None,
) -> str:
    """
    Синтезирует экспертный персонализированный ответ инженера Helpdesk
    с жестким заземлением на факты (Strict Grounding), детерминированными шлюзами,
    контекстом истории переписки (Thread-Aware) и соблюдением брендбука без эмодзи (Zero-Emoji Policy).
    """
    if force_deterministic:
        return strip_emojis(
            _synthesize_deterministic_fallback(
                task=task, kb_matches=kb_matches, telemetry=telemetry, rule_decision=rule_decision
            )
        )

    task_id = task.get("Id") or 0
    t_name = task.get("Name") or ""
    t_desc = clean_html(task.get("Description") or "")
    s_id = task.get("ServiceId") or 0
    combined = f"{t_name} {t_desc}".lower()

    # 1. Детерминированный шлюз для не-IT заявок (АХО, канцелярия) — ZERO LLM.
    # Использует модульный SSOT-константу _NON_IT_KEYWORDS.
    if any(k in combined for k in _NON_IT_KEYWORDS):
        return strip_emojis(
            _synthesize_deterministic_fallback(
                task=task, kb_matches=kb_matches, telemetry=telemetry, rule_decision=rule_decision
            )
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
        return strip_emojis(
            _synthesize_deterministic_fallback(
                task=task, kb_matches=kb_matches, telemetry=telemetry, rule_decision=rule_decision
            )
        )

    # 4. Анализ истории переписки (Thread-Aware)
    thread_ctx = extract_thread_context(comments_history)
    is_follow_up = thread_ctx.get("is_follow_up", False)

    thread_fact = ""
    if is_follow_up and thread_ctx.get("thread"):
        lines = [f"  - [{c['author']}]: {c['text']}" for c in thread_ctx["thread"]]
        thread_fact = "ИСТОРИЯ ПРЕДЫДУЩЕЙ ПЕРЕПИСКИ (ПОСЛЕДНИЕ РЕПЛИКИ):\n" + "\n".join(lines)

    # 5. Подготовка заземленного контекста (Strict Grounding Context)
    rule_fact = ""
    r_key = (rule_decision.get("rule_type") or rule_decision.get("template_key")) if rule_decision else None
    if rule_decision and r_key not in (None, "", "standard_in_work"):
        r_comment = (rule_decision.get("comment") or "").strip()
        if r_comment:
            if r_key in {"wlan_access", "user_creation"}:
                rule_fact = (
                    f"ПЛАНИРУЕМОЕ ИНФРАСТРУКТУРНОЕ ДЕЙСТВИЕ ({r_key}): "
                    "операция еще не подтверждена Execution Worker. Запрещено "
                    "сообщать, что она выполнена."
                )
            else:
                rule_fact = f"ПОДТВЕРЖДЕННЫЙ РЕГЛАМЕНТ КОМПАНИИ (ДЕЙСТВИЕ {r_key}): {r_comment}"

    fact_block = ""
    if kb_matches and len(kb_matches) > 0:
        top_sol = kb_matches[0].get("solution", "").strip()
        if top_sol:
            fact_block = f"ПОДТВЕРЖДЕННЫЙ ФАКТ РЕШЕНИЯ (ИЗ БАЗЫ ЗНАНИЙ): {top_sol}"

    telemetry_fact = ""
    if telemetry:
        pc_name = telemetry.get("pc_name") or ""
        ping = telemetry.get("status") or telemetry.get("ping_status") or ""
        metrics = telemetry.get("metrics") or {}
        disk_free = metrics.get("disk_free_gb")
        if disk_free is None and isinstance(telemetry.get("disk_c"), dict):
            disk_free = telemetry["disk_c"].get("free_gb")
        services = telemetry.get("services") or {}
        spooler = metrics.get("spooler") or services.get("spooler") or "?"
        telemetry_fact = (
            f"ДАННЫЕ ТЕЛЕМЕТРИИ ХОСТА: ПК {pc_name}, связь {ping}, "
            f"Диск C: {disk_free if disk_free is not None else '?'}GB, "
            f"Служба Spooler: {spooler}"
        )

    if not fact_block and not telemetry_fact and not rule_fact and not thread_fact:
        fact_block = (
            "ПОДТВЕРЖДЕННЫЙ ФАКТ ВЫПОЛНЕНИЯ: Отсутствует (заявка только поступила, "
            "инженер еще не производил технических действий)."
        )

    greeting_instruction = (
        "5. ПЕРЕПИСКА УЖЕ ИДЕТ (THREAD-AWARE): В заявке уже есть история сообщений. "
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать 'Заявка принята в работу' или повторять шаблонные вводные фразы первого контакта! "
        f"Отвечай конкретно и по существу на последнюю реплику заявителя: «{thread_ctx.get('last_comment', '')}»."
        if is_follow_up
        else "5. ПЕРВЫЙ КОНТАКТ: Ответ ОБЯЗАТЕЛЬНО должен начинаться со слова 'Здравствуйте!' и завершаться вежливым вопросом или призывом к действию."
    )

    system_prompt = (
        "Ты — опытный инженер 1-й линии Helpdesk ООО «АйТи ТЭМПО» Беликов Ален.\n"
        "Твоя задача — сформировать краткий (2-4 предложения), профессиональный и вежливый комментарий заявителю.\n\n"
        "КОРПОРАТИВНЫЙ БРЕНДБУК И СТИЛЬ (ZERO-EMOJI POLICY):\n"
        "1. ПОЛНЫЙ ЗАПРЕТ ЭМОДЗИ: Категорически запрещено использовать любые эмодзи, смайлики или значки (никаких 🙂, 👍, 🚀, ⚠️, ✅).\n"
        "2. СТРОГИЙ ДЕЛОВОЙ ТОН: Вежливая, конкретная, технически грамотная речь инженера ООО «АйТи ТЭМПО». Обращение к заявителю строго на 'Вы'.\n"
        "3. ГРАММАТИКА: Изложение строго от первого лица единственного числа ('Я проверил...', 'Пожалуйста, проверьте...'). "
        "Не смешивай лица ('мы' и 'они').\n"
        "4. ЖЕСТКИЕ ПРАВИЛА ЗАЗЕМЛЕНИЯ (STRICT GROUNDING):\n"
        "   - ПРИОРИТЕТ РЕГЛАМЕНТА: Если указан 'ПОДТВЕРЖДЕННЫЙ РЕГЛАМЕНТ КОМПАНИИ', ОБЯЗАТЕЛЬНО опирайся на него!\n"
        "   - ЗАПРЕТ НА ВЫДУМЫВАНИЕ ДЕЙСТВИЙ: Если в блоке 'ПОДТВЕРЖДЕННЫЙ ФАКТ' указано 'Отсутствует' и нет регламента, "
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать, что проблема решена! Сообщи о проведении диагностики и предложи 1-2 первичных действия для проверки.\n"
        "   - ЕСЛИ ЕСТЬ ФАКТ РЕШЕНИЯ ИЗ БАЗЫ ЗНАНИЙ: Опирайся строго на него.\n"
        f"{greeting_instruction}\n"
    )

    context_lines = [line for line in [rule_fact, fact_block, telemetry_fact, thread_fact] if line]
    context_text = "\n".join(context_lines)

    user_prompt = (
        f"Заявка #{task_id}: {t_name}\n"
        f"Описание проблемы от заявителя: {t_desc}\n"
        f"{context_text}\n\n"
        f"Сформируй регламентный комментарий заявителю в соответствии с корпоративным стилем без эмодзи."
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
            return strip_emojis(output_text.strip())
    except Exception as e:
        logger.debug("AI Hub генерация не удалась, переход на fallback: %s", e)

    # Fallback
    return strip_emojis(
        _synthesize_deterministic_fallback(
            task=task, kb_matches=kb_matches, telemetry=telemetry, rule_decision=rule_decision
        )
    )
