"""
Конечный автомат жизненного цикла заявок (Lifecycle State Machine).
Инкапсулирует правила и защитные условия (Guards) перехода между статусами IntraService:
  - 31 (Открыта) -> 35 (Требует уточнения) при нехватке реквизитов
  - 31 (Открыта) -> 27 (В работе) при готовности к исполнению
  - 35 (Требует уточнения) -> 31 (Открыта) при получении реквизитов
  - 35 (Требует уточнения) -> 30 (Отменена) при отказе заявителя
  - 27 (В работе) -> 29 (Выполнена) при подтвержденном успехе
  - 27 (В работе) -> 35 (Требует уточнения) при выключенном ПК заявителя
  - 27 (В работе) -> 31 (Открыта) + эскалация человеку при техническом сбое
"""

import logging
from typing import Any, Optional

from app.config import settings
from app.services.lifecycle.intent_analyzer import IntentAnalyzer
from app.services.lifecycle.models import (
    IntentAnalysisResult,
    LifecycleStepResult,
    TicketLifecycleState,
    UserReplyIntent,
)

logger = logging.getLogger("core_api.services.lifecycle.state_machine")


class LifecycleStateMachine:
    """Детерминированная машина состояний жизненного цикла заявки."""

    @classmethod
    def evaluate_open_task(cls, task: dict[str, Any]) -> LifecycleStepResult:
        """
        Оценка открытой заявки (статус 31):
        1. Проверка правил полноты данных.
        2. Если не хватает реквизитов (например, IP принтера) -> перевод в статус 35.
        3. Если все реквизиты есть -> готовность к исполнению и перевод в статус 27.
        """
        task_id = int(task.get("Id") or 0)
        name = (task.get("Name") or "").lower()
        desc = (task.get("Description") or "").lower()
        full_text = f"{name} {desc}"

        is_printer_topic = any(
            w in full_text
            for w in [
                "принтер", "мфу", "печать", "kyocera", "ecosys", "hp laserjet",
                "canon", "xerox", "pantum", "пантум", "драйвер"
            ]
        )

        if is_printer_topic:
            # Извлекаем PC и IP из кастомных полей или текста
            pc_name = ""
            printer_ip = ""

            for cf in task.get("CustomFields", []):
                fid = cf.get("CustomFieldId") or cf.get("FieldId")
                val = str(cf.get("Value") or "").strip()
                if fid == settings.PRINTER_PC_CUSTOM_FIELD_ID and val:
                    pc_name = val
                elif fid == settings.PRINTER_IP_CUSTOM_FIELD_ID and val:
                    printer_ip = val

            if not pc_name or not printer_ip:
                regex_res = IntentAnalyzer.analyze_fast_regex(full_text)
                if regex_res:
                    if not pc_name and regex_res.extracted_pc:
                        pc_name = regex_res.extracted_pc
                    if not printer_ip and regex_res.extracted_ip:
                        printer_ip = regex_res.extracted_ip

            # Если IP адрес принтера отсутствует -> запрашиваем уточнение (статус 35)
            if not printer_ip:
                logger.info("Задача #%d: не указан IP принтера. Перевод в статус 35.", task_id)
                return LifecycleStepResult(
                    task_id=task_id,
                    action_taken="request_clarification",
                    previous_status_id=settings.STATUS_OPEN_ID,
                    target_status_id=settings.STATUS_WAITING_ID,
                    comment=(
                        "Добрый день! Для подключения сетевого принтера укажите, пожалуйста, "
                        "в комментариях к заявке IP-адрес сетевого принтера (указан на корпусе устройства "
                        "или в отчете о конфигурации сети принтера)."
                    ),
                    expenses=0,
                    success=True,
                )

            # Все реквизиты есть -> готова к исполнению (статус 27)
            logger.info("Задача #%d: реквизиты в наличии (PC=%s, IP=%s). Готова к исполнению.", task_id, pc_name, printer_ip)
            return LifecycleStepResult(
                task_id=task_id,
                action_taken="dispatch_execution",
                previous_status_id=settings.STATUS_OPEN_ID,
                target_status_id=settings.STATUS_IN_PROGRESS_ID,
                comment="Заявка принята к автоматическому исполнению платформой IntraLink.",
                expenses=0,
                success=True,
            )

        # Для других услуг, где нет готового автоисполнителя
        return LifecycleStepResult(
            task_id=task_id,
            action_taken="no_action_needed",
            previous_status_id=settings.STATUS_OPEN_ID,
            target_status_id=settings.STATUS_OPEN_ID,
            success=True,
        )

    @classmethod
    def evaluate_waiting_task(
        cls,
        task: dict[str, Any],
        intent_result: IntentAnalysisResult,
    ) -> LifecycleStepResult:
        """
        Оценка заявки в статусе ожидания (статус 35) по ответу заявителя:
        1. PROVIDE_DATA -> возобновление в статус 31.
        2. CANCEL_REQUEST -> отмена в статус 30.
        3. CLARIFICATION_QUESTION -> пояснение от бота (остается в 35).
        4. UNSUPPORTED -> эскалация живому инженеру.
        """
        task_id = int(task.get("Id") or 0)

        # 1. Заявитель предоставил недостающие данные
        if intent_result.intent == UserReplyIntent.PROVIDE_DATA:
            logger.info("Задача #%d: заявитель предоставил реквизиты (%s). Возобновление в статус 31.", task_id, intent_result.summary)
            return LifecycleStepResult(
                task_id=task_id,
                action_taken="resume_to_open",
                previous_status_id=settings.STATUS_WAITING_ID,
                target_status_id=settings.STATUS_OPEN_ID,
                comment="Получены уточненные сетевые параметры. Заявка возвращена в работу для автоматической установки.",
                expenses=0,
                success=True,
            )

        # 2. Заявитель просит отменить/закрыть заявку
        if intent_result.intent == UserReplyIntent.CANCEL_REQUEST:
            logger.info("Задача #%d: заявитель запросил отмену заявки. Перевод в статус 30.", task_id)
            return LifecycleStepResult(
                task_id=task_id,
                action_taken="cancel_by_user",
                previous_status_id=settings.STATUS_WAITING_ID,
                target_status_id=settings.STATUS_CANCELLED_ID,
                comment="Заявка отменена по запросу заявителя в комментариях.",
                expenses=0,
                success=True,
            )

        # 3. Заявитель уточняет, где найти реквизиты
        if intent_result.intent == UserReplyIntent.CLARIFICATION_QUESTION:
            logger.info("Задача #%d: заявитель задал вопрос о расположении реквизитов.", task_id)
            reply_text = intent_result.suggested_reply or (
                "IP-адрес сетевого принтера обычно указан на наклейке на корпусе устройства. "
                "Также его можно распечатать через панель управления принтера: Меню -> Отчеты -> Конфигурация сети."
            )
            return LifecycleStepResult(
                task_id=task_id,
                action_taken="reply_clarification",
                previous_status_id=settings.STATUS_WAITING_ID,
                target_status_id=settings.STATUS_WAITING_ID,
                comment=reply_text,
                expenses=0,
                success=True,
            )

        # 4. Нестандартный ответ или претензия -> снятие с бота и передача человеку
        logger.info("Задача #%d: нестандартный ответ заявителя. Эскалация на живого инженера.", task_id)
        return LifecycleStepResult(
            task_id=task_id,
            action_taken="escalate_to_human",
            previous_status_id=settings.STATUS_WAITING_ID,
            target_status_id=settings.STATUS_OPEN_ID,
            comment="Получен ответ заявителя, требующий индивидуального рассмотрения. Заявка передана инженеру первой линии.",
            expenses=0,
            success=True,
            escalated_to_human=True,
        )

    @classmethod
    def evaluate_execution_result(
        cls,
        task: dict[str, Any],
        is_success: bool,
        error_message: str = "",
        job_payload: Optional[dict[str, Any]] = None,
    ) -> LifecycleStepResult:
        """
        Оценка завершения технического действия Execution Worker (статус 27):
        1. Успех -> перевод в 29 (Выполнена) со списанием 15 минут норматива.
        2. ПК офлайн -> перевод в 35 (Требует уточнения) с просьбой включить ПК.
        3. Технический сбой -> возврат в 31 (Открыта) + эскалация инженеру.
        """
        task_id = int(task.get("Id") or 0)

        if is_success:
            logger.info("Задача #%d: техническое действие успешно выполнено. Перевод в статус 29.", task_id)
            return LifecycleStepResult(
                task_id=task_id,
                action_taken="complete_success",
                previous_status_id=settings.STATUS_IN_PROGRESS_ID,
                target_status_id=settings.STATUS_COMPLETED_ID,
                comment=(
                    "Добрый день! Сетевой принтер успешно установлен и настроен на вашей рабочей станции. "
                    "Тестовая страница отправлена на печать. "
                    "(Выполнено автономным сервисом IntraLink)"
                ),
                expenses=settings.AUTONOMOUS_AUTO_EXPENSES_MINUTES,
                success=True,
            )

        # Анализ ошибки
        err_lower = (error_message or "").lower()
        is_offline = any(
            w in err_lower
            for w in ("offline", "хост недоступен", "не пингуется", "ping failed", "выключен", "100% loss")
        )

        if is_offline:
            logger.info("Задача #%d: ПК заявителя недоступен. Перевод в статус 35.", task_id)
            return LifecycleStepResult(
                task_id=task_id,
                action_taken="request_pc_power_on",
                previous_status_id=settings.STATUS_IN_PROGRESS_ID,
                target_status_id=settings.STATUS_WAITING_ID,
                comment=(
                    "Добрый день! Не удалось выполнить удаленную установку принтера, так как ваш компьютер "
                    "выключен или недоступен в локальной сети. Пожалуйста, включите ПК и сообщите в комментариях к заявке."
                ),
                expenses=0,
                success=True,
            )

        # Внутренний технический сбой (WinRM, RPC, драйвер)
        logger.warning("Задача #%d: технический сбой исполнения (%s). Эскалация человеку.", task_id, error_message)
        return LifecycleStepResult(
            task_id=task_id,
            action_taken="escalate_technical_error",
            previous_status_id=settings.STATUS_IN_PROGRESS_ID,
            target_status_id=settings.STATUS_OPEN_ID,
            comment=(
                f"Автоматическая установка приостановлена из-за технической ошибки: {error_message[:150]}. "
                "Заявка передана на ручное исполнение инженеру 2-й линии."
            ),
            expenses=0,
            success=False,
            error=error_message,
            escalated_to_human=True,
        )
