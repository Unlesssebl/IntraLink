import logging
from .schemas import PrintJob, JobState, KnowledgeBase
from .router import JobRouter
from strategies import get_strategy
from services.api_client import add_task_comment, update_task_status
import config
from executors.wmi_executor import WMIExecutor, WmiBootstrapError
logger = logging.getLogger(__name__)

# Маппинг абстрактных состояний на статусы IntraService (заглушка ID статусов)
# В реальной интеграции эти ID подгружаются из /statuses
STATUS_IN_PROGRESS = 2  # В работе
STATUS_WAITING = 3      # В ожидании / Ожидание пользователя
STATUS_ON_HOLD = 4      # На удержании / Требуется специалист
STATUS_RESOLVED = 5     # Решена / Закрыта

class PrinterOrchestrator:
    """
    Оркестратор (State Machine) установки принтеров.
    Управляет переходами между состояниями выполнения задачи и отправляет отчеты в Core API.
    """
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.router = JobRouter(kb)

    async def run(self, job: PrintJob) -> None:
        logger.info("Начало выполнения задачи установки принтера для Task ID: %d", job.task_id)
        
        try:
            # 1. Переход в статус "В работе" в системе IntraService
            await update_task_status(job.tg_user_id, job.task_id, STATUS_IN_PROGRESS)
            await add_task_comment(job.tg_user_id, job.task_id, "🔧 Запущена автоматическая установка принтера...")

            # 2. Маршрутизация (Fast-Track или Smart-Track)
            job.state = JobState.ROUTING
            job = await self.router.route(job)

            if job.state == JobState.FAILED:
                await self._handle_failure(job, "Маршрутизация не удалась")
                return

            # 3. Загрузка подходящей стратегии установки
            if not job.connection_type:
                await self._handle_failure(job, "Тип подключения принтера не определен")
                return
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
                domain=domain
            )

            # Включение WinRM
            job.state = JobState.PROBING
            await add_task_comment(
                job.tg_user_id, 
                job.task_id, 
                f"🚀 Инициализация удаленного подключения к {job.target_pc} (WMI Bootstrap)..."
            )
            try:
                await wmi_exec.enable_winrm()
            except Exception as e:
                await self._handle_failure(job, f"Не удалось инициализировать подключение (WMI Bootstrap): {e}")
                return

            try:
                # 4. Проверка готовности (WinRM Probe / USB detection)
                await add_task_comment(
                    job.tg_user_id, 
                    job.task_id, 
                    f"🔎 Диагностика целевого хоста {job.target_pc}. Проверка подключения принтера по {job.connection_type.value.upper()}..."
                )
                job = await strategy.probe(job)

                # 5. Обработка случая, когда USB-принтер отключен (WAITING)
                if job.state == JobState.WAITING:
                    logger.info("Задача #%d переведена в режим ожидания (USB кабель не подключен)", job.task_id)
                    await update_task_status(job.tg_user_id, job.task_id, STATUS_WAITING)
                    await add_task_comment(
                        job.tg_user_id,
                        job.task_id,
                        f"⏳ Внимание: {job.error_message}. Пожалуйста, подключите USB кабель принтера и включите устройство, после чего установка продолжится автоматически."
                    )
                    return

                if job.state == JobState.FAILED:
                    await self._handle_failure(job, f"Сбой этапа диагностики: {job.error_message}")
                    return

                # 6. Выполнение установки
                await add_task_comment(
                    job.tg_user_id,
                    job.task_id,
                    f"📥 Установка драйвера {job.driver_info.display_name} и настройка портов на ПК {job.target_pc}..."
                )
                job = await strategy.execute(job)

            finally:
                # Всегда отключаем WinRM после завершения установки (успешной или нет)
                logger.info("Отключение WinRM на %s...", job.target_pc)
                try:
                    await wmi_exec.disable_winrm()
                except Exception as e:
                    logger.warning("Не удалось отключить WinRM на %s: %s", job.target_pc, e)

            # 7. Финализация результатов
            if job.state == JobState.DONE:
                logger.info("Установка принтера по задаче #%d завершена успешно!", job.task_id)
                await update_task_status(job.tg_user_id, job.task_id, STATUS_RESOLVED)
                await add_task_comment(
                    job.tg_user_id,
                    job.task_id,
                    f"✅ Успех: Принтер '{job.driver_info.display_name}' успешно установлен и настроен на компьютере {job.target_pc}.\n"
                    f"Тип подключения: {job.connection_type.value.upper()}\n"
                    f"Используемый драйвер: {job.driver_info.driver_name}"
                )
            else:
                await self._handle_failure(job, job.error_message or "Неизвестная ошибка во время установки")

        except Exception as e:
            logger.exception("Критическая ошибка оркестрации задачи #%d: %s", job.task_id, e)
            job.state = JobState.FAILED
            job.error_message = f"Внутренняя ошибка оркестратора: {e}"
            await self._handle_failure(job, job.error_message)

    async def _handle_failure(self, job: PrintJob, error_detail: str) -> None:
        logger.error("Сбой выполнения задачи #%d. Состояние: %s. Причина: %s", job.task_id, job.state.value, error_detail)
        await update_task_status(job.tg_user_id, job.task_id, STATUS_ON_HOLD)
        await add_task_comment(
            job.tg_user_id,
            job.task_id,
            f"❌ Ошибка автоустановки на этапе '{job.state.value}': {error_detail}.\n"
            f"Задача передана на ручной разбор специалисту технической поддержки."
        )
