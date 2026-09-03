import asyncio
import logging
from typing import Any
import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import REDIS_URL
from services.api_client import api_client

logger = logging.getLogger(__name__)

STREAM_NAME = "stream:intraservice_events"
GROUP_NAME = "bot_group"
CONSUMER_NAME = "bot_1"


async def _handle_message_payload(bot: Bot, payload: dict[str, Any]):
    """Отправляет отформатированное сообщение пользователю Telegram."""
    tg_user_id = payload.get("tg_user_id")
    if not tg_user_id:
        return

    text = payload.get("text")
    if not text:
        return

    try:
        await bot.send_message(
            chat_id=tg_user_id,
            text=text,
            parse_mode="HTML",
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


async def _autoclaim_pending_messages(redis: aioredis.Redis, bot: Bot):
    """
    Фоновая периодическая задача автовосстановления зависших сообщений (Pending Entries List).
    Если бот перезагружался или упал во время отправки, сообщения старше 30 секунд будут
    перехвачены, обработаны и подтверждены через XACK.
    """
    while True:
        try:
            await asyncio.sleep(30)
            # xautoclaim перехватывает сообщения, находящиеся в PEL > 30000 мс
            res = await redis.xautoclaim(
                name=STREAM_NAME,
                groupname=GROUP_NAME,
                consumername=CONSUMER_NAME,
                min_idle_time=30000,
                start_id="0-0",
                count=20,
            )
            if not res or len(res) < 2:
                continue

            messages = res[1]
            if not messages:
                continue

            logger.info("XAUTOCLAIM: Найдено %s зависших сообщений в стриме %s. Доставка...", len(messages), STREAM_NAME)
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
                        import json
                        payload = json.loads(payload_str)

                    await _handle_message_payload(bot, payload)
                    await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)
                except Exception as e:
                    logger.error("Ошибка обработки восстановленного сообщения %s: %s", msg_id, e)
                    await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Ошибка в фоновом таске xautoclaim: %s", e)


async def start_redis_listener(bot: Bot):
    """
    Асинхронный слушатель Redis Streams с гарантированной доставкой (At-Least-Once Delivery).
    """
    logger.info("Запуск фонового слушателя Redis Streams (%s, группа: %s)...", STREAM_NAME, GROUP_NAME)

    while True:
        redis = None
        autoclaim_task = None
        try:
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)

            try:
                await redis.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
                logger.info("Создана Consumer Group '%s' для стрима '%s'", GROUP_NAME, STREAM_NAME)
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    logger.debug("Инициализация Consumer Group в боте: %s", e)

            # Запускаем фоновый таск автовосстановления зависших сообщений
            autoclaim_task = asyncio.create_task(_autoclaim_pending_messages(redis, bot))

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
                                import json
                                payload = json.loads(payload_str)

                            await _handle_message_payload(bot, payload)
                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)
                        except Exception as e:
                            logger.error("Ошибка при обработке сообщения %s в боте: %s", msg_id, e)
                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)

        except asyncio.CancelledError:
            logger.info("Слушатель Redis Streams в боте остановлен.")
            if autoclaim_task:
                autoclaim_task.cancel()
            break
        except Exception as e:
            logger.exception(
                "Сетевая ошибка или сбой подключения к Redis. Повторное подключение через 5 секунд... Ошибка: %s",
                e,
            )
            if autoclaim_task:
                autoclaim_task.cancel()
            await asyncio.sleep(5)
        finally:
            if redis is not None:
                await redis.close()
