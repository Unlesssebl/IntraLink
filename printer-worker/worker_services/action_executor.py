import logging
from orchestrator.schemas import PrintJob, ErrorType
from action_config import ACTIONS, ActionRule, ERROR_RULES, STATUS_WAITING
from worker_services.api_client import update_task_status, add_task_comment

logger = logging.getLogger(__name__)

async def execute_action(action_name: str, job: PrintJob, error_detail: str = "") -> None:
    """
    Применяет действие по имени из конфигурации action_config.py
    """
    if job.is_manual:
        return

    rule: ActionRule | None = ACTIONS.get(action_name)
    if not rule:
        logger.warning(f"Неизвестное действие '{action_name}'. Пропуск.")
        return

    status_id = rule.status_id
    comment_template = rule.comment_template
    
    # Адаптивный выбор шаблона для глобальной ошибки
    comment_text = None
    if action_name == "on_error" and error_detail:
        # Сначала пытаемся найти подходящее правило в ERROR_RULES
        found_rule = False
        for error_rule in ERROR_RULES:
            if any(kw.lower() in error_detail.lower() for kw in error_rule["keywords"]):
                logger.info("Найдено адаптивное правило для ошибки. Шаблон заменен.")
                comment_template = error_rule["template"]
                status_id = STATUS_WAITING
                found_rule = True
                break
        
        if not found_rule:
            if job.error_type == ErrorType.USER:
                # Ошибка пользовательская и нет специального правила — отправляем как есть (fallback)
                logger.info("Тип ошибки USER без правила: отправка готового комментария пользователю.")
                comment_text = error_detail
                status_id = STATUS_WAITING

    # Если смены статуса и комментария нет, выходим
    if status_id is None and not comment_template and not comment_text:
        logger.info(f"Действие '{action_name}' не требует уведомления пользователя или смены статуса. Пропуск.")
        return

    # Обновление статуса
    if status_id is not None:
        try:
            await update_task_status(job.tg_user_id, job.task_id, status_id)
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса задачи #{job.task_id} (действие {action_name}): {e}")

    # Добавление комментария
    if comment_text is None and comment_template:
        printer_name = job.driver_info.display_name if job.driver_info else "Неизвестно"
        target_pc = job.target_pc or "Неизвестно"
        connection_type = job.connection_type.value if job.connection_type else "Неизвестно"
        printer_address = job.printer_address or "Неизвестно"
        
        try:
            comment_text = comment_template.format(
                printer_name=printer_name,
                target_pc=target_pc,
                error=error_detail,
                connection_type=connection_type,
                printer_address=printer_address
            )
        except Exception as e:
            logger.error(f"Ошибка при форматировании комментария задачи #{job.task_id}: {e}")
            comment_text = comment_template

    if comment_text:
        try:
            await add_task_comment(job.tg_user_id, job.task_id, comment_text)
        except Exception as e:
            logger.error(f"Ошибка при добавлении комментария к задаче #{job.task_id} (действие {action_name}): {e}")

