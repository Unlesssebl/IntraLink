import logging
import contextvars
import asyncio
from .schemas import PrintJob, JobState, KnowledgeBase
from .router import JobRouter
from strategies import get_strategy
from worker_services.api_client import (
    update_task_status,
    add_task_expenses,
)
from worker_services.action_executor import execute_action
from worker_services.redis_listener import save_job_state, get_redis
import worker_config as config
from executors.wmi_executor import WMIExecutor

logger = logging.getLogger(__name__)

# ContextVar для логирования логов конкретной задачи в Redis
current_task_id = contextvars.ContextVar("current_task_id", default=0)

# Маппинг абстрактных состояний на статусы IntraService под реальный стенд
STATUS_IN_PROGRESS = 27  # В работе
STATUS_WAITING = 35  # Требует уточнения
STATUS_ON_HOLD = 40  # На доработку (передано специалисту)
STATUS_RESOLVED = 29  # Выполнена


class PrinterOrchestrator:
    """
    Оркестратор (State Machine) установки принтеров.
    Управляет переходами между состояниями выполнения задачи и отправляет отчеты в Core API.
    """

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.router = JobRouter(kb)

    async def _update_status(self, job: PrintJob, status_id: int) -> None:
        if not job.is_manual:
            await update_task_status(job.tg_user_id, job.task_id, status_id)

    async def _add_expenses(self, job: PrintJob, minutes: int) -> None:
        if not job.is_manual:
            await add_task_expenses(job.tg_user_id, job.task_id, minutes)

    async def run(self, job: PrintJob) -> None:
        from worker_services.redis_listener import save_job_state

        token = current_task_id.set(job.task_id)
        _failure_handled = False

        async def fail(job_obj: PrintJob, error_msg: str) -> None:
            nonlocal _failure_handled
            _failure_handled = True
            await self.handle_failure(job_obj, error_msg)

        try:
            if job.state != JobState.WAITING_APPROVAL:
                logger.info(
                    "Начало выполнения задачи установки принтера для Task ID: %d",
                    job.task_id,
                )

                # 1. Переход в статус "В работе" в системе IntraService
                await self._update_status(job, STATUS_IN_PROGRESS)
                await save_job_state(job)

                # 2. Маршрутизация (Fast-Track или Smart-Track)
                job.state = JobState.ROUTING
                await save_job_state(job)
                job = await self.router.route(job)
                await save_job_state(job)

                if job.state == JobState.FAILED:
                    await fail(job, job.error_message or "Маршрутизация не удалась")
                    return

                # 2.4 Валидация наличия всех необходимых данных (до запроса подтверждения)
                if not job.connection_type:
                    await fail(job, "Тип подключения принтера не определен")
                    return
                if not job.target_pc:
                    await fail(job, "Целевой ПК не определен")
                    return
                if not job.driver_info:
                    await fail(job, "Драйвер принтера не определен")
                    return

                # 2.4.1 Проверка доступности целевых узлов (параллельно)
                from orchestrator.snmp import is_host_reachable
                import asyncio
                
                logger.info("Проверка доступности целевых узлов по сети (ПК: %s, МФУ: %s)", 
                            job.target_pc, job.printer_address if job.connection_type == "tcpip" else "N/A")
                job.state = JobState.PROBING
                await save_job_state(job)
                
                pc_task = asyncio.create_task(is_host_reachable(job.target_pc))
                printer_task = None
                
                if job.connection_type == "tcpip" and job.printer_address:
                    printer_task = asyncio.create_task(is_host_reachable(job.printer_address))
                
                is_pc_reachable = await pc_task
                is_printer_reachable = await printer_task if printer_task else True
                
                errors = []
                if not is_pc_reachable:
                    logger.warning("Целевой ПК %s недоступен (ping failed)", job.target_pc)
                    errors.append("компьютер")
                    
                if printer_task and not is_printer_reachable:
                    logger.warning("МФУ %s недоступно (ping failed)", job.printer_address)
                    errors.append("принтер")
                    
                if errors:
                    if len(errors) == 2:
                        error_msg = (
                            f"Здравствуйте! Не удалось связаться по сети ни с вашим компьютером ({job.target_pc}), ни с принтером ({job.printer_address}).\n\n"
                            "Пожалуйста, проверьте оба устройства:\n"
                            "1. Включены ли они? Экран компьютера должен светиться (убедитесь, что он не «спит»), а на принтере должны гореть лампочки или экран.\n"
                            "2. Подключены ли провода сети? Проверьте серые, синие или желтые кабели (с прозрачными штекерами на концах). Они должны плотно, до щелчка, сидеть в гнездах компьютера, принтера и розетки в стене.\n"
                            "3. Верны ли данные? Проверьте, правильно ли указано имя вашего ПК и IP-адрес принтера.\n"
                            "4. Попробуйте перезагрузить компьютер, а принтер выключите кнопкой на 10 секунд и включите снова.\n\n"
                            "Если все проверено, но связь не появилась, обратитесь в службу поддержки по телефону 49-87."
                        )
                    elif not is_pc_reachable:
                        error_msg = (
                            f"Здравствуйте! Не удалось связаться с вашим компьютером ({job.target_pc}) по сети. Возможно, он сейчас выключен или не подключен к сети.\n\n"
                            "Пожалуйста, проверьте следующее:\n"
                            "1. Включен ли компьютер? Если экран темный, попробуйте нажать любую клавишу или пошевелить мышкой, чтобы разбудить его.\n"
                            "2. Проверьте сетевой кабель. Это провод (обычно серый, синий или желтый с пластиковым зажимом), который соединяет системный блок или ноутбук с сетевой розеткой. Убедитесь, что он плотно вставлен в разъем компьютера до щелчка.\n"
                            "3. Посмотрите на сетевой разъем сзади компьютера: там должны светиться или мигать маленькие лампочки (зеленая или оранжевая). Если они не горят, значит, компьютер не видит сеть.\n"
                            "4. Попробуйте перезагрузить компьютер.\n\n"
                            "Если все провода на месте, но проблема осталась, пожалуйста, позвоните в службу поддержки по телефону 49-87."
                        )
                    else:
                        error_msg = (
                            f"Здравствуйте! Не удалось связаться с принтером ({job.printer_address}) по сети. Возможно, он выключен или отключен сетевой кабель.\n\n"
                            "Пожалуйста, выполните несколько простых шагов:\n"
                            "1. Включен ли принтер? На панели управления принтера должен светиться экран или индикатор питания. Если принтер находится в режиме энергосбережения («спит»), нажмите любую кнопку на нем.\n"
                            "2. Проверьте сетевой кабель. Провод, идущий от принтера к сетевой розетке в стене, должен быть надежно зафиксирован в гнезде принтера (до характерного щелчка пластикового фиксатора).\n"
                            "3. Проверьте IP-адрес. Убедитесь, что адрес ({job.printer_address}) указан верно.\n"
                            "4. Попробуйте перезагрузить принтер: выключите его кнопкой питания, подождите 10 секунд и включите обратно.\n\n"
                            "Если принтер включен и подключен, но ошибка повторяется, пожалуйста, свяжитесь с нами по телефону 49-87."
                        )
                        
                    await fail(job, error_msg)
                    return

                # 2.5 Ожидание подтверждения (Approval Gate)
                job.state = JobState.WAITING_APPROVAL
                from worker_services.redis_listener import publish_approval_request

                await save_job_state(job)
                await publish_approval_request(job)
                logger.info("Задача %d ожидает подтверждения. Пауза.", job.task_id)
                return
            else:
                logger.info(
                    "Возобновление задачи установки принтера после подтверждения для Task ID: %d",
                    job.task_id,
                )
                job.state = JobState.PROBING
                await save_job_state(job)

            # 3. Загрузка подходящей стратегии установки
            assert job.connection_type is not None
            assert job.target_pc is not None
            assert job.driver_info is not None

            strategy = get_strategy(job.connection_type)

            # Разбор домена и пользователя
            domain = ""
            username = config.WINRM_USERNAME
            if "\\" in username:
                domain, username = username.split("\\", 1)

            wmi_exec = WMIExecutor(
                target_ip=job.target_pc,
                username=username,
                password=config.WINRM_PASSWORD,
                domain=domain,
            )

            # Включение WinRM и блокировка выполнения
            winrm_enabled_successfully = False
            r = get_redis()
            async with r.lock(f"printer_pc_lock:{job.target_pc}", timeout=3600):
                try:
                    job.state = JobState.PROBING
                    await save_job_state(job)
                    try:
                        await wmi_exec.enable_winrm()
                        winrm_enabled_successfully = True
                    except Exception as e:
                        await fail(
                            job,
                            f"Не удалось инициализировать подключение (WMI Bootstrap): {e}",
                        )
                        return

                    # 4. Проверка готовности (WinRM Probe / USB detection)
                    job = await strategy.probe(job)
                    await save_job_state(job)

                    # 5. Обработка случая, когда USB-принтер отключен (WAITING)
                    if job.state == JobState.WAITING:
                        logger.info(
                            "Задача #%d переведена в режим ожидания (USB кабель не подключен)",
                            job.task_id,
                        )
                        await execute_action("on_usb_disconnected", job, job.error_message or "")
                        await save_job_state(job)
                        return

                    if job.state == JobState.FAILED:
                        await fail(job, f"Сбой этапа диагностики: {job.error_message}")
                        return

                    # 6. Выполнение установки
                    assert job.driver_info is not None
                    job = await strategy.execute(job)
                    await save_job_state(job)

                finally:
                    if winrm_enabled_successfully:
                        # Всегда отключаем WinRM после завершения установки (успешной или нет)
                        logger.info("Отключение WinRM на %s...", job.target_pc)
                        try:
                            await wmi_exec.disable_winrm()
                        except Exception as e:
                            logger.warning(
                                "Не удалось отключить WinRM на %s: %s", job.target_pc, e
                            )

            # 7. Финализация результатов
            if job.state == JobState.DONE:
                logger.info(
                    "Установка принтера по задаче #%d завершена успешно!", job.task_id
                )
                # Списание трудозатрат перед переводом в статус "Выполнена"
                await self._add_expenses(job, config.WORKLOG_MINUTES)
                assert job.driver_info is not None
                assert job.connection_type is not None
                await execute_action("on_success", job)
                await save_job_state(job)
            else:
                await fail(
                    job, job.error_message or "Неизвестная ошибка во время установки"
                )

        except Exception as e:
            if not _failure_handled:
                logger.exception(
                    "Критическая ошибка оркестрации задачи #%d: %s", job.task_id, e
                )
                job.state = JobState.FAILED
                job.error_message = f"Внутренняя ошибка оркестратора: {e}"
                await self.handle_failure(job, job.error_message)
            else:
                logger.debug(
                    "Исключение в run() проигнорировано, так как сбой уже обработан: %s",
                    e,
                )
        finally:
            current_task_id.reset(token)

    async def handle_failure(self, job: PrintJob, error_detail: str) -> None:
        logger.error(
            "Сбой выполнения задачи #%d. Состояние: %s. Причина: %s",
            job.task_id,
            job.state.value,
            error_detail,
        )
        job.state = JobState.FAILED
        job.error_message = error_detail
        try:
            from worker_services.redis_listener import save_job_state

            await save_job_state(job)
        except Exception:
            pass
        # Переводим в «Требует уточнения» (35): переход из «В работе» (27) в «На доработку» (40)
        # запрещён бизнес-процессом «Настройка\установка», поэтому используем статус 35.
        await execute_action("on_error", job, error_detail)
