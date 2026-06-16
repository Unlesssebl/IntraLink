import logging
import json
import asyncio
import re
import time
import redis.asyncio as aioredis
from typing import Optional
from worker_config import REDIS_URL, MAX_CONCURRENT_JOBS
from orchestrator.schemas import PrintJob, JobState
from worker_services.api_client import (
    get_task_details,
    update_task_status,
)

logger = logging.getLogger(__name__)

# Глобальный клиент Redis для публикации и сохранения стейта
_redis_client = None


def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


async def save_job_state(job: PrintJob) -> None:
    try:
        r = get_redis()
        await r.set(f"printer_job:{job.task_id}", job.model_dump_json(), ex=2592000)
        await r.zadd("printer_jobs_list", {str(job.task_id): time.time()})
        await r.zremrangebyrank("printer_jobs_list", 0, -101)
    except Exception as e:
        logger.error(
            "Ошибка при сохранении состояния задачи #%d в Redis: %s", job.task_id, e
        )


async def load_job_state(task_id: int) -> Optional[PrintJob]:
    r = get_redis()
    data = await r.get(f"printer_job:{task_id}")
    if data:
        return PrintJob.model_validate_json(data)
    return None


async def publish_approval_request(job: PrintJob) -> None:
    r = get_redis()
    payload = {
        "event_type": "printer_approval_request",
        "task_id": job.task_id,
        "tg_user_id": job.tg_user_id,
        "target_pc": job.target_pc,
        "model_key": job.model_key,
        "connection_type": job.connection_type.value if job.connection_type else None,
        "driver_name": job.driver_info.display_name if job.driver_info else None,
    }
    await r.publish("printer_actions", json.dumps(payload))


# Семафор для ограничения одновременных сессий WinRM/SMB
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

# Множество task_id, которые сейчас обрабатываются (дедупликация)
_active_tasks: set[int] = set()


async def _process_approval_response(payload: dict) -> None:
    task_id = payload.get("task_id")
    if not task_id:
        return

    if task_id in _active_tasks:
        logger.info(
            "Событие 'approval_response' для задачи #%d пропущено: задача уже обрабатывается",
            task_id,
        )
        return

    _active_tasks.add(task_id)
    try:
        async with _semaphore:
            action = payload.get("action")  # "approve", "reject", "update"

            job = await load_job_state(task_id)
            if not job:
                logger.error(
                    "Job %s не найден в Redis при получении approval_response", task_id
                )
                tg_user_id = payload.get("tg_user_id")
                if tg_user_id:
                    from worker_services.action_executor import execute_action

                    dummy_job = PrintJob(
                        task_id=task_id,
                        tg_user_id=tg_user_id,
                        raw_text="",
                        state=JobState.FAILED,
                    )
                    await execute_action("on_state_lost", dummy_job)
                return

            from worker_main import get_orchestrator

            orchestrator = get_orchestrator()

            if action == "approve":
                await orchestrator.run(job)
            elif action == "update":
                job.target_pc = payload.get("target_pc", job.target_pc)
                job.model_key = payload.get("model_key", job.model_key)
                if payload.get("connection_type"):
                    from orchestrator.schemas import ConnectionType

                    job.connection_type = ConnectionType(payload.get("connection_type"))

                if job.model_key:
                    job.driver_info = orchestrator.kb.find_by_key(job.model_key)

                await save_job_state(job)
                await orchestrator.run(job)
            elif action == "reject":
                job.state = JobState.FAILED
                job.error_message = "Отменено инженером технической поддержки."
                await orchestrator.handle_failure(job, job.error_message)
    except Exception as e:
        logger.exception(
            "Ошибка при обработке подтверждения для задачи #%d: %s", task_id, e
        )
    finally:
        _active_tasks.discard(task_id)
        logger.debug("Задача #%d (approval_response) удалена из активных", task_id)


