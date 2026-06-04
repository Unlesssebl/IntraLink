import logging
from datetime import datetime
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from services.api_client import api_client
from utils import parse_api_date
from config import INTRAService_URL

logger = logging.getLogger(__name__)

class UserDeactivated(Exception):
    pass

async def send_notification(bot: Bot, tg_id: int, text: str, parse_mode: str = "HTML"):
    try:
        await bot.send_message(tg_id, text, parse_mode=parse_mode)
    except TelegramForbiddenError:
        logger.warning("Бот заблокирован пользователем %s. Разлогиниваем.", tg_id)
        await api_client.logout(tg_id)
        raise UserDeactivated()
    except TelegramBadRequest as e:
        if "chat not found" in str(e).lower():
            logger.warning("Чат с пользователем %s не найден. Разлогиниваем.", tg_id)
            await api_client.logout(tg_id)
            raise UserDeactivated()
        raise

async def check_updates(bot: Bot):
    """
    Периодическая проверка новых заявок и комментариев.
    Использует объекты datetime для корректного сравнения и мапинг статусов.
    """
    base_web_url = INTRAService_URL.replace("/api/", "")
    try:
        users = await api_client.get_all_users()
        if not users:
            return
            
        for user in users:
            try:
                tg_id = user.get('tg_user_id')
                is_user_id = user.get('is_user_id')
                last_task_id = user.get('last_task_id') or 0
                last_check_str = user.get('last_check_time')
                
                if not tg_id or not is_user_id:
                    continue

                current_time = datetime.now()
                last_check_time = parse_api_date(last_check_str) if last_check_str else None
                
                if not last_check_time:
                    await api_client.update_user_state(
                        tg_id, 
                        last_check_time=current_time.strftime("%Y-%m-%d %H:%M:%S")
                    )
                    continue
                
                api_filter_time = last_check_time.strftime("%Y-%m-%d %H:%M")
                
                # 1. Проверка НОВЫХ заявок
                tasks_data = await api_client.get_tasks(tg_id, {"CreatedMoreThan": api_filter_time})
                
                new_tasks = []
                statuses_map = {}
                if isinstance(tasks_data, dict):
                    new_tasks = tasks_data.get("Tasks", [])
                    for s in tasks_data.get("Statuses", []):
                        statuses_map[s.get("Id")] = s.get("Name")
                elif isinstance(tasks_data, list):
                    new_tasks = tasks_data

                any_new_task = False
                for task in new_tasks:
                    if task["Id"] > last_task_id:
                        status_name = task.get("StatusName")
                        if not status_name and task.get("StatusId") in statuses_map:
                            status_name = statuses_map[task.get("StatusId")]
                        
                        status_name = status_name or "N/A"
                        
                        await send_notification(
                            bot,
                            tg_id, 
                            f"🆕 <b>Новая заявка #{task['Id']}</b>\n"
                            f"📝 Тема: {task['Name']}\n"
                            f"📊 Статус: {status_name}\n"
                            f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>Открыть в браузере</a>",
                            parse_mode="HTML"
                        )
                        last_task_id = max(last_task_id, task["Id"])
                        any_new_task = True
                
                if any_new_task:
                    await api_client.update_user_state(tg_id, last_task_id=last_task_id)

                # 2. Проверка КОММЕНТАРИЕВ
                updated_tasks_data = await api_client.get_tasks(tg_id, {
                    "ChangedMoreThan": api_filter_time,
                    "include": "executorids,status"
                })
                
                updated_tasks = []
                updated_statuses_map = {}
                if isinstance(updated_tasks_data, dict):
                    updated_tasks = updated_tasks_data.get("Tasks", [])
                    for s in updated_tasks_data.get("Statuses", []):
                        updated_statuses_map[s.get("Id")] = s.get("Name")
                elif isinstance(updated_tasks_data, list):
                    updated_tasks = updated_tasks_data

                for task in updated_tasks:
                    executor_ids_str = str(task.get("ExecutorIds") or "")
                    executor_ids = [eid.strip() for eid in executor_ids_str.split(",") if eid.strip()]
                    
                    if str(is_user_id) not in executor_ids:
                        continue
                    
                    lifetime_data = await api_client.get_task_lifetime(tg_id, task["Id"])
                    
                    events = []
                    if isinstance(lifetime_data, list):
                        events = lifetime_data
                    elif isinstance(lifetime_data, dict) and "TaskLifetimes" in lifetime_data:
                        events = lifetime_data["TaskLifetimes"]
                    
                    if events:
                        for event in events:
                            if not isinstance(event, dict):
                                continue
                                
                            raw_date = event.get("Date")
                            event_date = parse_api_date(raw_date)
                            
                            if event_date and event_date > last_check_time:
                                if event.get("Comments"):
                                    await send_notification(
                                        bot,
                                        tg_id, 
                                        f"💬 <b>Новый комментарий в заявке #{task['Id']}</b> от <i>{event.get('Editor', 'Unknown')}</i>:\n{event['Comments']}\n"
                                        f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>Открыть в браузере</a>",
                                        parse_mode="HTML"
                                    )
                                elif event.get("StatusId"):
                                    status_name = task.get("StatusName")
                                    if not status_name and task.get("StatusId") in updated_statuses_map:
                                        status_name = updated_statuses_map[task.get("StatusId")]
                                    
                                    status_name = status_name or "N/A"
                                    
                                    await send_notification(
                                        bot,
                                        tg_id, 
                                        f"🔄 <b>Статус заявки #{task['Id']} изменен</b> на: {status_name}\n"
                                        f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>Открыть в браузере</a>",
                                        parse_mode="HTML"
                                    )
                
                await api_client.update_user_state(
                    tg_id, 
                    last_check_time=current_time.strftime("%Y-%m-%d %H:%M:%S")
                )
                
            except UserDeactivated:
                continue
            except Exception as e:
                logger.error("Ошибка при обработке обновлений для пользователя %s: %s", tg_id, e)
        
    except Exception as e:
        logger.exception("Критическая ошибка в check_updates: %s", e)

