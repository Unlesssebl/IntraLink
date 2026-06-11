import logging
import contextvars
import asyncio
from .schemas import PrintJob, JobState, KnowledgeBase
from .router import JobRouter
from strategies import get_strategy
from worker_services.api_client import (
    add_task_comment,
    update_task_status,
    add_task_expenses,
)
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

# Глобальный словарь блокировок по целевым ПК для исключения конфликтов одновременной установки
_pc_locks: dict[str, asyncio.Lock] = {}


def get_pc_lock(target_pc: str) -> asyncio.Lock:
    if target_pc not in _pc_locks:
        _pc_locks[target_pc] = asyncio.Lock()
    return _pc_locks[target_pc]


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

    async def _add_comment(self, job: PrintJob, comment: str) -> None:
        if not job.is_manual:
            await add_task_comment(job.tg_user_id, job.task_id, comment)

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
                await self._add_comment(
                    job, "🔧 Запущена автоматическая установка принтера..."
                )
                await save_job_state(job)

                # 2. Маршрутизация (Fast-Track или Smart-Track)
                job.state = JobState.ROUTING
                await save_job_state(job)
                job = await self.router.route(job)
                await save_job_state(job)

                if job.state == JobState.FAILED:
                    await fail(job, job.error_message or "Маршрутизация не удалась")
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
            if not job.connection_type:
                await fail(job, "Тип подключения принтера не определен")
                return
            if not job.target_pc:
                await fail(job, "Целевой ПК не определен")
                return
            if not job.driver_info:
                await fail(job, "Драйвер принтера не определен")
                return

            assert job.connection_type is not None
            assert job.target_pc is not None

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

            # Включение WinRM
            winrm_enabled_successfully = False
            async with get_pc_lock(job.target_pc):
                try:
                    job.state = JobState.PROBING
                    await save_job_state(job)
                    await self._add_comment(
                        job,
                        f"🚀 Инициализация удаленного подключения к {job.target_pc} (WMI Bootstrap)...",
                    )
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
                    await self._add_comment(
                        job,
                        f"🔎 Диагностика целевого хоста {job.target_pc}. Проверка подключения принтера по {job.connection_type.value.upper()}...",
                    )
                    job = await strategy.probe(job)
                    await save_job_state(job)

                    # 5. Обработка случая, когда USB-принтер отключен (WAITING)
                    if job.state == JobState.WAITING:
                        logger.info(
                            "Задача #%d переведена в режим ожидания (USB кабель не подключен)",
                            job.task_id,
                        )
                        await self._update_status(job, STATUS_WAITING)
                        await self._add_comment(
                            job,
                            f"⏳ Внимание: {job.error_message}. Пожалуйста, подключите USB кабель принтера и включите устройство, после чего перезапустите задачу вручную (через веб-панель администратора или команду бота).",
                        )
                        await save_job_state(job)
                        return

                    if job.state == JobState.FAILED:
                        await fail(job, f"Сбой этапа диагностики: {job.error_message}")
                        return

                    # 6. Выполнение установки
                    assert job.driver_info is not None
                    await self._add_comment(
                        job,
                        f"📥 Установка драйвера {job.driver_info.display_name} и настройка портов на ПК {job.target_pc}...",
                    )
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
                await self._update_status(job, STATUS_RESOLVED)
                assert job.driver_info is not None
                assert job.connection_type is not None
                await self._add_comment(
                    job,
                    f"✅ Успех: Принтер '{job.driver_info.display_name}' успешно установлен и настроен на компьютере {job.target_pc}.\n"
                    f"Тип подключения: {job.connection_type.value.upper()}\n"
                    f"Используемый драйвер: {job.driver_info.driver_name}",
                )
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
        await self._update_status(job, STATUS_WAITING)
        await self._add_comment(
            job,
            f"❌ Ошибка автоустановки на этапе '{job.state.value}': {error_detail}.\n"
            f"Задача передана на ручной разбор специалисту технической поддержки.",
        )
