import logging
import contextvars
from .schemas import PrintJob, JobState, KnowledgeBase, ErrorType
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
from executors.smb_executor import smb_executor
from executors.winrm_executor import winrm_executor
from executors.smb_bootstrap import SMBBootstrapExecutor
from worker_services.credentials import get_domain_credentials, format_smb_username

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

                # 2.4 Валидация и проверка доступности целевых узлов (параллельно)
                from orchestrator.snmp import is_host_reachable
                import asyncio

                logger.info(
                    "Валидация данных и проверка доступности узлов (ПК: %s, МФУ: %s)",
                    job.target_pc or "не указан",
                    job.printer_address if getattr(job, 'connection_type', None) == "tcpip" else "N/A",
                )
                
                # Сохраняем состояние PROBING, даже если были ошибки в роутере
                if job.state != JobState.FAILED:
                    job.state = JobState.PROBING
                    await save_job_state(job)

                pc_task = None
                printer_task = None

                if job.target_pc:
                    pc_task = asyncio.create_task(is_host_reachable(job.target_pc))
                
                if getattr(job, 'connection_type', None) == "tcpip" and job.printer_address:
                    printer_task = asyncio.create_task(is_host_reachable(job.printer_address))

                is_pc_reachable = await pc_task if pc_task else False
                is_printer_reachable = await printer_task if printer_task else False

                pc_ok = bool(job.target_pc) and is_pc_reachable
                printer_ok = True
                if getattr(job, 'connection_type', None) == "tcpip":
                    printer_ok = bool(job.printer_address) and is_printer_reachable

                # Собираем все ошибки в один список
                errors = []
                
                # 1. Сбор ошибок связи с ПК и МФУ (главное требование)
                if not pc_ok and not printer_ok and getattr(job, 'connection_type', None) == "tcpip":
                    # Оба узла недоступны или не указаны
                    errors.append(f"ping failed: both (PC: {job.target_pc or 'не указан'}, Printer: {job.printer_address or 'не указан'})")
                elif not pc_ok:
                    if not job.target_pc:
                        errors.append("Целевой компьютер не указан")
                    else:
                        errors.append(f"ping failed: pc (PC: {job.target_pc})")
                elif not printer_ok and getattr(job, 'connection_type', None) == "tcpip":
                    if not job.printer_address:
                        errors.append("не удалось определить IP-адрес")
                    else:
                        errors.append(f"ping failed: printer (Printer: {job.printer_address})")

                # 2. Сбор ошибок данных
                if not getattr(job, 'connection_type', None):
                    errors.append("Тип подключения принтера не определен")
                if not getattr(job, 'driver_info', None):
                    errors.append("Драйвер принтера не определен")
                    
                # 3. Учет ошибок от роутера (например, "Не удалось подобрать драйвер...")
                if job.state == JobState.FAILED and job.error_message and job.error_message not in errors:
                    errors.append(job.error_message)

                if errors:
                    # Если есть ошибка 'ping failed: both', она перекроет всё в action_config
                    # Иначе отправляем первую/основную ошибку (или склеиваем)
                    error_msg = "\n".join(errors) if len(errors) > 1 else errors[0]
                    # Но чтобы шаблоны в action_config сработали, лучше передать строку, содержащую ключевые слова
                    job.error_type = ErrorType.USER
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
            domain, username, password = await get_domain_credentials()

            # Инициализация SMBExecutor динамическими учетными данными
            smb_executor.username = username
            smb_executor.password = password
            smb_executor.domain = domain

            # Инициализация WinRM динамическими учетными данными
            full_username = format_smb_username(job.target_pc, domain, username)
            winrm_executor.username = full_username
            winrm_executor.password = password

            wmi_exec = WMIExecutor(
                target_ip=job.target_pc,
                username=username,
                password=password,
                domain=domain,
            )
            smb_bootstrap_exec = SMBBootstrapExecutor(
                target_ip=job.target_pc,
                username=username,
                password=password,
                domain=domain,
            )

            # Включение WinRM и блокировка выполнения
            active_bootstrap_exec = None
            r = get_redis()
            async with r.lock(f"printer_pc_lock:{job.target_pc}", timeout=3600):
                try:
                    job.state = JobState.PROBING
                    await save_job_state(job)
                    try:
                        await wmi_exec.enable_winrm()
                        active_bootstrap_exec = wmi_exec
                    except Exception as e:
                        logger.warning(
                            "WMI Bootstrap не удался для %s: %s. Переход на SMB Fallback...",
                            job.target_pc, e
                        )
                        try:
                            await smb_bootstrap_exec.enable_winrm()
                            active_bootstrap_exec = smb_bootstrap_exec
                        except Exception as fallback_e:
                            await fail(
                                job,
                                f"Не удалось инициализировать подключение ни по WMI, ни по SMB (Fallback): {fallback_e}",
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
                        await execute_action(
                            "on_usb_disconnected", job, job.error_message or ""
                        )
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
                    if active_bootstrap_exec:
                        # Всегда отключаем WinRM после завершения установки (успешной или нет)
                        logger.info("Отключение WinRM на %s...", job.target_pc)
                        try:
                            await active_bootstrap_exec.disable_winrm()
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
