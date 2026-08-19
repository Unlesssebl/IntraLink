import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("helpdesk_agent.template_engine")

TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), "templates.json")


def load_templates() -> dict[str, dict[str, Any]]:
    """Загружает шаблоны из templates.json."""
    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Ошибка загрузки templates.json: %s", e)
    return {}


def render_template(template_key: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Подставляет переменные контекста в указанный шаблон.
    """
    templates = load_templates()
    tmpl = templates.get(template_key) or templates.get("in_work_standard", {
        "name": "Стандартное принятие",
        "status_id": 27,
        "status_name": "В работе",
        "expenses": 10,
        "template": "Ваша заявка принята в работу. По вопросам звоните на номер 49-87.",
    })

    raw_text = tmpl.get("template", "")
    pc_name = context.get("pc_name") or "вашем компьютере"
    room = context.get("room") or "кабинет"
    phone = context.get("phone") or "49-87"
    target_service = context.get("target_service") or "соответствующем разделе каталога"

    rendered_text = raw_text.replace("{pc_name}", pc_name)
    rendered_text = rendered_text.replace("{room}", room)
    rendered_text = rendered_text.replace("{phone}", phone)
    rendered_text = rendered_text.replace("{target_service}", target_service)

    return {
        "template_key": template_key,
        "name": tmpl.get("name"),
        "status_id": tmpl.get("status_id", 27),
        "status_name": tmpl.get("status_name", "В работе"),
        "expenses": tmpl.get("expenses", 10),
        "comment": rendered_text.strip(),
    }


def auto_detect_template(
    task: dict[str, Any],
    diag: dict[str, Any] | None = None,
    kb_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Интеллектуальный авто-подбор наиболее точного шаблона на основе контекста инцидента.
    """
    name = (task.get("Name") or "").lower()
    desc = (task.get("Description") or "").lower()
    service_name = (task.get("ServiceName") or "").lower()
    full_text = f"{name} {desc} {service_name}"
    
    meta = task.get("_field_meta") or {}
    pc_name = meta.get("pc_name") or ""
    room = meta.get("room") or ""
    phone = meta.get("phone") or ""

    context = {
        "pc_name": pc_name,
        "room": room,
        "phone": phone,
        "target_service": "Общий раздел",
    }

    # 1. Wi-Fi доступ
    if any(w in full_text for w in ["wi-fi", "wifi", "вайфай", "вай-фай", "work-net", "пароль от сети"]):
        return render_template("wifi_access", context)

    # 2. Проблемы 1С Предприятие (приоритет перед общими словами)
    if any(w in full_text for w in ["1с", "1c", "база 1с", "упп", "штрихкод", "кэш 1с", "хранилище данных"]):
        return render_template("1c_issue", context)

    # 3. Принтеры и оргтехника
    if any(w in full_text for w in ["принтер", "мфу", "печать", "kyocera", "hp ", "canon", "картридж", "тонер", "скан"]):
        return render_template("printer_issue", context)

    # 4. Аппаратный ремонт / Системный блок / Доставка в 112 каб
    if any(w in full_text for w in [
        "новый процессор", "новый системный", "новый пк", "замена диска", "замена hdd", "замена ssd",
        "замена памяти", "черный экран", "пищит", "замена клавиатуры", "установить windows", "присвоить номер",
        "клавиатур", "мышь", "монитор"
    ]):
        return render_template("hardware_repair", context)

    # 5. ПК не в сети (если хост явно найден и оффлайн)
    if diag and not diag.get("is_online", False) and diag.get("target") and diag.get("target") != "UNKNOWN":
        context["pc_name"] = diag.get("target")
        return render_template("pc_offline", context)

    # 6. Fallback на стандартное принятие в работу
    return render_template("in_work_standard", context)
