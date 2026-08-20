from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuleDecision:
    """
    Результат применения правила триажа / шаблонизации.
    """
    template_key: str
    name: str
    status_id: int
    status_name: str
    expenses: int
    comment: str
    is_redirect: bool = False
    target_service_name: str = ""
    current_root: str | None = None
    target_root: str | None = None
    reason: str | None = None
    rag_applied: bool = False
    rag_task_id: int | None = None
    rag_similarity: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "template_key": self.template_key,
            "name": self.name,
            "status_id": self.status_id,
            "status_name": self.status_name,
            "expenses": self.expenses,
            "comment": self.comment,
        }
        if self.is_redirect:
            res["is_redirect"] = True
            res["current_root"] = self.current_root
            res["target_root"] = self.target_root
            res["target_service_name"] = self.target_service_name
            res["reason"] = self.reason
        if self.rag_applied:
            res["rag_applied"] = True
            res["rag_task_id"] = self.rag_task_id
            res["rag_similarity"] = self.rag_similarity
        if self.extra:
            res.update(self.extra)
        return res


class BaseRule(ABC):
    """
    Абстрактный базовый класс для отдельного правила триажа Helpdesk.
    Каждое правило изолированно и решает ровно одну задачу (Single Responsibility).
    """

    def __init__(self, priority: int = 100):
        self.priority = priority

    @property
    @abstractmethod
    def name(self) -> str:
        """Человекочитаемое название правила."""
        pass

    @abstractmethod
    def evaluate(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> RuleDecision | None:
        """
        Оценивает контекст заявки. Если правило применимо — возвращает RuleDecision,
        иначе None (передавая управление следующему правилу в пайплайне).
        """
        pass
