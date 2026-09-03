"""
Модуль AI-генерации ответов инженера (Response Synthesis) и автоматической канонизации базы знаний (Auto-KB Canonization).
Обеспечивает строгое соблюдение контуров Zero Trust DLP (RED / YELLOW / GREEN).
"""

import asyncio
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
    "согласовано",
    "согласована",
    "попросили перекинуть",
}

_SYSTEM_LOG_PATTERNS = [
    re.compile(r"(?i)автоматически переведена в статус"),
    re.compile(r"(?i)автоматически закрыта"),
    re.compile(r"(?i)по истечении \d+ часов"),
    re.compile(r"(?i)статус изменен"),
    re.compile(r"(?i)назначен исполнитель"),
    re.compile(r"(?i)исполнитель изменен"),
    re.compile(r"(?i)оцените качество"),
    re.compile(r"(?i)оповещение отправлено"),
    re.compile(r"(?i)уведомление отправлено"),
    re.compile(r"(?i)отменил[ао]? делегирование"),
    re.compile(r"(?i)делегировал[ао]? заявку"),
    re.compile(r"(?i)попросили перекинуть"),
    re.compile(r"(?i)^согласовано$"),
    re.compile(r"(?i)^согласована?$"),
    re.compile(r"(?i)служебная записка №?\d* была согласована"),
]

_SYSTEM_EDITORS = {
    "intraservice",
    "system",
    "система",
    "администратор",
    "administrator",
    "служба рассылки",
    "робот",
}


def is_system_or_noise_comment(text: str, editor_name: str = "") -> bool:
    """Проверяет, является ли комментарий системным уведомлением или служебным логом."""
    if not text:
        return True
    if editor_name and editor_name.strip().lower() in _SYSTEM_EDITORS:
        return True
    t_clean = text.strip()
    for pat in _SYSTEM_LOG_PATTERNS:
        if pat.search(t_clean):
            return True
    return False


_NON_INFORMATIVE_WORDS = {
    "все",
    "всё",
    "работает",
    "спасибо",
    "ок",
    "хорошо",
    "готово",
    "сделано",
    "решено",
    "закрыто",
    "закрыта",
    "проверено",
    "принято",
    "в",
    "работу",
    "выполнено",
    "выполнена",
    "устранено",
    "штатном",
    "режиме",
    "заявка",
}


_UNINFORMATIVE_PHRASE_PATTERNS = [
    re.compile(r"(?i)\b(?:не\s+дозвонил(?:ся|ась)?|не\s+смог(?:ли)?\s+дозвониться|перезвоните|не\s+отвечает|не\s+берет\s+трубку|нет\s+связи\s+с\s+заявителем|не\s+выходит\s+на\s+связь|не\s+могу\s+до\s+вас\s+дозвониться)\b"),
    re.compile(r"(?i)\b(?:акт\s+прикрепил|прикрепил\s+акт|скан\s+прикрепил|акт\s+во\s+вложении|акт\s+к\s+заявке)\b"),
    re.compile(r"(?i)\b(?:дубликат|повторн(?:ая|ый)\s+(?:заявк|инцидент)|работы\s+ведутся\s+в\s+основн|заявка\s+продублирован)\b"),
    re.compile(r"(?i)\b(?:заявка\s+не\s*актуальн|не\s*актуальн(?:о|а)|потеряла\s+актуальность)\b"),
    re.compile(r"(?i)\b(?:неправильн(?:ый|ом)\s+раздел|оставьте\s+заявку\s+в\s+раздел|ошибочно\s+создан)\b"),
]

_TECHNICAL_INSTRUCTION_PATTERNS = re.compile(
    r"(?i)(?:\d+\.|\b(?:ipconfig|ping|кабель|драйвер|реестр|служба|служеб|перезагруз|конфигурац|активн|разблокиров|пароль|логин|доступ|установ|настро|программ|порту|сеть|коммутатор|маршрутизатор|принтер|картридж)\b)"
)


