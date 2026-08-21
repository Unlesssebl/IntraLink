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


async def _handle_message_payload(bot: Bot, payload: dict):
    """Отправляет отформатированное сообщение пользователю Telegram."""
    tg_user_id = payload.get("tg_user_id")
    if not tg_user_id:
        return

    event_type = payload.get("event_type")
    reply_markup = None

    if event_type == "printer_approval_request":
        task_id = payload.get("task_id")
        target_pc = payload.get("target_pc") or "Не определен"
        model_key = payload.get("model_key") or "Не определена"
        connection_type = payload.get("connection_type") or "Не определен"
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
            return

        is_printer_approval = payload.get("is_printer_approval", False)
        task_id = payload.get("task_id")
        if is_printer_approval and task_id:
            reply_markup = get_approval_keyboard(task_id)

    try:
        await bot.send_message(
            chat_id=tg_user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
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


async def _listen_printer_actions_pubsub(redis: aioredis.Redis, bot: Bot):
    """Слушает интерактивные запросы подтверждения из printer_actions."""
    try:
        async with redis.pubsub() as pubsub:
            await pubsub.subscribe("printer_actions")
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None or message.get("type") != "message":
                    await asyncio.sleep(0.1)
                    continue

                payload_str = message.get("data")
                if not isinstance(payload_str, (str, bytes)):
                    continue

                try:
                    try:
                        import orjson
                        payload = orjson.loads(payload_str)
                    except ImportError:
                        payload = json.loads(payload_str)

                    if payload.get("event_type") == "printer_approval_request":
                        await _handle_message_payload(bot, payload)
                except Exception as e:
                    logger.error("Ошибка обработки printer_actions: %s", e)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Сбой подписчика printer_actions в боте: %s", e)


async def start_redis_listener(bot: Bot):
    """
    Асинхронная функция, которая слушает события из Redis Streams и пересылает их пользователям Telegram.
    """
    logger.info("Запуск фонового слушателя Redis Streams для Telegram-бота...")
    STREAM_NAME = "stream:intraservice_events"
    GROUP_NAME = "bot_group"
    CONSUMER_NAME = "bot_1"

    while True:
        redis = None
        action_task = None
        try:
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)

            try:
                await redis.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
                logger.info("Создана Consumer Group '%s' для стрима '%s'", GROUP_NAME, STREAM_NAME)
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    logger.debug("Инициализация Consumer Group в боте: %s", e)

            action_task = asyncio.create_task(_listen_printer_actions_pubsub(redis, bot))

            while True:
                try:
                    entries = await redis.xreadgroup(
                        groupname=GROUP_NAME,
                        consumername=CONSUMER_NAME,
                        streams={STREAM_NAME: ">"},
                        count=10,
                        block=2000,
                    )
                except Exception as read_err:
                    logger.warning("Ошибка чтения из стрима %s в боте: %s", STREAM_NAME, read_err)
                    await asyncio.sleep(2)
                    continue

                if not entries:
                    continue

                for stream, messages in entries:
                    for msg_id, data in messages:
                        payload_str = data.get("payload") if isinstance(data, dict) else None
                        if not payload_str:
                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)
                            continue

                        try:
                            try:
                                import orjson
                                payload = orjson.loads(payload_str)
                            except ImportError:
                                payload = json.loads(payload_str)

                            await _handle_message_payload(bot, payload)
                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)
                        except Exception as e:
                            logger.error("Ошибка при обработке сообщения %s в боте: %s", msg_id, e)
                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)

        except asyncio.CancelledError:
            logger.info("Слушатель Redis Streams в боте остановлен.")
            if action_task:
                action_task.cancel()
            break
        except Exception as e:
            logger.exception(
                "Сетевая ошибка или сбой подключения к Redis. Повторное подключение через 5 секунд... Ошибка: %s",
                e,
            )
            if action_task:
                action_task.cancel()
            await asyncio.sleep(5)
        finally:
            if redis is not None:
                await redis.close()
