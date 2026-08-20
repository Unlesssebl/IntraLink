from typing import Any

from .base import BaseRule, RuleDecision


class FileLockRule(BaseRule):
    """
    Правило: Зависшие файловые сессии / блокировки файлов SMB (Статус 27).
    """

    def __init__(self, priority: int = 40):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "FileLockRule"

    def evaluate(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> RuleDecision | None:
        name = (task.get("Name") or "").lower()
        desc = (task.get("Description") or "").lower()
        user_text = f"{name} {desc}".strip()

        is_file_lock = any(w in user_text for w in [
            "занят другим", "занята другим", "заблокирован другим",
            "не впускает", "якобы я им пользуюсь", "кем-то занят",
            "обменный exle", "обменный excel", "файл занят", "файл заблокирован"
        ])

        if is_file_lock:
            return RuleDecision(
                template_key="file_lock_smb",
                name="Зависшая файловая блокировка (SMB-сессия)",
                status_id=27,
                status_name="В работе",
                expenses=10,
                comment=(
                    "Добрый день! Ваша заявка принята в работу. "
                    "Уточните, пожалуйста, в комментариях к этой заявке полный путь к файлу или сетевой папке для сброса зависшей сессии на файловом сервере."
                ),
            )

        return None