async def _process_manual_trigger(payload: dict) -> None:
    task_id = payload.get("task_id")
    if not task_id:
        logger.error(
            "Неполные данные для ручного запуска (отсутствует task_id): %s", payload
        )
        return

    if task_id in _active_tasks:
        logger.info(
            "Событие 'manual_trigger' для задачи #%d пропущено: задача уже обрабатывается",
            task_id,
        )
        return

    _active_tasks.add(task_id)
    try:
        async with _semaphore:
            tg_user_id = payload.get("tg_user_id") or 0
            target_pc = payload.get("target_pc")
            model_key = payload.get("model_key")
            connection_type = payload.get("connection_type")
            printer_address = payload.get("printer_address")

            if not target_pc or not model_key:
                logger.error(
                    "Неполные данные для ручного запуска в сообщении: %s", payload
                )
                return

            from orchestrator.schemas import ConnectionType
            from worker_main import get_orchestrator

            orchestrator = get_orchestrator()

            job = PrintJob(
                task_id=task_id,
                tg_user_id=tg_user_id,
                raw_text=f"Ручная установка: {model_key} на ПК {target_pc}",
                state=JobState.PROBING,
                target_pc=target_pc,
                model_key=model_key,
                connection_type=ConnectionType(connection_type)
                if connection_type
                else None,
                printer_address=printer_address,
                is_manual=True,
            )

            if job.model_key:
                job.driver_info = orchestrator.kb.find_by_key(job.model_key)

            await save_job_state(job)
            logger.info("Запущен ручной процесс установки для задачи #%d", task_id)
            await orchestrator.run(job)
    except Exception as e:
        logger.exception("Ошибка при ручном запуске для задачи #%d: %s", task_id, e)
    finally:
        _active_tasks.discard(task_id)
        logger.debug("Задача #%d (manual_trigger) удалена из активных", task_id)


