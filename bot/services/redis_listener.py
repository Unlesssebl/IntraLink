import logging
import json
import asyncio
import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from config import REDIS_URL
from services.api_client import api_client

# ИМПОРТ КЛАВИАТУРЫ ИЗ НАШЕГО РОУТЕРА
from handlers.printer_approvals import get_approval_keyboard

logger = logging.getLogger(__name__)

async def start_redis_listener(bot: Bot):
    """
    Асинхронная функция, которая слушает события из Redis Pub/Sub и пересылает их пользователям Telegram.
    """
    logger.info("Запуск фонового слушателя Redis Pub/Sub для канала 'intraservice_events'...")
    while True:
        redis = None
        try:
            # Инициализация подключения к Redis
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe("intraservice_events")
                logger.info("Успешная подписка на Redis Pub/Sub канал 'intraservice_events'.")
                
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message is None or message.get("type") != "message":
                        continue
                    
                    payload_str = message.get("data")
                    if not isinstance(payload_str, (str, bytes)):
                        continue
                    try:
                        payload = json.loads(payload_str)
                        tg_user_id = payload.get("tg_user_id")
                        text = payload.get("text")
                        
                        if not tg_user_id or not text:
                            continue
                            
                        # ДОБАВЛЕНА ЛОГИКА ДЛЯ КНОПОК
                        # Воркер должен присылать флаг is_printer_approval=True и task_id 
                        # в payload события Redis
                        is_printer_approval = payload.get("is_printer_approval", False)
                        task_id = payload.get("task_id")
                        
                        reply_markup = None
                        if is_printer_approval and task_id:
                            reply_markup = get_approval_keyboard(task_id)

                        try:
                            # Теперь мы передаем reply_markup в метод send_message
                            await bot.send_message(
                                chat_id=tg_user_id, 
                                text=text, 
                                parse_mode="HTML",
                                reply_markup=reply_markup
                            )
                        except TelegramForbiddenError as e:
                            logger.warning("Пользователь %s заблокировал бота: %s", tg_user_id, e)
                            try:
                                await api_client.logout(tg_user_id)
                            except Exception as logout_err:
                                logger.error("Ошибка при разлогинивании пользователя %s: %s", tg_user_id, logout_err)
                        except TelegramBadRequest as e:
                            if "chat not found" in str(e).lower():
                                logger.warning("Чат с пользователем %s не найден. Выполняем разлогин.", tg_user_id)
                                try:
                                    await api_client.logout(tg_user_id)
                                except Exception as logout_err:
                                    logger.error("Ошибка при разлогинивании пользователя %s: %s", tg_user_id, logout_err)
                            else:
                                logger.error("Ошибка API Telegram при отправке пользователю %s: %s", tg_user_id, e)
                        except Exception as e:
                            logger.error("Неизвестная ошибка при отправке уведомления пользователю %s: %s", tg_user_id, e)
                    except json.JSONDecodeError:
                        logger.error("Ошибка декодирования JSON из Redis: %s", payload_str)
        except asyncio.CancelledError:
            logger.info("Слушатель Redis Pub/Sub был отменен/остановлен.")
            break
        except Exception as e:
            logger.exception("Сетевая ошибка или сбой подключения к Redis. Повторное подключение через 5 секунд... Ошибка: %s", e)
            await asyncio.sleep(5)
        finally:
            if redis is not None:
                await redis.close() # Гарантированное закрытие соединения