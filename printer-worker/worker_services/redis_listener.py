import logging
import json
import asyncio
import re
import time
import redis.asyncio as aioredis
from typing import Optional
from orchestrator.device_normalizer import normalize_pc_name, normalize_printer_address
from worker_config import REDIS_URL, MAX_CONCURRENT_JOBS
from orchestrator.schemas import PrintJob, JobState
from worker_services.api_client import (
    get_task_details,
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


async def _process_event(payload: dict, channel: str = "ai_validated_events") -> None:
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

        if task_id is None:
            logger.error("Отсутствует task_id в событии: %s", payload)
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
            "Обработка события '%s' (канал: %s) для задачи #%d (пользователь %s)",
            event_type,
            channel,
            task_id,
            str(tg_user_id) if tg_user_id is not None else "Service Account",
        )

        # ЖЁСТКОЕ ПРАВИЛО МАРШРУТИЗАЦИИ:
        # Сырые события new_task игнорируем, ждем их из ai_validated_events
        if event_type == "new_task" and channel != "ai_validated_events":
            logger.debug(
                "Событие new_task для задачи #%d проигнорировано в канале %s (ожидается из ai_validated_events)",
                task_id,
                channel
            )
            _active_tasks.discard(task_id)
            return

        try:
            task_data = payload.get("task_data")

            # Предварительная проверка наличия кастомных полей в payload
            has_custom_fields = task_data and (
                task_data.get("Field1112")
                or task_data.get("Field1103")
                or task_data.get("Data")
                or task_data.get("CreatorComments")
            )

            # Если нужных полей нет (IntraService не вернул их в списке), делаем fallback запрос
            if not task_data or not has_custom_fields:
                logger.info(
                    "Кастомные поля отсутствуют в событии. Выполняю fallback-запрос get_task_details для задачи #%d",
                    task_id,
                )
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

            # Текст заявки собираем из Name + Description + CreatorComments
            raw_text = f"{task_data.get('Name', '')} {task_data.get('Description', '')}"
            if task_data.get("CreatorComments"):
                raw_text += f" {task_data['CreatorComments']}"

            # --- Извлечение кастомных полей ---
            # IS возвращает кастомные поля в виде плоских полей FieldXXXX
            # ID 1103 = Оборудование (модель принтера)
            # ID 1112 = Номер компьютера
            target_pc = task_data.get("Field1112") or None
            model_key = task_data.get("Field1103") or None

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

            # Нормализация target_pc: транслитерация + исправление опечаток в префиксе
            target_pc = normalize_pc_name(target_pc)

            # Очистка model_key
            if model_key:
                # Замена русских букв-омоглифов на английские
                cyrillic = 'ОСАЕРХМТКВ'
                latin    = 'OCAEPXMTKB'
                tr_map = str.maketrans(cyrillic + cyrillic.lower(), latin + latin.lower())
                model_key = model_key.translate(tr_map)
                
                # Удаление IP-адресов из названия модели
                model_key = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '', model_key)
                model_key = model_key.strip()
                if not model_key:
                    model_key = None

            logger.info(
                "Параметры задачи #%d из IS: target_pc=%s, model_key=%s",
                task_id,
                target_pc,
                model_key,
            )

            # Создаем контекст выполнения PrintJob (raw_text передается чистым для LLM)
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