def evaluate_solution_quality_fast(solution: str, status_id: int | None = None) -> tuple[bool, str]:
    """
    Уровень 1: Быстрый отсев неинформативных отписок инженеров (Regex & Эвристики длины).
    Возвращает (True, 'ok') или (False, 'причина отсева').
    """
    if not solution or not isinstance(solution, str):
        return False, "пустой текст решения"
    s_clean = solution.strip()
    if not s_clean:
        return False, "пустой текст решения"

    if is_system_or_noise_comment(s_clean):
        return False, "системный лог / уведомление"

    s_lower = s_clean.lower()
    if s_lower in _NON_INFORMATIVE_SOLUTIONS:
        return False, f"шаблонная отписка: '{s_clean}'"

    s_alpha = re.sub(r"[^\w\s]", "", s_lower).strip()
    if s_alpha in _NON_INFORMATIVE_SOLUTIONS:
        return False, f"шаблонная отписка: '{s_clean}'"

    words = {w for w in re.split(r"\W+", s_lower) if w}
    if words and words.issubset(_NON_INFORMATIVE_WORDS):
        return False, "вежливая отписка без технических деталей"

    # Проверка стоп-паттернов отписок инженеров
    for pat in _UNINFORMATIVE_PHRASE_PATTERNS:
        m = pat.search(s_clean)
        if m:
            if len(s_clean) > 150 and _TECHNICAL_INSTRUCTION_PATTERNS.search(s_clean):
                break
            matched_phrase = m.group(0).strip()
            return False, f"отписка инженера ('{matched_phrase}')"

    # Проверка длины по статусам
    if status_id == 30:  # Отменена
        if len(s_clean) < 60:
            return False, "слишком короткое решение для статуса Отменена (<60 симв)"
    elif len(s_clean) < 20:
        return False, "слишком короткое решение (<20 симв)"

    return True, "ok"


def is_informative_solution(solution: str, status_id: int | None = None) -> bool:
    """
    Совместимый интерфейс фильтра качества.
    Возвращает True, если решение пригодно для базы знаний RAG.
    """
    ok, _ = evaluate_solution_quality_fast(solution, status_id=status_id)
    return ok


async def evaluate_solution_quality_llm(
    problem: str,
    solution: str,
    *,
    service_name: str | None = None,
    task_name: str | None = None,
    status_name: str | None = None,
) -> bool:
    """
    Уровень 2: Экспресс-валидация полезности решения через локальную нейросеть (Qwen 2.5).
    Вызывается только для пограничных решений (20-150 символов), прошедших Уровень 1.
    Обогащена контекстом услуги, темы заявки и статуса.
    """
    if len(solution.strip()) > 150:
        return True

    ctx_parts = []
    if service_name:
        ctx_parts.append(f"Категория услуги: {service_name}")
    if task_name:
        ctx_parts.append(f"Тема: {task_name}")
    if problem and problem != task_name:
        ctx_parts.append(f"Симптомы: {problem[:250]}")
    if status_name:
        ctx_parts.append(f"Статус: {status_name}")
    ctx_parts.append(f"Решение инженера: {solution.strip()[:400]}")
    full_ctx = "\n".join(ctx_parts)

    prompt = (
        "Оцени, содержит ли решение конкретные полезные технические действия или инструкции для указанной услуги, "
        "либо это пустая отписка/административное закрытие.\n\n"
        f"{full_ctx}\n\n"
        "Ответь строго одной цифрой: 1 (если полезно) или 0 (если отписка)."
    )

    try:
        from app.services.ai.hub import ai_hub
        from app.services.ai.schemas import RoutedInferenceRequest, RoutingMetadata
        req = RoutedInferenceRequest(
            prompt=prompt,
            system_prompt="Ты ассистент контроля качества базы знаний Helpdesk. Отвечай строго 1 или 0.",
            metadata=RoutingMetadata(force_circuit=DataCircuit.RED),
            max_tokens=4,
            temperature=0.0,
        )
        res = await asyncio.wait_for(ai_hub.dispatch_routed_inference(req), timeout=3.5)
        text_out = (getattr(res, "text", "") or getattr(res, "final_text", "")).strip().lower()
        if "0" in text_out or "нет" in text_out or "false" in text_out:
            return False
        return True
    except Exception as e:
        logger.debug("AI-валидация не ответила за таймаут, мягкий fallback на Уровень 1: %s", e)
        return True


