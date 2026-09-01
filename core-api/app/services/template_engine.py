import json
import logging
import os
from typing import Any

try:
    from .rules import (
        ROOT_SERVICES,
        SERVICE_ID_TO_ROOT,
        RuleDecision,
        RuleEngine,
        classify_target_service,
        get_root_name,
        get_root_number_for_service_id,
    )
    from .rules.redirect import ServiceRedirectRule
except (ImportError, ValueError):
    from rules import (
        ROOT_SERVICES,
        SERVICE_ID_TO_ROOT,
        RuleDecision,
        RuleEngine,
        classify_target_service,
        get_root_name,
        get_root_number_for_service_id,
    )
    from rules.redirect import ServiceRedirectRule

logger = logging.getLogger("core_api.template_engine")

TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), "rules", "templates.json")

# Глобальный инстанс RuleEngine
_default_engine = RuleEngine()
_redirect_rule = ServiceRedirectRule()


def load_templates() -> dict[str, dict[str, Any]]:
    """Загружает шаблоны из templates.json."""
    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Ошибка загрузки templates.json: %s", e)
    # fallback
    fallback_file = os.path.join(os.path.dirname(__file__), "templates.json")
    if os.path.exists(fallback_file):
        try:
            with open(fallback_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Ошибка загрузки fallback templates.json: %s", e)
    return {}


def render_template(template_key: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Подставляет переменные контекста в указанный шаблон.
    """
    templates = load_templates()
    tmpl = templates.get(template_key) or templates.get("in_work_standard", {
        "name": "Стандартное принятие в работу",
        "status_id": 27,
        "status_name": "В работе",
        "expenses": 10,
        "template": "Добрый день! Ваша заявка принята в работу. По вопросам звоните на номер 49-87.",
    })

    raw_text = tmpl.get("template", "")
    pc_name = context.get("pc_name") or "ПК"
    room = context.get("room") or "кабинет"
    phone = context.get("phone") or "49-87"
    target_service = context.get("target_service") or "соответствующем разделе каталога"
    occupied_user = context.get("occupied_user") or "другой сотрудник"
    details = context.get("details") or "удобное время"
    master_task_id = str(context.get("master_task_id") or "")

    rendered_text = raw_text.replace("{pc_name}", pc_name)
    rendered_text = rendered_text.replace("{room}", room)
    rendered_text = rendered_text.replace("{phone}", phone)
    rendered_text = rendered_text.replace("{target_service}", target_service)
    rendered_text = rendered_text.replace("{occupied_user}", occupied_user)
    rendered_text = rendered_text.replace("{details}", details)
    rendered_text = rendered_text.replace("{master_task_id}", master_task_id)

    return {
        "template_key": template_key,
        "name": tmpl.get("name"),
        "status_id": tmpl.get("status_id", 27),
        "status_name": tmpl.get("status_name", "В работе"),
        "expenses": tmpl.get("expenses", 10),
        "comment": rendered_text.strip(),
    }


def detect_service_redirect(task: dict[str, Any]) -> dict[str, Any] | None:
    """
    Проверяет, требует ли заявка отмены и редиректа в другой раздел каталога.
    Если обнаружено несоответствие разделов, возвращает dict с подробным описанием редиректа.
    Если заявка подана корректно, возвращает None.
    """
    decision = _redirect_rule.evaluate(task)
    if decision and decision.is_redirect:
        return decision.to_dict()
    return None


def auto_detect_template(
    task: dict[str, Any],
    diag: dict[str, Any] | None = None,
    kb_matches: list[dict[str, Any]] | None = None,
    redirect_mode: bool = False,
) -> dict[str, Any]:
    """
    Интеллектуальный авто-подбор наиболее точного шаблона на основе контекста инцидента.
    Использует модульный Rule Engine.
    """
    meta = task.get("_field_meta") or {}
    context = {
        "pc_name": meta.get("pc_name") or (diag.get("target") if diag else "") or "ПК",
        "room": meta.get("room") or "",
        "phone": meta.get("phone") or "",
        "target_service": "Общий раздел",
    }

    decision: RuleDecision = _default_engine.evaluate(
        task=task,
        diag=diag,
        kb_matches=kb_matches,
        redirect_mode=redirect_mode,
        context=context,
    )
    return decision.to_dict()