async def _listen_printer_actions(redis: aioredis.Redis):
    """Слушает интерактивные команды оператора/бота из канала printer_actions."""
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

                    event_type = payload.get("event_type")
                    if event_type == "approval_response":
                        asyncio.create_task(_process_approval_response(payload))
                    elif event_type == "manual_trigger":
                        asyncio.create_task(_process_manual_trigger(payload))
                    elif event_type == "rebuild_index":
                        from worker_services.indexer_service import auto_extract_and_index

                        already_running = await redis.get("indexer:status")
                        if already_running == "running":
                            continue

                        async def run_indexing():
                            started_at = time.time()
                            try:
                                await redis.setex("indexer:status", 3600, "running")
                                stats = await auto_extract_and_index()
                                duration = time.time() - started_at
                                result = {
                                    "status": "ok",
                                    "indexed": stats.get("indexed", 0) if stats else 0,
                                    "copied": stats.get("copied", 0) if stats else 0,
                                    "extracted": stats.get("extracted", 0) if stats else 0,
                                    "skipped": stats.get("skipped", 0) if stats else 0,
                                    "duration_sec": round(duration),
                                }
                                await redis.set("indexer:last_result", json.dumps(result))
                            except Exception as exc:
                                duration = time.time() - started_at
                                result = {
                                    "status": "error",
                                    "error": str(exc),
                                    "duration_sec": round(duration),
                                }
                                await redis.set("indexer:last_result", json.dumps(result))
                            finally:
                                await redis.delete("indexer:status")
                                await redis.set("indexer:last_run", time.time())

                        asyncio.create_task(run_indexing())
                    elif event_type == "fast_reindex":
                        from worker_services.indexer_service import rebuild_index_only

                        already_running = await redis.get("indexer:status")
                        if already_running == "running":
                            continue

                        async def run_fast_reindex():
                            started_at = time.time()
                            try:
                                await redis.setex("indexer:status", 600, "running")
                                stats = await rebuild_index_only()
                                duration = time.time() - started_at
                                result = {
                                    "status": "ok",
                                    "mode": "fast",
                                    "indexed": stats.get("indexed", 0) if stats else 0,
                                    "copied": 0,
                                    "extracted": 0,
                                    "skipped": 0,
                                    "duration_sec": round(duration),
                                }
                                await redis.set("indexer:last_result", json.dumps(result))
                            except Exception as exc:
                                duration = time.time() - started_at
                                result = {
                                    "status": "error",
                                    "mode": "fast",
                                    "error": str(exc),
                                    "duration_sec": round(duration),
                                }
                                await redis.set("indexer:last_result", json.dumps(result))
                            finally:
                                await redis.delete("indexer:status")
                                await redis.set("indexer:last_run", time.time())

                        asyncio.create_task(run_fast_reindex())
                except Exception as e:
                    logger.error("Ошибка обработки printer_actions: %s", e)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Сбой подписчика printer_actions: %s", e)


async def start_redis_listener():
    """
    Фоновый процесс подписки на Redis Streams (stream:ai_validated_events) и Pub/Sub (printer_actions).
    """
    logger.info("Запуск фонового подписчика Redis Streams...")
    try:
        await _recover_orphan_jobs()
    except Exception as e:
        logger.exception("Критическая ошибка при восстановлении сиротских задач: %s", e)

    STREAM_NAME = "stream:ai_validated_events"
    GROUP_NAME = "printer_worker_group"
    CONSUMER_NAME = "printer_worker_1"

    while True:
        redis = None
        action_task = None
        try:
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)

            # Создаем группу потребителей
            try:
                await redis.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
                logger.info("Создана Consumer Group '%s' для стрима '%s'", GROUP_NAME, STREAM_NAME)
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    logger.debug("Инициализация Consumer Group: %s", e)

            # Запускаем параллельный слушатель управляющих действий
            action_task = asyncio.create_task(_listen_printer_actions(redis))

            while True:
                try:
                    entries = await redis.xreadgroup(
                        groupname=GROUP_NAME,
                        consumername=CONSUMER_NAME,
                        streams={STREAM_NAME: ">"},
                        count=5,
                        block=2000,
                    )
                except Exception as read_err:
                    logger.warning("Ошибка чтения из стрима %s: %s", STREAM_NAME, read_err)
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

                            event_type = payload.get("event_type")
                            if event_type == "new_task":
                                asyncio.create_task(_process_event(payload, STREAM_NAME))

                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)
                        except Exception as e:
                            logger.error("Ошибка при обработке сообщения %s: %s", msg_id, e)
                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)

        except asyncio.CancelledError:
            logger.info("Слушатель Redis Streams остановлен.")
            if action_task:
                action_task.cancel()
            break
        except Exception as e:
            logger.exception(
                "Сбой соединения с Redis. Повторное подключение через 5 секунд... Ошибка: %s",
                e,
            )
            if action_task:
                action_task.cancel()
            await asyncio.sleep(5)
        finally:
            if redis is not None:
                await redis.close()
