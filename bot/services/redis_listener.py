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
    logger.info(
        "Запуск фонового слушателя Redis Pub/Sub для каналов 'intraservice_events' и 'printer_actions'..."
    )
    while True:
        redis = None
        try:
            # Инициализация подключения к Redis
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe("intraservice_events", "printer_actions")
                logger.info(
                    "Успешная подписка на Redis Pub/Sub каналы 'intraservice_events' и 'printer_actions'."
                )

                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message is None or message.get("type") != "message":
                        continue

                    payload_str = message.get("data")
                    if not isinstance(payload_str, (str, bytes)):
                        continue
                    try:
                        payload = json.loads(payload_str)
                        tg_user_id = payload.get("tg_user_id")
                        if not tg_user_id:
                            continue

                        event_type = payload.get("event_type")
                        reply_markup = None

                        if event_type == "printer_approval_request":
                            task_id = payload.get("task_id")
                            target_pc = payload.get("target_pc") or "Не определен"
                            model_key = payload.get("model_key") or "Не определена"
                            connection_type = (
                                payload.get("connection_type") or "Не определен"
                            )
                            driver_name = payload.get("driver_name") or "Не определен"

                            text = (
                                f"⚙️ <b>Запрос на подтверждение установки принтера</b> по заявке #{task_id}\n\n"
                                f"🖥 <b>Компьютер:</b> <code>{target_pc}</code>\n"
                                f"🖨 <b>Модель:</b> <code>{model_key}</code>\n"
                                f"🔌 <b>Тип подключения:</b> <code>{connection_type}</code>\n"
                                f"📄 <b>Драйвер:</b> <code>{driver_name}</code>\n\n"
                                f"Пожалуйста, подтвердите установку или измените параметры."
                            )
                            if task_id:
                                reply_markup = get_approval_keyboard(task_id)
                        else:
                            text = payload.get("text")
                            if not text:
                                continue

                            is_printer_approval = payload.get(
                                "is_printer_approval", False
                            )
                            task_id = payload.get("task_id")

                            if is_printer_approval and task_id:
                                reply_markup = get_approval_keyboard(task_id)

                        try:
                            # Теперь мы передаем reply_markup в метод send_message
                            await bot.send_message(
                                chat_id=tg_user_id,
                                text=text,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                            )
                        except TelegramForbiddenError as e:
                            logger.warning(
                                "Пользователь %s заблокировал бота: %s", tg_user_id, e
                            )
                            try:
                                await api_client.logout(tg_user_id)
                            except Exception as logout_err:
                                logger.error(
                                    "Ошибка при разлогинивании пользователя %s: %s",
                                    tg_user_id,
                                    logout_err,
                                )
                        except TelegramBadRequest as e:
                            if "chat not found" in str(e).lower():
                                logger.warning(
                                    "Чат с пользователем %s не найден. Выполняем разлогин.",
                                    tg_user_id,
                                )
                                try:
                                    await api_client.logout(tg_user_id)
                                except Exception as logout_err:
                                    logger.error(
                                        "Ошибка при разлогинивании пользователя %s: %s",
                                        tg_user_id,
                                        logout_err,
                                    )
                            else:
                                logger.error(
                                    "Ошибка API Telegram при отправке пользователю %s: %s",
                                    tg_user_id,
                                    e,
                                )
                        except Exception as e:
                            logger.error(
                                "Неизвестная ошибка при отправке уведомления пользователю %s: %s",
                                tg_user_id,
                                e,
                            )
                    except json.JSONDecodeError:
                        logger.error(
                            "Ошибка декодирования JSON из Redis: %s", payload_str
                        )
        except asyncio.CancelledError:
            logger.info("Слушатель Redis Pub/Sub был отменен/остановлен.")
            break
        except Exception as e:
            logger.exception(
                "Сетевая ошибка или сбой подключения к Redis. Повторное подключение через 5 секунд... Ошибка: %s",
                e,
            )
            await asyncio.sleep(5)
        finally:
            if redis is not None:
                await redis.close()  # Гарантированное закрытие соединения
