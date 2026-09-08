"""
Движок десенсибилизации (DLP/PII Sanitizer) и PII Vault на базе Redis.
Обеспечивает токенизацию чувствительных данных (ФИО, IP, хосты, почты, телефоны, пароли)
и их безопасную обратную деанонимизацию (Rehydration) после вызова облачной LLM.
"""
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.services.ai.schemas import (
    DataCircuit,
    EntityType,
    RouteDecision,
    RoutingMetadata,
    SanitizationResult,
)
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.ai_sanitizer")


class DataSanitizer:
    """
    Модуль инспекции, маскирования и деанонимизации чувствительных данных.
    """

    # Регулярные выражения для поиска чувствительных сущностей
    PATTERNS = {
        EntityType.CREDENTIAL: [
            re.compile(
                r"(?i)\b(?:пароль|pass|password|pwd|secret|токен|token)\s*[:=]\s*([^\s,;]+)",
                re.IGNORECASE,
            ),
            re.compile(r"\bBearer\s+([a-zA-Z0-9_\-\.]{20,})\b", re.IGNORECASE),
        ],
        EntityType.IP_ADDRESS: [
            # Внутренние/приватные IPv4 адреса (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8)
            re.compile(
                r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|127\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"
            ),
            # Общие IPv4 адреса
            re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
        ],
        EntityType.HOSTNAME: [
            # Имена корпоративных рабочих станций и серверов
            re.compile(
                r"\b(?:NTEMW|DESKTOP-|SRV-|WS-|PC-|LAPTOP-)[A-Za-z0-9_-]+\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b[A-Za-z0-9_-]+\.(?:local|corp|domain|lan|company\.ru)\b",
                re.IGNORECASE,
            ),
        ],
        EntityType.EMAIL: [
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
        ],
        EntityType.PHONE: [
            # Российские номера телефонов (+7 / 8 с различными разделителями)
            re.compile(
                r"(?:(?<=\s)|(?<=^)|(?<=[^\w\+]))(?:\+7|8)[\s\-(]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b"
            )
        ],
        EntityType.USER_NAME: [
            # ФИО с инициалами (Иванов И.И. / Иванов И. И.)
            re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.\b"),
            # Полное трехсловное ФИО (Иванов Иван Иванович)
            re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\b"),
        ],
    }

    def sanitize(self, text: str) -> SanitizationResult:
        """
        Сканирует текст и заменяет все найденные чувствительные сущности на обратимые маркеры.
        Возвращает очищенный текст и карту обратного соответствия.
        """
        if not text:
            return SanitizationResult(
                sanitized_text="", entity_map={}, detected_types=[]
            )

        sanitized_text = text
        entity_map: Dict[str, str] = {}
        detected_types: List[str] = []
        counters: Dict[str, int] = {
            "USER": 0,
            "INTERNAL_IP": 0,
            "IP": 0,
            "HOST": 0,
            "EMAIL": 0,
            "PHONE": 0,
            "CREDENTIAL": 0,
        }

        # 1. Поиск и маскирование учетных данных
        for pattern in self.PATTERNS[EntityType.CREDENTIAL]:
            for match in pattern.finditer(sanitized_text):
                val = match.group(1) if match.groups() else match.group(0)
                if val not in entity_map.values():
                    counters["CREDENTIAL"] += 1
                    token = f"{{{{CREDENTIAL_{counters['CREDENTIAL']}}}}}"
                    entity_map[token] = val
                    if EntityType.CREDENTIAL.value not in detected_types:
                        detected_types.append(EntityType.CREDENTIAL.value)

        # 2. Поиск и маскирование IP-адресов
        for pattern in self.PATTERNS[EntityType.IP_ADDRESS]:
            for match in pattern.finditer(sanitized_text):
                val = match.group(0)
                if val not in entity_map.values():
                    is_internal = val.startswith(("10.", "192.168.", "127.")) or (
                        val.startswith("172.")
                        and 16 <= int(val.split(".")[1]) <= 31
                    )
                    prefix = "INTERNAL_IP" if is_internal else "IP"
                    counters[prefix] += 1
                    token = f"{{{{{prefix}_{counters[prefix]}}}}}"
                    entity_map[token] = val
                    if EntityType.IP_ADDRESS.value not in detected_types:
                        detected_types.append(EntityType.IP_ADDRESS.value)

        # 3. Поиск и маскирование имен хостов
        for pattern in self.PATTERNS[EntityType.HOSTNAME]:
            for match in pattern.finditer(sanitized_text):
                val = match.group(0)
                if val not in entity_map.values():
                    counters["HOST"] += 1
                    token = f"{{{{HOST_{counters['HOST']}}}}}"
                    entity_map[token] = val
                    if EntityType.HOSTNAME.value not in detected_types:
                        detected_types.append(EntityType.HOSTNAME.value)

        # 4. Поиск и маскирование Email
        for pattern in self.PATTERNS[EntityType.EMAIL]:
            for match in pattern.finditer(sanitized_text):
                val = match.group(0)
                if val not in entity_map.values():
                    counters["EMAIL"] += 1
                    token = f"{{{{EMAIL_{counters['EMAIL']}}}}}"
                    entity_map[token] = val
                    if EntityType.EMAIL.value not in detected_types:
                        detected_types.append(EntityType.EMAIL.value)

        # 5. Поиск и маскирование телефонов
        for pattern in self.PATTERNS[EntityType.PHONE]:
            for match in pattern.finditer(sanitized_text):
                val = match.group(0)
                if val not in entity_map.values():
                    counters["PHONE"] += 1
                    token = f"{{{{PHONE_{counters['PHONE']}}}}}"
                    entity_map[token] = val
                    if EntityType.PHONE.value not in detected_types:
                        detected_types.append(EntityType.PHONE.value)

        # 6. Поиск и маскирование ФИО
        for pattern in self.PATTERNS[EntityType.USER_NAME]:
            for match in pattern.finditer(sanitized_text):
                val = match.group(0)
                if val not in entity_map.values():
                    counters["USER"] += 1
                    token = f"{{{{USER_{counters['USER']}}}}}"
                    entity_map[token] = val
                    if EntityType.USER_NAME.value not in detected_types:
                        detected_types.append(EntityType.USER_NAME.value)

        # Выполняем точную замену (от длинных строк к коротким во избежание коллизий подстрок)
        sorted_mappings = sorted(
            entity_map.items(), key=lambda x: len(x[1]), reverse=True
        )
        for token, original_val in sorted_mappings:
            sanitized_text = sanitized_text.replace(original_val, token)

        return SanitizationResult(
            sanitized_text=sanitized_text,
            entity_map=entity_map,
            detected_types=detected_types,
        )

    def deanonymize(self, text: str, entity_map: Dict[str, str]) -> str:
        """
        Восстанавливает исходные значения на месте токенов (Rehydration).
        Устойчив к изменениям пробелов внутри токенов со стороны LLM (например, `{{ USER_1 }}`).
        """
        if not text or not entity_map:
            return text

        restored_text = text

        for token, original_val in entity_map.items():
            # Точная замена
            restored_text = restored_text.replace(token, original_val)

            # Толерантная замена на случай если LLM изменила формат скобок
            clean_name = token.strip("{}")
            tolerant_regex = re.compile(
                r"\{\{\s*" + re.escape(clean_name) + r"\s*\}\}|\{\s*"
                + re.escape(clean_name) + r"\s*\}"
            )
            restored_text = tolerant_regex.sub(original_val, restored_text)

        return restored_text

    async def save_vault(
        self, session_id: str, entity_map: Dict[str, str], ttl_sec: int = 300
    ) -> bool:
        """
        Сохраняет таблицу PII Vault в Redis с TTL.
        """
        if not entity_map:
            return True
        try:
            r = get_redis_client()
            key = f"vault:pii:{session_id}"
            await r.set(key, json.dumps(entity_map, ensure_ascii=False), ex=ttl_sec)
            return True
        except Exception as e:
            logger.error("Ошибка сохранения PII Vault в Redis для %s: %s", session_id, e)
            return False

    async def load_vault(self, session_id: str) -> Dict[str, str]:
        """
        Загружает таблицу соответствия из Redis.
        """
        try:
            r = get_redis_client()
            key = f"vault:pii:{session_id}"
            data = await r.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error("Ошибка загрузки PII Vault из Redis для %s: %s", session_id, e)
        return {}

    def evaluate_circuit(
        self,
        prompt: str,
        metadata: RoutingMetadata,
        sanitization_result: Optional[SanitizationResult] = None,
    ) -> RouteDecision:
        """
        Принимает решение о контуре безопасности (Red, Yellow, Green) и целевой модели.
        """
        # force_circuit допускается только для повышения изоляции. Он не должен
        # обходить классификатор и отправлять чувствительные данные в облако.
        forced = metadata.force_circuit
        if forced == DataCircuit.RED:
            return RouteDecision(
                circuit=DataCircuit.RED,
                reason="Принудительный выбор более строгого закрытого контура",
                target_backend="ollama",
                target_model="local_qwen",
                requires_sanitization=False,
            )

        # 1. Проверка строгих критериев RED (пароли, учетные записи, конфиденциальность)
        if metadata.contains_credentials or metadata.is_confidential:
            return RouteDecision(
                circuit=DataCircuit.RED,
                reason="Обнаружены учетные данные или флаг конфиденциальности -> Строго локальный инференс",
                target_backend="ollama",
                target_model="local_qwen",
                requires_sanitization=False,
            )

        # Раздел информационной безопасности является RED-контуром даже если
        # конкретный текст не содержит распознаваемого regex-секрета.
        if metadata.service_id is not None:
            try:
                from app.services.rules.catalog import get_root_number_for_service_id

                if get_root_number_for_service_id(metadata.service_id) == "08":
                    return RouteDecision(
                        circuit=DataCircuit.RED,
                        reason="Заявка относится к разделу информационной безопасности -> Строго локальный инференс",
                        target_backend="ollama",
                        target_model="local_qwen",
                        requires_sanitization=False,
                    )
            except (TypeError, ValueError):
                logger.warning(
                    "Некорректный service_id при выборе DLP-контура: %r",
                    metadata.service_id,
                )

        # 2. Анализ контента на наличие учетных данных
        san_res = sanitization_result or self.sanitize(prompt)
        if EntityType.CREDENTIAL.value in san_res.detected_types:
            return RouteDecision(
                circuit=DataCircuit.RED,
                reason="В тексте обнаружены пароли/токены авторизации -> Строго локальный инференс",
                target_backend="ollama",
                target_model="local_qwen",
                requires_sanitization=False,
            )

        # 3. Если обнаружены PII (ФИО, IP, хосты, телефоны, email) -> YELLOW
        if san_res.detected_types:
            detected_str = ", ".join(san_res.detected_types)
            return RouteDecision(
                circuit=DataCircuit.YELLOW,
                reason=f"Обнаружены чувствительные сущности ({detected_str}) -> Десенсибилизация перед вызовом Gemini",
                target_backend="litellm_gemini",
                target_model="gemini_cloud",
                requires_sanitization=True,
            )

        # 4. GREEN разрешён только когда классификатор не нашёл чувствительных
        # данных. force_circuit=YELLOW остаётся допустимым повышением защиты.
        if forced == DataCircuit.YELLOW:
            return RouteDecision(
                circuit=DataCircuit.YELLOW,
                reason="Принудительный выбор более строгого трансформируемого контура",
                target_backend="litellm_gemini",
                target_model="gemini_cloud",
                requires_sanitization=True,
            )

        # 5. Если сущности не обнаружены -> GREEN
        return RouteDecision(
            circuit=DataCircuit.GREEN,
            reason="Чувствительные сущности не обнаружены -> Прямой вызов Gemini Cloud",
            target_backend="litellm_gemini",
            target_model="gemini_cloud",
            requires_sanitization=False,
        )


# Синглтон десенсибилизатора
data_sanitizer = DataSanitizer()
