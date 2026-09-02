import logging
from typing import Any

from fastapi import APIRouter, Depends

from app.routers.deps import verify_admin_jwt
from app.services.template_engine import load_templates

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_TEMPLATES_CATALOG = {
    "wifi_access": {
        "name": "Предоставление Wi-Fi (WLAN-WORKNET)",
        "status_id": 29,
        "status_name": "Выполнена (29)",
        "expenses": 10,
        "template": (
            "Доступ к беспроводной корпоративной сети WLAN-WORKNET успешно предоставлен. "
            "Используйте логин и пароль от вашей учетной записи на ПК. Инструкцию по подключению приложил. "
            "По всем вопросам вы можете написать ответ в комментариях к этой заявке."
        ),
        "badge_color": "success",
    },
    "redirect_1c": {
        "name": "Редирект ➔ 06. 1C:Предприятие",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена, т. к. создана не в подходящем разделе. "
            "Требуется оставить заявку в подходящем разделе: 06. 1C:Предприятие. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "redirect_directum": {
        "name": "Редирект ➔ 05. Directum",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена, т. к. создана не в подходящем разделе. "
            "Требуется оставить заявку в подходящем разделе: 05. Directum. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "redirect_security": {
        "name": "Редирект ➔ 08. Информационная безопасность",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена, т. к. создана не в подходящем разделе. "
            "Требуется оставить заявку в подходящем разделе: 08. Информационная безопасность. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "redirect_printers": {
        "name": "Редирект ➔ 03. Оргтехника",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена, т. к. создана не в подходящем разделе. "
            "Требуется оставить заявку в подходящем разделе: 03. Оргтехника. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "hardware_repair": {
        "name": "Обслуживание / Ремонт ПК в 112 каб.",
        "status_id": 48,
        "status_name": "Ожидание устройства (48)",
        "expenses": 10,
        "template": (
            "Приносите системный блок / ноутбук в АБК 3, 112 каб. на диагностику, обслуживание и настройку. "
            "О времени визита вы можете написать в комментариях к этой заявке."
        ),
        "badge_color": "primary",
    },
    "duplicate_task": {
        "name": "Дубликат заявки",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена как повторная (дубликат ранее созданного инцидента). "
            "Все работы и переписка ведутся в основной заявке. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "pc_offline": {
        "name": "Не вижу ПК в сети",
        "status_id": 35,
        "status_name": "Требует уточнения (35)",
        "expenses": 5,
        "template": (
            "Не вижу ПК в сети.\n"
            "1. Убедитесь в корректности имени ПК;\n"
            "2. Перезагрузите компьютер;\n"
            "3. Проверьте подключение сетевого кабеля.\n"
            "Пожалуйста, напишите в комментариях к заявке, когда ПК будет включен и доступен в сети."
        ),
        "badge_color": "info",
    },
    "printer_offline": {
        "name": "Не вижу МФУ в сети",
        "status_id": 35,
        "status_name": "Требует уточнения (35)",
        "expenses": 5,
        "template": (
            "Не вижу МФУ в сети.\n"
            "1. Убедитесь в корректности имени/IP адреса принтера;\n"
            "2. Перезагрузите МФУ;\n"
            "3. Переподключите сетевой кабель к МФУ.\n"
            "Пожалуйста, напишите в комментариях к заявке о результатах проверки."
        ),
        "badge_color": "info",
    },
    "anydesk_fallback_assistant": {
        "name": "Сбой AnyDesk (Установка Ассистент)",
        "status_id": 35,
        "status_name": "Требует уточнения (35)",
        "expenses": 5,
        "template": (
            "Связь через AnyDesk не устанавливается. Установите программу «Ассистент» по ссылке: https://мойассистент.рф/скачать/\n"
            "После установки укажите в комментарии к этой заявке ваш идентификатор и пароль от программы."
        ),
        "badge_color": "info",
    },
    "file_lock_smb": {
        "name": "Снятие SMB-блокировки файлов",
        "status_id": 27,
        "status_name": "В работе (27)",
        "expenses": 10,
        "template": (
            "Добрый день! Уточните, пожалуйста, в комментариях к этой заявке полный путь к файлу или сетевой папке "
            "для сброса зависшей сессии на файловом сервере."
        ),
        "badge_color": "info",
    },
    "general": {
        "name": "Принятие в работу (1-я линия)",
        "status_id": 27,
        "status_name": "В работе (27)",
        "expenses": 10,
        "template": (
            "Принято в работу специалистом 1-й линии техподдержки. "
            "Пожалуйста, оставайтесь на связи и пишите ответы в комментариях к этой заявке."
        ),
        "badge_color": "secondary",
    },
    "resolved_standard": {
        "name": "Стандартное завершение заявки",
        "status_id": 29,
        "status_name": "Выполнена (29)",
        "expenses": 15,
        "template": (
            "Работы по заявке успешно выполнены. Если возникнут вопросы или потребуется помощь, "
            "пожалуйста, оставьте комментарий в этой заявке."
        ),
        "badge_color": "success",
    },
}


def _get_all_templates() -> dict[str, Any]:
    """
    Возвращает актуальный словарь шаблонов из централизованного template_engine.
    """
    loaded = load_templates()
    return loaded if loaded else DEFAULT_TEMPLATES_CATALOG


@router.get("/admin/api/templates", dependencies=[Depends(verify_admin_jwt)])
async def get_templates_catalog():
    """
    Возвращает полный каталог шаблонов ответов заявителю для быстрого выбора в UI.
    """
    templates_data = _get_all_templates()
    return {
        "total": len(templates_data),
        "templates": [
            {
                "key": key,
                **val,
            }
            for key, val in templates_data.items()
        ],
        "map": templates_data,
    }
