import difflib
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger("helpdesk_agent.deduplication")


def normalize_text_for_comparison(text: str) -> str:
    """Удаляет спецсимволы, лишние пробелы и приводит текст к канонической форме для сравнения."""
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"[^\w\sа-яёa-z0-9]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def calculate_text_similarity(text1: str, text2: str) -> float:
    """Вычисляет коэффициент текстового сходства между двумя строками (0.0 .. 1.0)."""
    norm1 = normalize_text_for_comparison(text1)
    norm2 = normalize_text_for_comparison(text2)
    if not norm1 or not norm2:
        return 0.0
    if norm1 == norm2:
        return 1.0
    return difflib.SequenceMatcher(None, norm1, norm2).ratio()


def parse_task_datetime(dt_str: str | None) -> datetime | None:
    """Парсит дату создания задачи из ISO-строки IntraService."""
    if not dt_str:
        return None
    try:
        clean_str = dt_str.split(".")[0]
        return datetime.fromisoformat(clean_str)
    except Exception:
        return None


def extract_task_hardware(task: dict[str, Any]) -> tuple[str, str]:
    """
    Извлекает имя ПК и инвентарный номер из задачи (кастомные поля, парсер, сырые данные).
    Возвращает (pc_name, inventory_number) в верхнем регистре.
    """
    meta = task.get("_field_meta") or {}
    parsed = task.get("_parsed_fields") or {}
    raw_data = task.get("Data") or ""
    name = task.get("Name") or ""
    desc = task.get("Description") or ""

    # 1. ПК
    pc = (meta.get("pc_name") or task.get("pc_name") or task.get("Host") or "").upper().strip()

    # 2. Инвентарный номер
    inv = (meta.get("inventory_number") or parsed.get("Оборудование / Инвентарный номер") or "").upper().strip()
    if not inv and raw_data:
        m = re.search(r'(?:инв|inv)[^\w\d№]*([0-9a-zа-яё\-]{3,15})', raw_data, re.I)
        if m:
            inv = m.group(1).upper()
    if not inv:
        m = re.search(r'(?:инв\.?\s*(?:№|номер)?\s*([0-9a-zа-яё\-]{3,15}))', f"{name} {desc}", re.I)
        if m:
            inv = m.group(1).upper()

    return pc, inv