async def _process_event(payload: dict) -> None:
    async with _semaphore:
        tg_user_id_raw = payload.get("tg_user_id")
        task_id_raw = payload.get("task_id")
        event_type = payload.get("event_type")

        try:
            tg_user_id = int(tg_user_id_raw) if tg_user_id_raw is not None else None
            task_id = int(task_id_raw) if task_id_raw is not None else None
        except (ValueError, TypeError):
            logger.error(
                "Не удалось привести tg_user_id (%s) или task_id (%s) к int",
                tg_user_id_raw,
                task_id_raw,
            )
            return

        if tg_user_id is None or task_id is None:
            logger.error("Отсутствует tg_user_id или task_id в событии: %s", payload)
            return

        # Ранняя фильтрация: отбрасываем чужие заявки до HTTP-запроса
        from worker_config import PRINTER_EXECUTOR_IS_USER_ID, PRINTER_EXECUTOR_LOGIN

        if PRINTER_EXECUTOR_IS_USER_ID is not None:
            event_is_user_id = payload.get("is_user_id")
            if (
                event_is_user_id
                and int(event_is_user_id) != PRINTER_EXECUTOR_IS_USER_ID
            ):
                logger.debug(
                    "Ранняя фильтрация: событие для задачи #%d пропущено (is_user_id=%s, ожидается=%d)",
                    task_id,
                    event_is_user_id,
                    PRINTER_EXECUTOR_IS_USER_ID,
                )
                return

        if PRINTER_EXECUTOR_LOGIN:
            event_login = payload.get("is_login")
            if event_login and event_login.lower() != PRINTER_EXECUTOR_LOGIN.lower():
                logger.debug(
                    "Ранняя фильтрация: событие для задачи #%d пропущено (is_login='%s', ожидается='%s')",
                    task_id,
                    event_login,
                    PRINTER_EXECUTOR_LOGIN,
                )
                return

        # Дедупликация: пропускаем задачу если она уже обрабатывается
        if task_id in _active_tasks:
            logger.info(
                "Событие '%s' для задачи #%d пропущено: задача уже обрабатывается",
                event_type,
                task_id,
            )
            return

        _active_tasks.add(task_id)
        logger.info(
            "Обработка события '%s' для задачи #%d (пользователь %d)",
            event_type,
            task_id,
            tg_user_id,
        )

        try:
            # Получаем подробности задачи из Core API
            raw_response = await get_task_details(tg_user_id, task_id)
            if not raw_response:
                logger.error(
                    "Не удалось загрузить подробности задачи #%d из Core API. Пропуск.",
                    task_id,
                )
                return

            # IntraService возвращает обёртку {Task: {...}, Statuses: [...]}
            # если это словарь с ключом Task — разворачиваем
            if isinstance(raw_response, dict) and "Task" in raw_response:
                task_data = raw_response["Task"]
            else:
                task_data = raw_response

            if not task_data:
                logger.error("Пустой ответ по задаче #%d", task_id)
                return

            # Текст заявки собираем из Name + Description
            raw_text = f"{task_data.get('Name', '')} {task_data.get('Description', '')}"

            # --- Извлечение кастомных полей ---
            # IS возвращает кастомные поля в виде плоских полей FieldXXXX
            # ID 1103 = Оборудование (модель принтера)
            # ID 1112 = Номер компьютера
            target_pc = task_data.get("Field1112") or None
            model_key = task_data.get("Field1103") or None

            # Fallback: CreatorComments иногда содержит имя ПК
            if not target_pc and task_data.get("CreatorComments"):
                target_pc = task_data.get("CreatorComments")

            # Fallback: XML-поле Data (если плоские поля пусты)
            if (not target_pc or not model_key) and task_data.get("Data"):
                data_xml = task_data["Data"]
                if not target_pc:
                    m = re.search(r'<field id="1112">([^<]+)</field>', data_xml)
                    if m:
                        target_pc = m.group(1).strip() or None
                if not model_key:
                    m = re.search(r'<field id="1103">([^<]+)</field>', data_xml)
                    if m:
                        model_key = m.group(1).strip() or None

            # Добавляем все значения полей в raw_text
            if target_pc:
                raw_text += f" {target_pc}"
            if model_key:
                raw_text += f" {model_key}"

            logger.info(
                "Параметры задачи #%d: target_pc=%s, model_key=%s, raw_text='%s'",
                task_id,
                target_pc,
                model_key,
                raw_text,
            )

            # Создаем контекст выполнения PrintJob
            job = PrintJob(
                task_id=task_id,
                tg_user_id=tg_user_id,
                raw_text=raw_text,
                state=JobState.PENDING,
                target_pc=target_pc,
                model_key=model_key,
            )

            # Создаем оркестратор и запускаем стейт-машину
            from worker_main import get_orchestrator

            orchestrator = get_orchestrator()
            await orchestrator.run(job)

        except Exception as e:
            logger.exception("Ошибка при обработке события задачи #%d: %s", task_id, e)
        finally:
            # Всегда снимаем блокировку, даже при ошибке
            _active_tasks.discard(task_id)
            logger.debug("Задача #%d удалена из активных", task_id)


