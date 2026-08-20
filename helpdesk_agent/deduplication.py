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


class DuplicateDetector:
    """
    Интеллектуальный анализатор и детектор заявок-дубликатов в очереди IntraService.
    Обнаруживает:
    1. Точные дубликаты (Дабл-клики / одинаковый текст и заявитель).
    2. Семантические дубликаты по той же проблеме/приложению.
    3. Аппаратные дубликаты (тот же ПК / инвентарник / МФУ).
    """

    def __init__(self, exact_threshold: float = 0.80, host_threshold: float = 0.60):
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
            m_meta = master.get("_field_meta") or {}
            m_pc = (m_meta.get("pc_name") or "").upper().strip()
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
                c_meta = candidate.get("_field_meta") or {}
                c_pc = (c_meta.get("pc_name") or "").upper().strip()
                c_srv_id = candidate.get("ServiceId")
                c_created = parse_task_datetime(candidate.get("Created"))

                # Вычисляем сходство
                sim_ratio = calculate_text_similarity(m_text, c_text)
                same_creator = (m_creator_id and m_creator_id == c_creator_id) or (m_creator and m_creator == c_creator)
                same_pc = bool(m_pc and c_pc and m_pc == c_pc)
                same_service = (m_srv_id and m_srv_id == c_srv_id)

                time_diff_hours = None
                if m_created and c_created:
                    time_diff_hours = abs((c_created - m_created).total_seconds()) / 3600.0

                is_dup = False
                reason = ""
                confidence = 0

                # 1. Точный дубликат того же заявителя
                if same_creator and sim_ratio >= self.exact_threshold:
                    is_dup = True
                    confidence = 10 if sim_ratio >= 0.95 else 9
                    if time_diff_hours is not None and time_diff_hours <= 1.0:
                        reason = f"Повторная отправка тем же заявителем через {int(time_diff_hours * 60)} мин. (Сходство текста {int(sim_ratio * 100)}%)"
                    else:
                        reason = f"Дубликат того же заявителя (Сходство текста {int(sim_ratio * 100)}%)"

                # 2. Тот же ПК и тот же заявитель с высокой схожестью проблемы
                elif same_creator and same_pc and sim_ratio >= self.host_threshold:
                    is_dup = True
                    confidence = 9
                    reason = f"Повторная заявка по тому же ПК {m_pc} (Сходство {int(sim_ratio * 100)}%)"

                # 3. Разные заявители, но тот же ПК и одинаковый инцидент (коллективный дубль)
                elif same_pc and sim_ratio >= self.exact_threshold:
                    is_dup = True
                    confidence = 8
                    reason = f"Коллективный дубль на один ПК {m_pc} от разных сотрудников (Сходство {int(sim_ratio * 100)}%)"

                # 4. Тот же заявитель и раздел каталога с очень высоким сходством темы
                elif same_creator and same_service and calculate_text_similarity(m_name, c_name) >= 0.85:
                    is_dup = True
                    confidence = 9
                    reason = f"Идентичная тема в том же разделе каталога услуг от {candidate.get('Creator')}"

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
