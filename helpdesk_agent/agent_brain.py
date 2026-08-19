import difflib
import logging
from typing import Any
from pydantic import BaseModel, Field

from diagnostics import extract_potential_hosts, run_host_diagnostics, format_diagnostics_summary

logger = logging.getLogger("helpdesk_agent.brain")

# Статусы IntraService
STATUS_OPEN = 31
STATUS_IN_PROGRESS = 27
STATUS_NEED_CLARIFICATION = 35
STATUS_RESOLVED = 29
STATUS_CANCELED = 30
STATUS_WAIT_DEVICE = 48


class AgentDecision(BaseModel):
    action_type: str = Field(
        description="Тип действия: 'take_in_progress', 'need_clarification', 'redirect_and_cancel', 'wait_device', 'resolve'"
    )
    new_status_id: int = Field(
        description="Целевой ID статуса (27=В работе, 35=Требует уточнения, 30=Отменена, 48=Ожидание устройства, 29=Выполнена)"
    )
    new_status_name: str = Field(
        description="Понятное название статуса на русском языке ('В работе', 'Требует уточнения', 'Отменена', 'Ожидание устройства', 'Выполнена')"
    )
    proposed_comment: str = Field(
        description="Текст комментария пользователю СТРОГО в лаконичном стиле инженера Беликова Алена по его шаблонам"
    )
    reason: str = Field(
        description="Краткое техническое обоснование принятого решения для оператора"
    )
    confidence: float = Field(
        description="Уверенность в решении от 0.0 до 1.0"
    )
    correct_service_id: int | None = Field(
        default=None,
        description="ID правильного раздела каталога услуг (если перенаправляется/отменяется)",
    )
    correct_service_name: str | None = Field(
        default=None,
        description="Точное официальное название правильного раздела (если заявка отменяется)",
    )
    diagnostics_info: str | None = Field(
        default=None,
        description="Сводка результатов сетевой диагностики ПК",
    )


class HelpdeskAgentBrain:
    def __init__(self):
        self._service_catalog: list[dict[str, Any]] = []

    def set_service_catalog(self, catalog: list[dict[str, Any]]):
        """Устанавливает актуальный каталог услуг."""
        self._service_catalog = catalog

    def normalize_service_name(self, name_to_check: str) -> tuple[int | None, str | None]:
        """Ищет точное или наиболее близкое официальное имя раздела в каталоге."""
        if not self._service_catalog or not name_to_check:
            return None, name_to_check

        clean_target = name_to_check.strip().lower()
        # 1. Точное совпадение
        for item in self._service_catalog:
            item_name = item.get("Name", "")
            if item_name.strip().lower() == clean_target:
                return item.get("Id"), item_name

        # 2. Нечеткий поиск (Fuzzy match)
        names = [item.get("Name", "") for item in self._service_catalog if item.get("Name")]
        matches = difflib.get_close_matches(name_to_check, names, n=1, cutoff=0.55)
        if matches:
            matched_name = matches[0]
            for item in self._service_catalog:
                if item.get("Name") == matched_name:
                    return item.get("Id"), matched_name

        return None, name_to_check

    async def analyze_and_decide(self, task: dict[str, Any]) -> AgentDecision:
        """
        Анализирует заявку, выполняет сетевую диагностику и формирует проект решения.
        """
        name = task.get("Name") or ""
        desc = task.get("Description") or ""
        service_name = task.get("ServiceName") or ""
        parsed_fields = task.get("_parsed_fields") or {}
        full_text = f"{name} {desc}".lower()

        # 1. Сетевая диагностика целевого хоста (если найден в полях/описании)
        potential_hosts = extract_potential_hosts(f"{name} {desc}", parsed_fields)
        diag_summary = None
        is_pc_online = None
        if potential_hosts:
            target_host = potential_hosts[0]
            try:
                diag = await run_host_diagnostics(target_host)
                diag_summary = format_diagnostics_summary(diag)
                is_pc_online = diag.get("is_online")
            except Exception as e:
                logger.warning("Ошибка диагностики хоста %s: %s", target_host, e)

        # 2. Правило: Сетевые проблемы / Недоступен ПК
        if is_pc_online is False or any(k in full_text for k in ["не видит сеть", "нет сети", "нет интернета", "не доступен диск", "сетевой кабель", "нет подключения"]):
            comment = (
                "Не вижу ПК в сети.\n"
                "1. Убедитесь в корректности имени ПК;\n"
                "2. Перезагрузите компьютер;\n"
                "3. Проверьте подключение сетевого кабеля;\n"
                "4. Если кабель подключен, проверьте наличие световой индикации в месте подключения кабеля.\n"
                "По вопросам звоните на номер 49-87."
            )
            return AgentDecision(
                action_type="need_clarification",
                new_status_id=STATUS_NEED_CLARIFICATION,
                new_status_name="Требует уточнения",
                proposed_comment=comment,
                reason="ПК не отвечает на сетевые запросы / заявлен сбой сети",
                confidence=0.9,
                diagnostics_info=diag_summary,
            )

        # 3. Правило: Аппаратный сбой / Ремонт / Замена диска / Новый системник
        if any(k in full_text for k in ["новый процессор", "не включается", "гудит", "трещит", "замена диска", "новый системный блок", "синий экран", "bios", "установка ос", "принести системный", "замена комплектующих", "замена клавиатуры"]):
            comment = "Приносите ПК в АБК 3, 112 каб."
            return AgentDecision(
                action_type="wait_device",
                new_status_id=STATUS_WAIT_DEVICE,
                new_status_name="Ожидание устройства",
                proposed_comment=comment,
                reason="Аппаратная неисправность или необходимость настройки системного блока/оборудования в отделе",
                confidence=0.95,
                diagnostics_info=diag_summary,
            )

        # 4. Правило: Wi-Fi доступ (WORK-NET)
        if any(k in full_text for k in ["wi-fi", "wifi", "work-net", "вай фай", "вайфай"]):
            comment = (
                "Доступ к Wi-Fi предоставлен. \n"
                "Используйте логин и пароль от вашей учетной записи на ПК. Инструкцию по подключению приложил.\n"
                "Если возникнут проблемы с подключением, приходите в АБК-3, кабинет 112."
            )
            return AgentDecision(
                action_type="resolve",
                new_status_id=STATUS_RESOLVED,
                new_status_name="Выполнена",
                proposed_comment=comment,
                reason="Предоставление доступа к корпоративному Wi-Fi WORK-NET",
                confidence=0.9,
                diagnostics_info=diag_summary,
            )

        # 5. Правило: Стандартное взятие в работу
        comment = "Ваша заявка принята в работу. По вопросам звоните на номер 49-87."
        return AgentDecision(
            action_type="take_in_progress",
            new_status_id=STATUS_IN_PROGRESS,
            new_status_name="В работе",
            proposed_comment=comment,
            reason="Штатный инцидент 1-й линии техподдержки",
            confidence=0.85,
            diagnostics_info=diag_summary,
        )
