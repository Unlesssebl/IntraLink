import re
import logging
from typing import Optional
from .schemas import PrintJob, JobState, KnowledgeBase
from llm import get_provider

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.65

class JobRouter:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def _parse_ip(self, text: str) -> Optional[str]:
        # Простая регулярка для извлечения IPv4 адреса из текста
        match = re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', text)
        return match.group(0) if match else None

    async def route(self, job: PrintJob) -> PrintJob:
        logger.info("Маршрутизация задачи #%d", job.task_id)

        # 1. Попытка Fast-Track: проверка, есть ли уже предзаполненные поля
        if job.target_pc and job.model_key:
            logger.info("Fast-Track: обнаружены предзаполненные поля (ПК: %s, Модель: %s)", job.target_pc, job.model_key)
            # Сначала точный поиск по model_key
            driver = self.kb.find_by_key(job.model_key)
            # Если не нашли — нечёткий поиск по display_name (кастомное поле содержит название)
            if not driver:
                driver = self.kb.find_by_name(job.model_key)
                if driver:
                    logger.info("Fast-Track: нечёткий поиск нашёл модель '%s' для строки '%s'", driver.model_key, job.model_key)
            if driver:
                job.model_key = driver.model_key  # нормализуем к model_key из БЗ
                job.driver_info = driver
                job.connection_type = driver.connection_type
                # Если сетевой принтер, пробуем найти IP/DNS в тексте
                if driver.connection_type == "tcpip" and not job.printer_address:
                    job.printer_address = self._parse_ip(job.raw_text)
                
                logger.info("Fast-Track успешно пройден для задачи #%d", job.task_id)
                return job
            else:
                logger.warning("Модель %s не найдена в Базе Знаний ни по ключу, ни по имени. Фолбэк на Smart-Track.", job.model_key)

        # 2. Smart-Track: использование LLM-as-a-Function
        logger.info("Smart-Track: Запуск LLM-парсинга для задачи #%d", job.task_id)
        job.state = JobState.PARSING
        
        try:
            provider = get_provider()
            result = await provider.parse_task_text(job.raw_text)
            
            if result.confidence < CONFIDENCE_THRESHOLD:
                logger.warning("Низкий ConfidenceScore (%f < %f)", result.confidence, CONFIDENCE_THRESHOLD)
                job.state = JobState.FAILED
                job.error_message = (
                    f"Не удалось распознать параметры заявки автоматически "
                    f"(уверенность модели: {result.confidence:.2f} < {CONFIDENCE_THRESHOLD}). "
                    f"Пожалуйста, уточните имя компьютера и модель принтера."
                )
                return job
            
            # Валидация модели из LLM по нашей БЗ
            driver = self.kb.find_by_key(result.model_key)
            if not driver:
                logger.warning("Модель '%s' из ответа LLM не найдена в БЗ", result.model_key)
                job.state = JobState.FAILED
                job.error_message = f"Модель принтера '{result.model_key}', определенная моделью ИИ, не зарегистрирована в Базе Знаний."
                return job

            job.target_pc = result.target_pc or job.target_pc
            job.model_key = result.model_key or job.model_key
            job.connection_type = result.connection_type
            job.driver_info = driver
            
            if driver.connection_type == "tcpip":
                job.printer_address = result.printer_address or self._parse_ip(job.raw_text)
                if not job.printer_address:
                    job.state = JobState.FAILED
                    job.error_message = "Для сетевого принтера (tcpip) не удалось определить IP-адрес или DNS-имя."
                    return job
            
            logger.info("Smart-Track успешно завершен для задачи #%d", job.task_id)
            job.state = JobState.ROUTING
            return job

        except Exception as e:
            logger.exception("Ошибка парсинга в Smart-Track: %s", e)
            job.state = JobState.FAILED
            job.error_message = f"Ошибка разбора текста заявки искусственным интеллектом: {e}"
            return job