async def _recover_orphan_jobs() -> None:
    logger.info("Запуск сканирования для восстановления сиротских задач в Redis...")
    r = get_redis()

    # Локальные импорты для избежания циклических ссылок
    from executors.wmi_executor import WMIExecutor
    from worker_services.credentials import get_domain_credentials
    from orchestrator.orchestrator import STATUS_WAITING

    try:
        async for key in r.scan_iter("printer_job:*"):
            try:
                data = await r.get(key)
                if not data:
                    continue

                job = PrintJob.model_validate_json(data)

                # Зависшие задачи, требующие отключения WinRM на удаленном PC и перевода в FAILED
                if job.state in (
                    JobState.PROBING,
                    JobState.ROUTING,
                    JobState.PARSING,
                    JobState.COPYING,
                    JobState.INSTALLING,
                    JobState.VERIFYING,
                ):
                    logger.warning(
                        "Обнаружена зависшая задача #%d в состоянии %s. Восстановление...",
                        job.task_id,
                        job.state.value,
                    )

                    if job.target_pc:
                        domain, username, password = await get_domain_credentials()

                        wmi = WMIExecutor(
                            target_ip=job.target_pc,
                            username=username,
                            password=password,
                            domain=domain,
                        )
                        try:
                            logger.info(
                                "Отключение WinRM на ПК %s для сиротской задачи #%d...",
                                job.target_pc,
                                job.task_id,
                            )
                            await wmi.disable_winrm()
                        except Exception as ex:
                            logger.error(
                                "Не удалось отключить WinRM для сиротской задачи #%d: %s",
                                job.task_id,
                                ex,
                            )

                    job.state = JobState.FAILED
                    job.error_message = "Установка прервана из-за перезапуска сервиса. Задача сброшена в очередь ожидания."
                    await save_job_state(job)

                    if not job.is_manual:
                        try:
                            from worker_services.action_executor import execute_action
                            await execute_action("on_orphan_recovered", job)
                        except Exception as ex:
                            logger.error(
                                "Не удалось отправить статус в ИС для сиротской задачи #%d: %s",
                                job.task_id,
                                ex,
                            )

                elif job.state == JobState.WAITING_APPROVAL:
                    logger.warning(
                        "Обнаружена зависшая задача #%d в состоянии WAITING_APPROVAL. Восстановление...",
                        job.task_id,
                    )
                    job.state = JobState.FAILED
                    job.error_message = "Подтверждение из Telegram-бота не было получено из-за перезапуска сервиса. Задача требует ручного перезапуска."
                    await save_job_state(job)

                    if not job.is_manual:
                        try:
                            from worker_services.action_executor import execute_action
                            await execute_action("on_orphan_recovered", job)
                        except Exception as ex:
                            logger.error(
                                "Не удалось отправить статус в ИС для сиротской задачи #%d: %s",
                                job.task_id,
                                ex,
                            )
            except Exception as e:
                logger.error("Ошибка разбора/восстановления для ключа %s: %s", key, e)
    except Exception as e:
        logger.error("Ошибка при сканировании ключей printer_job:* в Redis: %s", e)
    logger.info("Сканирование и восстановление сиротских задач завершено.")


async def start_redis_listener():
    """
    Фоновый процесс подписки на Redis Pub/Sub для прослушивания событий IntraService.
    """
    logger.info(
        "Запуск фонового подписчика Redis Pub/Sub на канале 'intraservice_events'..."
    )
    try:
        await _recover_orphan_jobs()
    except Exception as e:
        logger.exception("Критическая ошибка при восстановлении сиротских задач: %s", e)
    while True:
        redis = None
        try:
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe("intraservice_events", "printer_actions")
                logger.info(
                    "Подписка на Redis Pub/Sub каналы 'intraservice_events' и 'printer_actions' успешно оформлена."
                )

                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message is None or message.get("type") != "message":
                        continue

                    payload_str = message.get("data")
                    if not isinstance(payload_str, (str, bytes)):
                        logger.error(
                            "Неверный формат данных сообщения из Redis: %s",
                            type(payload_str),
                        )
                        continue
                    try:
                        payload = json.loads(payload_str)
                    except Exception as e:
                        logger.error(
                            "Ошибка парсинга JSON события из Redis: %s. Данные: %s",
                            e,
                            payload_str,
                        )
                        continue

                    event_type = payload.get("event_type")

                    # Обработка ответов от бота
                    if event_type == "approval_response":
                        asyncio.create_task(_process_approval_response(payload))
                        continue

                    # Обработка ручного запуска из веб-интерфейса
                    if event_type == "manual_trigger":
                        asyncio.create_task(_process_manual_trigger(payload))
                        continue

                    # Фильтруем события IntraService
                    if event_type not in ("new_task", "status_change", "executor_assigned"):
                        continue

                    # Если это изменение статуса или назначение исполнителя, реагируем только на статус "Открыта" (ID: 31)
                    if event_type in ("status_change", "executor_assigned"):
                        status_id = payload.get("status_id")
                        if status_id != 31:
                            logger.debug(
                                "Событие %s для задачи #%d пропущено: статус %s не является стартовым",
                                event_type,
                                payload.get("task_id"),
                                status_id,
                            )
                            continue

                    # Запускаем обработку события асинхронно
                    asyncio.create_task(_process_event(payload))

        except asyncio.CancelledError:
            logger.info("Слушатель Redis Pub/Sub остановлен.")
            break
        except Exception as e:
            logger.exception(
                "Сбой соединения с Redis. Повторное подключение через 5 секунд... Ошибка: %s",
                e,
            )
            await asyncio.sleep(5)
        finally:
            if redis is not None:
                await redis.close()
