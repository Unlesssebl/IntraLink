import logging
import json
import asyncio
import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from config import REDIS_URL
from services.api_client import api_client

logger = logging.getLogger(__name__)

async def start_redis_listener(bot: Bot):
    """
    Асинхронная функция, которая слушает события из Redis Pub/Sub и пересылает их пользователям Telegram.
    """
    logger.info("Запуск фонового слушателя Redis Pub/Sub для канала 'intraservice_events'...")
    while True:
        try:
            # Инициализация подключения к Redis
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            pubsub = redis.pubsub()
            await pubsub.subscribe("intraservice_events")
            logger.info("Успешная подписка на Redis Pub/Sub канал 'intraservice_events'.")
            
            async for message in pubsub.listen():
                if message is None or message["type"] != "message":
                    continue
                
                payload_str = message["data"]
                try:
                    payload = json.loads(payload_str)
                except Exception as e:
                    logger.error("Ошибка декодирования JSON из Redis: %s. Данные: %s", e, payload_str)
                    continue
                
                tg_user_id = payload.get("tg_user_id")
                msg_text = payload.get("message")
                
                if not tg_user_id or not msg_text:
                    logger.warning("Получено некорректное сообщение из Redis Pub/Sub: %s", payload)
                    continue
                
                try:
                    await bot.send_message(tg_user_id, msg_text, parse_mode="HTML")
                    logger.info("Уведомление по событию %s успешно отправлено пользователю %s", payload.get("event_type"), tg_user_id)
                except TelegramForbiddenError:
                    logger.warning("Бот заблокирован пользователем %s. Выполняем разлогин.", tg_user_id)
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
                    
        except asyncio.CancelledError:
            logger.info("Слушатель Redis Pub/Sub был отменен/остановлен.")
            break
        except Exception as e:
            logger.exception("Сетевая ошибка или сбой подключения к Redis. Повторное подключение через 5 секунд... Ошибка: %s", e)
            await asyncio.sleep(5)
