import asyncio
import re
import logging
from typing import Optional

from orchestrator.device_normalizer import normalize_pc_name, normalize_printer_address
from .schemas import PrintJob, JobState, KnowledgeBase, ErrorType, PrinterDriverInfo
from llm import get_provider

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.65


class JobRouter:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def _parse_printer_address(self, text: str) -> Optional[str]:
        # 1. Поиск IPv4 адреса (с учетом возможных опечаток: пробелы, запятые вместо точек)
        ip_match = re.search(r"\b\d{1,3}[., ]+\d{1,3}[., ]+\d{1,3}[., ]+\d{1,3}\b", text)
        if ip_match:
            matched_str = ip_match.group(0)
            return re.sub(r"[., ]+", ".", matched_str)

        # 2. Поиск имени принтера по префиксам из Базы Знаний
        prefixes = self.kb.printer_name_prefixes
        if prefixes:
            pattern = (
                rf"\b(?:{'|'.join(re.escape(p) for p in prefixes)})[a-zA-Z0-9.-]*\b"
            )
            printer_match = re.search(pattern, text, re.IGNORECASE)
            if printer_match:
                return printer_match.group(0).lower()

        return None

    def _resolve_bundle_driver(self, driver: PrinterDriverInfo, model_name: str) -> Optional[dict]:
        if not driver.driver_bundle or not model_name:
            return None
            
        import os
        import json
        
        index_filename = f"{driver.driver_bundle}_index.json"
        # Для обратной совместимости, если бандл "kyocera_kx_upd", файл называется kyocera_driver_index.json
        if driver.driver_bundle == "kyocera_kx_upd":
            index_filename = "kyocera_driver_index.json"
        elif driver.driver_bundle == "hp_aggregated_bundle":
            index_filename = "hp_driver_index.json"
            
        index_path = os.path.join(os.path.dirname(__file__), "..", "knowledge_base", index_filename)
        
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                models = data.get("models", {})
                
                # Функция для извлечения данных из словаря (строка для kyocera, dict для hp)
                def parse_entry(val):
                    if isinstance(val, dict):
                        return val.get("driver_name"), val.get("inf_path_suffix")
                    return val, None
                
                # Точный поиск
                for key, val in models.items():
                    if key.lower() == model_name.lower():
                        d_name, d_suffix = parse_entry(val)
                        return self._build_resolved_update(driver, d_name, d_suffix, model_name)
                
                # Нечёткий поиск (вхождение токенов)
                import re
                model_tokens = [t for t in model_name.lower().split() if len(t) > 2]
                model_numbers = set(re.findall(r'\d+', model_name))
                
                best_match_val = None
                best_score = 0
                for key, val in models.items():
                    key_lower = key.lower()
                    
                    # Жёсткий фильтр: если в названии модели есть цифры (например 428), 
                    # они ОБЯЗАТЕЛЬНО должны быть в имени драйвера, иначе это другая модель.
                    if model_numbers:
                        key_numbers = set(re.findall(r'\d+', key_lower))
                        if not model_numbers.intersection(key_numbers):
                            continue
                            
                    score = sum(1 for t in model_tokens if t in key_lower)
                    if score > 0 and score > best_score:
                        best_score = score
                        best_match_val = val
                
                if best_match_val:
                    d_name, d_suffix = parse_entry(best_match_val)
                    logger.info("Авто-подбор бандла %s: модель '%s' -> '%s'", driver.driver_bundle, model_name, d_name)
                    return self._build_resolved_update(driver, d_name, d_suffix, model_name)
            except Exception as e:
                logger.error("Ошибка чтения %s: %s", index_filename, e)
                
        logger.warning("Не удалось найти точный драйвер в бандле %s для модели '%s', fallback: %s", driver.driver_bundle, model_name, driver.driver_name)
        return None

    def _build_resolved_update(self, driver: PrinterDriverInfo, exact_driver_name: Optional[str], inf_path_suffix: Optional[str], model_name: Optional[str] = None) -> Optional[dict]:
        updates = {}
        if exact_driver_name and exact_driver_name != driver.driver_name:
            updates["driver_name"] = exact_driver_name
            
        if model_name:
            updates["model_key"] = model_name
            updates["display_name"] = model_name
            
        if inf_path_suffix:
            # Склеиваем корневой путь (указан в БД) и относительный путь к INF-файлу
            base_path = driver.driver_inf_path.rstrip("\\/")
            suffix_path = inf_path_suffix.replace("/", "\\").lstrip("\\")
            new_inf_path = f"{base_path}\\{suffix_path}"
            updates["driver_inf_path"] = new_inf_path
            
        return updates if updates else None

    def _validate_driver_inf(self, job: PrintJob, raw_model_name: str) -> bool:
        if job.driver_info and job.driver_info.driver_bundle:
            inf_path = job.driver_info.driver_inf_path
            if not inf_path or not inf_path.lower().endswith(".inf"):
                job.state = JobState.FAILED
                job.error_type = ErrorType.USER
                job.error_message = (
                    f"Не удалось подобрать драйвер для модели МФУ '{raw_model_name}'. "
                    f"Пожалуйста, добавьте драйвер в индекс или выберите Универсальный драйвер (HP Universal Printing PCL 6) вручную в веб-панели."
                )
                return False
        return True
    async def route(self, job: PrintJob) -> PrintJob:
        if job.printer_address:
            job.printer_address = normalize_printer_address(job.printer_address)
        if job.target_pc:
            job.target_pc = normalize_pc_name(job.target_pc)

        # 1. Извлечение адреса принтера (IP или Хостнейм) из текста, если он не задан
        if not job.printer_address:
            job.printer_address = normalize_printer_address(self._parse_printer_address(job.raw_text))

        # 2. Попытка SNMP-автоопределения модели (Высший приоритет)
        # Пробуем определить модель по сети, если адрес известен.
        if job.printer_address:
            from .snmp import probe_printer_model

            discovered_model = await probe_printer_model(job.printer_address)
            if discovered_model:
                driver = self.kb.find_by_name(discovered_model)
                resolved_updates = None
                
                if driver:
                    resolved_updates = self._resolve_bundle_driver(driver, discovered_model)
                else:
                    # Если точного совпадения в локальной БЗ нет, опрашиваем все агрегированные бандлы
                    for p in self.kb.printers:
                        if p.driver_bundle:
                            updates = self._resolve_bundle_driver(p, discovered_model)
                            if updates:
                                driver = p
                                resolved_updates = updates
                                logger.info("Бандл %s распознал модель '%s'", p.driver_bundle, discovered_model)
                                break

                if driver:
                    logger.info(
                        "SNMP Auto-Discovery: модель '%s' успешно определена по сети для %s",
                        driver.model_key,
                        job.printer_address,
                    )
                    
                    if resolved_updates:
                        driver = driver.model_copy(update=resolved_updates)
                    
                    job.model_key = driver.model_key
                    job.driver_info = driver
                    job.connection_type = driver.connection_type

                    # Если уже есть целевой ПК, маршрутизация завершена
                    if job.target_pc:
                        if not self._validate_driver_inf(job, discovered_model):
                            return job
                        logger.info(
                            "SNMP Auto-Discovery успешно завершил маршрутизацию для задачи #%d",
                            job.task_id,
                        )
                        return job
                else:
                    logger.warning(
                        "SNMP определил модель '%s', но она отсутствует в Базе Знаний и всех бандлах",
                        discovered_model,
                    )

        # 3. Попытка Fast-Track: проверка, есть ли уже предзаполненные поля (из кастомных полей IS)
        if (
            job.target_pc
            and job.model_key
            and job.model_key not in (None, "", "-", "unknown")
        ):
            logger.info(
                "Fast-Track: обнаружены предзаполненные поля (ПК: %s, Модель: %s)",
                job.target_pc,
                job.model_key,
            )
            # Сначала точный поиск по model_key
            driver = self.kb.find_by_key(job.model_key)
            # Если не нашли — нечёткий поиск по display_name или бандлам
            if not driver:
                driver = self.kb.find_by_name(job.model_key)
                resolved_updates = None
                
                if driver:
                    resolved_updates = self._resolve_bundle_driver(driver, job.model_key)
                else:
                    for p in self.kb.printers:
                        if p.driver_bundle:
                            updates = self._resolve_bundle_driver(p, job.model_key)
                            if updates:
                                driver = p
                                resolved_updates = updates
                                logger.info("Fast-Track: Бандл %s распознал модель '%s'", p.driver_bundle, job.model_key)
                                break

                if driver:
                    if resolved_updates:
                        driver = driver.model_copy(update=resolved_updates)
                    logger.info(
                        "Fast-Track: модель '%s' определена для строки '%s'",
                        driver.model_key,
                        job.model_key,
                    )

            if driver:
                job.model_key = driver.model_key  # нормализуем к model_key из БЗ
                job.driver_info = driver
                job.connection_type = driver.connection_type
                # Если сетевой принтер, проверяем наличие IP/DNS
                if driver.connection_type == "tcpip":
                    job.printer_address = normalize_printer_address(
                        job.printer_address or self._parse_printer_address(job.raw_text)
                    )

                if not self._validate_driver_inf(job, job.model_key):
                    return job

                logger.info("Fast-Track успешно пройден для задачи #%d", job.task_id)
                return job
            else:
                logger.warning(
                    "Модель %s не найдена в Базе Знаний ни по ключу, ни по имени. Фолбэк на Smart-Track.",
                    job.model_key,
                )

        # 4. Smart-Track: использование LLM-as-a-Function
        logger.info("Smart-Track: Запуск LLM-парсинга для задачи #%d", job.task_id)
        job.state = JobState.PARSING

        try:
            provider = get_provider()
            result = await provider.parse_task_text(job.raw_text)

            if result.confidence < CONFIDENCE_THRESHOLD:
                logger.warning(
                    "Низкий ConfidenceScore (%f < %f)",
                    result.confidence,
                    CONFIDENCE_THRESHOLD,
                )
                job.state = JobState.FAILED
                job.error_type = ErrorType.USER
                job.error_message = (
                    f"Не удалось автоматически определить параметры установки "
                    f"(уверенность модели: {result.confidence:.0%}). "
                    "Пожалуйста, уточните в комментарии: системное имя компьютера (например, NTEMW0123) "
                    "и модель принтера."
                )
                return job

            # Очистка model_key от LLM (на случай, если ИИ вернул с русскими буквами)
            if result.model_key:
                cyrillic = 'ОСАЕРХМТКВ'
                latin    = 'OCAEPXMTKB'
                tr_map = str.maketrans(cyrillic + cyrillic.lower(), latin + latin.lower())
                result.model_key = result.model_key.translate(tr_map).strip()

            # Валидация модели по нашей БЗ (если не определена ранее по SNMP)
            if not job.driver_info:
                driver = self.kb.find_by_key(result.model_key)
                resolved_updates = None
                
                if not driver:
                    driver = self.kb.find_by_name(result.model_key)
                    if driver:
                        resolved_updates = self._resolve_bundle_driver(driver, result.model_key)
                    else:
                        for p in self.kb.printers:
                            if p.driver_bundle:
                                updates = self._resolve_bundle_driver(p, result.model_key)
                                if updates:
                                    driver = p
                                    resolved_updates = updates
                                    break
                                    
                if not driver:
                    logger.warning(
                        "Модель '%s' из ответа LLM не найдена в БЗ и бандлах", result.model_key
                    )
                    job.state = JobState.FAILED
                    job.error_type = ErrorType.USER
                    job.error_message = f"Модель принтера '{result.model_key}', определенная моделью ИИ, не зарегистрирована в Базе Знаний."
                    return job
                
                if resolved_updates:
                    driver = driver.model_copy(update=resolved_updates)
                    
                job.model_key = driver.model_key
                job.driver_info = driver
                job.connection_type = driver.connection_type

            job.target_pc = normalize_pc_name(result.target_pc or job.target_pc)
            if not job.connection_type:
                job.connection_type = (
                    result.connection_type or job.driver_info.connection_type
                )

            if job.driver_info.connection_type == "tcpip":
                job.printer_address = normalize_printer_address(
                    job.printer_address
                    or result.printer_address
                    or self._parse_printer_address(job.raw_text)
                )

            if not self._validate_driver_inf(job, result.model_key):
                return job

            logger.info("Smart-Track успешно завершен для задачи #%d", job.task_id)
            job.state = JobState.ROUTING
            return job

        except Exception as e:
            logger.exception("Ошибка парсинга в Smart-Track: %s", e)
            job.state = JobState.FAILED
            job.error_message = (
                f"Ошибка разбора текста заявки искусственным интеллектом: {e}"
            )
            return job