class DuplicateDetector:
    """
    Интеллектуальный анализатор и детектор заявок-дубликатов в очереди IntraService.
    Обнаруживает:
    1. Точные дубликаты (Дабл-клики / одинаковый текст и заявитель в пределах 24 часов).
    2. Семантические дубликаты по тому же ПК / оборудованию.
    3. Коллективные дубликаты на один ПК от разных сотрудников.

    Защитные инварианты (Negative Constraints / Hard Veto):
    - РАЗНЫЕ ПК: если в обеих заявках указаны разные ПК, они НЕ могут быть дубликатами.
    - РАЗНЫЕ ИНВЕНТАРНЫЕ НОМЕРА: если указаны разные инвентарники, это разное оборудование.
    - АКТЫ ТЕХНИЧЕСКОГО ОСВИДЕТЕЛЬСТВОВАНИЯ / СПИСАНИЯ: шаблонные массовые заявки на разные единицы техники.
    - ВРЕМЕННОЙ ГОРИЗОНТ: заявки с разницей более 24 часов не считаются автоматическими дубликатами заявителя.
    """

    def __init__(self, exact_threshold: float = 0.85, host_threshold: float = 0.70):
        self.exact_threshold = exact_threshold
        self.host_threshold = host_threshold

    def find_duplicates(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Анализирует список открытых задач очереди и возвращает список найденных дубликатов.
        Для каждого дубликата определяется master_task (более ранняя заявка) и duplicate_task.
        """
        if not tasks or len(tasks) < 2:
            return []

        # Сортируем задачи хронологически (более ранние идут первыми и считаются Master)
        def sort_key(t: dict[str, Any]):
            dt = parse_task_datetime(t.get("Created"))
            tid = t.get("Id") or 0
            return (dt or datetime.min, tid)

        sorted_tasks = sorted(tasks, key=sort_key)
        duplicates = []
        already_marked_dup_ids = set()

        for i in range(len(sorted_tasks)):
            master = sorted_tasks[i]
            m_id = master.get("Id")
            if m_id in already_marked_dup_ids:
                continue

            m_creator_id = master.get("CreatorId")
            m_creator = (master.get("Creator") or "").strip().lower()
            m_name = master.get("Name") or ""
            m_desc = master.get("Description") or ""
            m_text = f"{m_name} {m_desc}".strip()
            m_pc, m_inv = extract_task_hardware(master)
            m_srv_id = master.get("ServiceId")
            m_created = parse_task_datetime(master.get("Created"))

            for j in range(i + 1, len(sorted_tasks)):
                candidate = sorted_tasks[j]
                c_id = candidate.get("Id")
                if c_id in already_marked_dup_ids:
                    continue

                c_creator_id = candidate.get("CreatorId")
                c_creator = (candidate.get("Creator") or "").strip().lower()
                c_name = candidate.get("Name") or ""
                c_desc = candidate.get("Description") or ""
                c_text = f"{c_name} {c_desc}".strip()
                c_pc, c_inv = extract_task_hardware(candidate)
                c_srv_id = candidate.get("ServiceId")
                c_created = parse_task_datetime(candidate.get("Created"))

                # -------------------------------------------------------------
                # 1. ЖЕСТКИЕ ЗАЩИТНЫЕ ОГРАНИЧЕНИЯ (HARD VETO)
                # -------------------------------------------------------------

                # VETO 1: Разные рабочие станции (ПК)
                # Если в обеих заявках указано имя ПК и они не совпадают — это РАЗНЫЕ компьютеры!
                if m_pc and c_pc and m_pc != c_pc:
                    continue

                # VETO 2: Разные инвентарные номера
                # Если указаны разные инвентарные номера — это РАЗНЫЕ единицы оборудования!
                if m_inv and c_inv and m_inv != c_inv:
                    continue

                # Вычисляем временную разницу
                time_diff_hours = None
                if m_created and c_created:
                    time_diff_hours = abs((c_created - m_created).total_seconds()) / 3600.0

                # VETO 3: Шаблоны массовых документов (Акты тех. освидетельствования / списание / дефектовка)
                # Сотрудники создают отдельные заявки на каждый монитор, системник или ИБП.
                is_batch_act = any(
                    kw in m_name.lower() or kw in c_name.lower()
                    for kw in ("акт технического", "акт тех", "освидетельствован", "дефектовк", "списани")
                )
                if is_batch_act:
                    # При актах освидетельствования дубликатом может быть ТОЛЬКО дабл-клик
                    # на ТОЧНО ТАКОЕ ЖЕ оборудование в течение 30 минут
                    has_same_asset = (m_pc and c_pc and m_pc == c_pc) or (m_inv and c_inv and m_inv == c_inv)
                    is_quick_double_click = (
                        time_diff_hours is not None
                        and time_diff_hours <= 0.5
                        and calculate_text_similarity(m_text, c_text) >= 0.95
                    )
                    if not (has_same_asset and is_quick_double_click):
                        continue

                # VETO 4: Временной горизонт
                # Если между заявками прошло более 24 часов и имя ПК не подтверждено как идентичное,
                # это не может быть ошибочной повторной отправкой (дабл-кликом).
                if time_diff_hours is not None and time_diff_hours > 24.0:
                    same_confirmed_pc = bool(m_pc and c_pc and m_pc == c_pc)
                    if not same_confirmed_pc:
                        continue
                    # Если даже на один ПК, но прошло более 72 часов (3 дня) — это отдельный инцидент
                    if time_diff_hours > 72.0:
                        continue

                # -------------------------------------------------------------
                # 2. ОЦЕНКА СХОДСТВА ТЕКСТА И КОНТЕКСТА
                # -------------------------------------------------------------
                sim_ratio = calculate_text_similarity(m_text, c_text)
                same_creator = (m_creator_id and m_creator_id == c_creator_id) or (m_creator and m_creator == c_creator)
                same_pc = bool(m_pc and c_pc and m_pc == c_pc)
                same_service = (m_srv_id and m_srv_id == c_srv_id)

                # Проверка описания: если в обеих заявках есть описание, оно не должно противоречить
                desc_sim = 1.0
                if len(m_desc.strip()) > 5 and len(c_desc.strip()) > 5:
                    desc_sim = calculate_text_similarity(m_desc, c_desc)

                is_dup = False
                reason = ""
                confidence = 0

                # Правило 1: Быстрый дубликат того же заявителя (дабл-клик или нетерпеливая отправка)
                # Требует: тот же заявитель, сходство текста >= exact_threshold, сходство описания >= 0.70, окно <= 24ч
                if same_creator and sim_ratio >= self.exact_threshold and desc_sim >= 0.70:
                    if time_diff_hours is not None and time_diff_hours <= 24.0:
                        is_dup = True
                        confidence = 10 if sim_ratio >= 0.95 else 9
                        if time_diff_hours <= 1.0:
                            reason = f"Повторная отправка тем же заявителем через {int(time_diff_hours * 60)} мин. (Сходство текста {int(sim_ratio * 100)}%)"
                        else:
                            reason = f"Повторная заявка того же заявителя через {int(time_diff_hours)} ч. (Сходство текста {int(sim_ratio * 100)}%)"

                # Правило 2: Тот же ПК и тот же заявитель с подтвержденной схожестью проблемы (до 48ч)
                elif same_creator and same_pc and sim_ratio >= self.host_threshold and desc_sim >= 0.60:
                    if time_diff_hours is None or time_diff_hours <= 48.0:
                        is_dup = True
                        confidence = 9
                        reason = f"Повторная заявка по тому же ПК {m_pc} (Сходство {int(sim_ratio * 100)}%)"

                # Правило 3: Коллективный дубль на один ПК от разных заявителей (до 12ч)
                elif same_pc and sim_ratio >= self.exact_threshold and desc_sim >= 0.70:
                    if time_diff_hours is None or time_diff_hours <= 12.0:
                        is_dup = True
                        confidence = 8
                        reason = f"Коллективный дубль на один ПК {m_pc} от разных сотрудников (Сходство {int(sim_ratio * 100)}%)"

                if is_dup:
                    already_marked_dup_ids.add(c_id)
                    duplicates.append({
                        "master_task_id": m_id,
                        "master_task": master,
                        "duplicate_task_id": c_id,
                        "duplicate_task": candidate,
                        "similarity_score": round(sim_ratio * 100, 1),
                        "confidence": confidence,
                        "reason": reason,
                        "action": {
                            "template_key": "duplicate_task",
                            "name": "Дубликат заявки",
                            "status_id": 30,
                            "status_name": "Отменена",
                            "expenses": 5,
                            "comment": (
                                f"Заявка отменена, т. к. является дубликатом заявки #{m_id}.\n"
                                f"Работы ведутся в рамках основной заявки.\n"
                                f"Если у вас есть дополнения по проблеме, пожалуйста, оставьте комментарий в заявке #{m_id}."
                            ),
                        },
                    })

        return duplicates
