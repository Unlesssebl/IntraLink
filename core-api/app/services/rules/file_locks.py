import re
from typing import Any

from shared.domain import ClarificationRequired, DecisionOutcome, Evidence, NoMatch, ResolutionProposed

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
            # Проверяем, указан ли уже сетевой путь к файлу (например \\server\share\... или X:\...)
            has_path = bool(re.search(r"(\\\\[a-zA-Z0-9_\-\.]+\\[^\s]+|[a-zA-Z]:\\[^\s]+)", desc + " " + name))
            if not has_path:
                return RuleDecision(
                    template_key="file_lock_smb",
                    name="Уточнение пути к заблокированному файлу",
                    status_id=35,
                    status_name="Требует уточнения",
                    expenses=5,
                    comment=(
                        "Добрый день! Уточните, пожалуйста, в комментариях к этой заявке полный путь к файлу или сетевой папке для сброса зависшей сессии на файловом сервере."
                    ),
                )
            else:
                return RuleDecision(
                    template_key="file_lock_smb_in_progress",
                    name="Сброс зависшей файловой блокировки",
                    status_id=27,
                    status_name="В работе",
                    expenses=10,
                    comment=(
                        "Добрый день! Ваша заявка принята в работу. Выполняем поиск и сброс зависшей файловой сессии на сервере."
                    ),
                )

        return None

    def evaluate_typed(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> DecisionOutcome:
        name = (task.get("Name") or "").lower()
        desc = (task.get("Description") or "").lower()
        user_text = f"{name} {desc}".strip()

        is_file_lock = any(w in user_text for w in [
            "занят другим", "занята другим", "заблокирован другим",
            "не впускает", "якобы я им пользуюсь", "кем-то занят",
            "обменный exle", "обменный excel", "файл занят", "файл заблокирован"
        ])

        if not is_file_lock:
            return NoMatch(rule_key="smb.file_lock", rule_version="2")

        extracted_path = task.get("_extracted_file_path")
        has_path = bool(
            extracted_path
            or re.search(r"(\\\\[a-zA-Z0-9_\-\.]+\\[^\s]+|[a-zA-Z]:\\[^\s]+)", desc + " " + name)
        )

        if not has_path:
            return ClarificationRequired(
                rule_key="smb.file_lock",
                rule_version="2",
                outcome_key="file_lock_smb",
                missing_fields=["file_path"],
                evidence=[
                    Evidence(
                        source="rule",
                        field="file_path",
                        code="path_missing",
                        detail="SMB lock detected but UNC/local path is not specified",
                    )
                ],
            )
        else:
            return ResolutionProposed(
                rule_key="smb.file_lock",
                rule_version="2",
                outcome_key="file_lock_smb_in_progress",
                target_status_id=27,
                evidence=[
                    Evidence(
                        source="rule",
                        field="file_path",
                        code="path_provided",
                        detail=str(extracted_path or "path detected in text"),
                    )
                ],
            )