async def deep_audit_solution_with_llm(
    problem: str,
    solution: str,
    *,
    service_name: str | None = None,
    task_name: str | None = None,
    status_name: str | None = None,
    resolution_label: str | None = None,
    diagnostic_steps: list[str] | str | None = None,
) -> dict[str, Any]:
    """
    Тяжелый ночной аудит прецедента без каких-либо эвристических ограничений и срезок длины.
    Выполняет глубокую семантическую оценку через локальную модель Qwen 2.5 (RED-контур)
    с полным контекстом услуги, темы, симптомов, истории диагностики и статуса.

    Возвращает словарь:
    {
        "score": float,         # 0.0 - 1.0 (оценка ценности решения для инженера)
        "verdict": str,         # "keep" | "blacklist"
        "reason": str,          # краткое обоснование оценки
        "key_steps": list[str], # извлеченные технические шаги решения
    }
    """
    import json
    default_res = {
        "score": 0.5,
        "verdict": "keep",
        "reason": "Базовый прецедент",
        "key_steps": [],
    }

    clean_sol = (solution or "").strip()
    if not clean_sol or len(clean_sol) < 15:
        return {
            "score": 0.0,
            "verdict": "blacklist",
            "reason": "Пустое или слишком короткое решение (<15 симв)",
            "key_steps": [],
        }

    context_lines = []
    if service_name:
        context_lines.append(f"Категория услуги: {service_name}")
    if task_name:
        context_lines.append(f"Тема заявки: {task_name}")
    if problem and problem != task_name:
        context_lines.append(f"Описание проблемы: {problem[:600]}")
    if status_name:
        st_info = f"{status_name}"
        if resolution_label:
            st_info += f" [{resolution_label}]"
        context_lines.append(f"Статус закрытия: {st_info}")
    if diagnostic_steps:
        if isinstance(diagnostic_steps, list):
            diag_str = " | ".join(str(x) for x in diagnostic_steps[:3])
        else:
            diag_str = str(diagnostic_steps)
        if diag_str.strip():
            context_lines.append(f"Предшествующие действия/диагностика: {diag_str[:500]}")
    context_lines.append(f"Финальное решение инженера: {clean_sol[:1200]}")

    context_block = "\n".join(context_lines)

    prompt = (
        "Ты строгий технический цензор базы знаний Helpdesk.\n"
        "Оцени реальную техническую пользу решения инженера с учетом категории услуги и симптомов проблемы.\n\n"
        f"{context_block}\n\n"
        "КРИТЕРИИ ОЦЕНКИ:\n"
        "- score 0.0 - 0.2, verdict=\"blacklist\": если это отписка, закрытие без действий, отказ, дубликат, просьба перезвонить, 'не дозвонился' или решение не относится к заявленной проблеме.\n"
        "- score 0.4 - 0.6, verdict=\"keep\": простое типовое действие (сброс пароля в AD, перезагрузка ПК/службы, выдача прав/доступа).\n"
        "- score 0.7 - 0.85, verdict=\"keep\": подробное решение с конкретными шагами, названиями программ, служб, путей, утилит или настроек.\n"
        "- score 0.9 - 1.0, verdict=\"keep\": исчерпывающая пошаговая инструкция с диагностикой и устранением причин.\n\n"
        "Ответь ИСКЛЮЧИТЕЛЬНО в формате JSON:\n"
        "{\n"
        '  "score": 0.8,\n'
        '  "verdict": "keep",\n'
        '  "reason": "краткое пояснение на русском с учетом услуги",\n'
        '  "key_steps": ["шаг 1", "шаг 2"]\n'
        "}"
    )

    try:
        from app.services.ai.hub import ai_hub
        from app.services.ai.schemas import DataCircuit, RoutedInferenceRequest, RoutingMetadata

        req = RoutedInferenceRequest(
            prompt=prompt,
            system_prompt="Отвечай исключительно валидным JSON без markdown разметки.",
            metadata=RoutingMetadata(force_circuit=DataCircuit.RED),
            max_tokens=256,
            temperature=0.0,
        )
        res = await asyncio.wait_for(ai_hub.dispatch_routed_inference(req), timeout=8.0)
        raw_text = (getattr(res, "text", "") or getattr(res, "final_text", "")).strip()

        if "`" in raw_text:
            raw_text = re.sub(r"^`(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
            raw_text = re.sub(r"\s*`$", "", raw_text, flags=re.MULTILINE).strip()

        json_match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
        if json_match:
            raw_text = json_match.group(0)

        # Безопасное экранирование обратных слешей путей Windows (C:\Windows\...) для валидности JSON
        raw_text = re.sub(r'\\(?![/u"bfnrt\\])', r"\\\\", raw_text)

        data = json.loads(raw_text)
        score = float(data.get("score", 0.5))

        # Калибровка: если текст содержит очевидный стоп-паттерн отписки
        if any(p.search(clean_sol) for p in _UNINFORMATIVE_PHRASE_PATTERNS):
            score = min(score, 0.15)
            data["verdict"] = "blacklist"
            orig_r = data.get("reason", "")
            data["reason"] = f"Отписка / закрытие без решения: {orig_r}"

        score = max(0.0, min(1.0, score))
        verdict = str(data.get("verdict", "keep" if score >= 0.4 else "blacklist")).lower()
        if score < 0.4:
            verdict = "blacklist"
        reason = str(data.get("reason", "")).strip()
        key_steps = [str(s).strip() for s in data.get("key_steps", []) if str(s).strip()]

        return {
            "score": round(score, 2),
            "verdict": verdict,
            "reason": reason,
            "key_steps": key_steps[:5],
        }
    except Exception as e:
        logger.warning("Ошибка глубокого аудита решения LLM: %s", e)
        # Если это очевидный стоп-паттерн, всё равно блокируем
        if any(p.search(clean_sol) for p in _UNINFORMATIVE_PHRASE_PATTERNS):
            return {
                "score": 0.1,
                "verdict": "blacklist",
                "reason": "Отписка / закрытие без решения (эвристика)",
                "key_steps": [],
            }
        return default_res


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


def classify_task_resolution(
    task: dict[str, Any],
    solution_text: str,
    status_id: int | None = None,
    status_name: str = "",
) -> dict[str, str]:
    """
    Классифицирует исход выполнения заявки (Outcome Classification)
    на основе статуса, текста решения и метаданных.
    Возвращает:
      - resolution_type: 'resolved' | 'rejected' | 'redirected' | 'duplicate' | 'consultation' | 'cancelled'
      - resolution_label: русскоязычное понятное наименование
      - resolution_badge_color: 'emerald' | 'rose' | 'sky' | 'amber'
    """
    sol_lower = solution_text.lower()
    st_id = status_id if status_id is not None else task.get("StatusId")
    status_map = {
        28: "Закрыта",
        29: "Выполнена",
        43: "Обработано 1-й линией",
        30: "Отменена",
        31: "Открыта",
        27: "В работе",
        35: "Требует уточнения",
        36: "Приостановлена",
        33: "На согласовании",
        34: "На тестировании",
    }
    st_name = status_name or task.get("StatusName") or status_map.get(st_id, "Закрыта" if st_id == 29 else "Отменена")

    # 0. Незавершенные заявки: Требует уточнения (StatusId: 35) или в работе
    if st_id == 35 or "уточнен" in (st_name or "").lower():
        return {
            "resolution_type": "clarification",
            "resolution_label": "Требует уточнения",
            "resolution_badge_color": "amber",
        }
    if st_id in (31, 27, 36, 33, 34) or any(w in (st_name or "").lower() for w in ("открыт", "работ", "согласован", "приостановлен")):
        return {
            "resolution_type": "in_progress",
            "resolution_label": st_name or "В работе",
            "resolution_badge_color": "amber",
        }

    # 1. Дубликат (Duplicate)
    if "дубликат" in sol_lower or "дубль" in sol_lower or "повтор" in sol_lower:
        return {
            "resolution_type": "duplicate",
            "resolution_label": "Дубликат",
            "resolution_badge_color": "amber",
        }

    # 2. Перенаправление (Redirected)
    if any(k in sol_lower for k in ("перенаправлен", "передано в", "создайте в сервисе", "обратитесь в раздел", "ошибочный сервис")):
        return {
            "resolution_type": "redirected",
            "resolution_label": "Перенаправлено",
            "resolution_badge_color": "sky",
        }

    # 3. Отказ / Отклонено (Rejected) — исключаем вопросы инженера заявителю
    is_clarification_question = any(q in sol_lower for q in ("?", "уточните", "прошу дать ответ", "был ли", "был уволен", "уволен ли", "уволен или"))
    if not is_clarification_question:
        rejection_markers = [
            "отказ", "не согласован", "нет сз", "служебная записка не", "сотрудник уволен", "пользователь уволен",
            "в связи с увольнением", "не числится в штате", "нет оснований", "запрещено", "отклонен",
            "отклонена", "отсутствуют права", "почты нет", "учетной записи нет",
        ]
        if any(k in sol_lower for k in rejection_markers):
            return {
                "resolution_type": "rejected",
                "resolution_label": "Отказ / Отклонено",
                "resolution_badge_color": "rose",
            }

    # 4. Если статус 30 ("Отменена"), но нет специфичного маркера выше
    if st_id == 30:
        return {
            "resolution_type": "cancelled",
            "resolution_label": "Отменена",
            "resolution_badge_color": "amber",
        }

    # 5. Консультация (Consultation)
    consultation_markers = ["инструкция", "разъяснено", "проконсультирован", "подсказал", "по информации", "консультация"]
    if any(k in sol_lower for k in consultation_markers):
        return {
            "resolution_type": "consultation",
            "resolution_label": "Консультация",
            "resolution_badge_color": "sky",
        }

    # 6. Успешно выполнено (Resolved) — по умолчанию для статуса 29
    return {
        "resolution_type": "resolved",
        "resolution_label": "Успешно выполнено",
        "resolution_badge_color": "emerald",
    }


def canonize_task_solution(
    task: dict[str, Any], lifetime: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """
    Извлекает структурированную триаду (Problem, Root Cause, Solution) с учетом
    ролей участников, контекста закрытия и классификации исхода заявки.
    """
    raw_name = task.get("Name") or ""
    raw_desc = task.get("Description") or ""
    task_id = task.get("Id") or 0
    creator_id = str(task.get("CreatorId") or "")
    executor_ids = {str(x).strip() for x in str(task.get("ExecutorIds") or "").split(",") if str(x).strip()}
    status_id = task.get("StatusId")
    status_map = {
        28: "Закрыта",
        29: "Выполнена",
        43: "Обработано 1-й линией",
        30: "Отменена",
        31: "Открыта",
        27: "В работе",
        35: "Требует уточнения",
        36: "Приостановлена",
        33: "На согласовании",
        34: "На тестировании",
    }
    status_name = task.get("StatusName") or status_map.get(status_id, f"Статус {status_id}")
    is_terminal = (status_id in (28, 29, 43, 30)) or (status_id is None)

    # 1. Очистка и дистилляция описания проблемы
    full_problem_raw = f"{raw_name}. {raw_desc}".strip()
    clean_problem = clean_html(full_problem_raw)
    distilled_problem = distill_search_query(clean_problem)
    if not distilled_problem or len(distilled_problem) < 5:
        distilled_problem = clean_problem

    # 2. Поиск содержательного решения с ролевой фильтрацией (Role & Outcome Isolation)
    solution_text = ""
    root_cause = "Эксплуатационный сбой"

    if lifetime:
        # Сортируем события от самых новых к старым
        sorted_events = sorted(
            lifetime,
            key=lambda x: str(x.get("Date") or ""),
            reverse=True,
        )

        candidates_closing: list[str] = []    # Tier 1: комментарий инженера при переводе в статус закрытия/отмены
        candidates_executor: list[str] = []   # Tier 2: комментарий назначенного исполнителя
        candidates_staff: list[str] = []      # Tier 3: комментарий любого другого сотрудника линии

        for item in sorted_events:
            comm = clean_html(
                item.get("Comments") or item.get("Comment") or item.get("Text") or ""
            ).strip()
            if not comm or len(comm) < 5:
                continue

            editor_id = str(item.get("EditorId") or "")
            editor_name = str(item.get("Editor") or "")
            ev_status_id = item.get("StatusId")

            # 1. Жесткий запрет заявителя (заявитель не может писать решение)
            if creator_id and editor_id == creator_id:
                continue

            # 2. Фильтр системных роботов и служебных уведомлений
            if is_system_or_noise_comment(comm, editor_name):
                continue

            # 3. Распределение по уровням доверия
            if ev_status_id in (29, 30):
                candidates_closing.append(comm)
            elif editor_id in executor_ids:
                candidates_executor.append(comm)
            else:
                candidates_staff.append(comm)

        # Выбираем наиболее авторитетный комментарий инженера:
        # 1. Приоритет: комментарий при переводе статуса (candidates_closing)
        # 2. Поднятие на уровень выше: последний комментарий назначенного исполнителя (candidates_executor)
        # 3. Комментарий любого другого сотрудника линии (candidates_staff)
        if candidates_closing:
            solution_text = candidates_closing[0]
        elif candidates_executor:
            solution_text = candidates_executor[0]
        elif candidates_staff:
            solution_text = candidates_staff[0]

    # Если в комментариях решения нет, проверяем поля самой задачи
    if not solution_text:
        for field in ("Solution", "CloseReason", "Resolution"):
            val = clean_html(task.get(field) or "").strip()
            if val and len(val) >= 10 and not is_system_or_noise_comment(val):
                solution_text = val
                break

    # Удаление стандартных телефонных подписей
    if solution_text:
        solution_text = _SIGNATURE_CLEANUP_RE.sub("", solution_text).strip()

    # Сбор предшествующих диагностических комментариев исполнителей
    diagnostic_steps: list[str] = []
    if lifetime:
        for c in (candidates_executor + candidates_staff):
            c_clean = _SIGNATURE_CLEANUP_RE.sub("", c).strip()
            if c_clean and c_clean != solution_text and c_clean not in diagnostic_steps:
                diagnostic_steps.append(c_clean)

    # Определение классификации исхода (Resolution Outcome)
    resolution_info = classify_task_resolution(
        task=task,
        solution_text=solution_text,
        status_id=status_id,
        status_name=status_name,
    )

    # Определение первопричины (Root Cause) по эвристикам
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
        root_cause = "Отсутствие учетной записи в доменной группе доступа WLAN"

    canonical_summary = (
        f"Проблема: {distilled_problem}\n"
        f"Статус: {status_name} [{resolution_info['resolution_label']}]\n"
        f"Первопричина: {root_cause}\n"
        f"Решение/Резолюция: {solution_text}"
    )

    return {
        "problem": distilled_problem,
        "root_cause": root_cause,
        "solution": solution_text,
        "diagnostic_steps": diagnostic_steps[:3],
        "canonical_summary": canonical_summary,
        "status_id": status_id,
        "status_name": status_name,
        "resolution_type": resolution_info["resolution_type"],
        "resolution_label": resolution_info["resolution_label"],
        "resolution_badge_color": resolution_info["resolution_badge_color"],
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
