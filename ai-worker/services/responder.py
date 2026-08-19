import logging
from pydantic import BaseModel, Field
from openai import AsyncOpenAI

from core.config import settings
from services.classifier import AIClassifier
from services.is_client import add_task_comment, update_task_status
from services.redis_client import get_redis_client

logger = logging.getLogger(__name__)

STATUS_RESOLVED = 29  # Выполнена


class ResponderResult(BaseModel):
    reply_text: str = Field(
        description="Текст ответа пользователю от лица инженера техподдержки на русском языке."
    )
    confidence: float = Field(
        description="Уверенность в правильности ответа от 0.0 до 1.0."
    )
    can_resolve: bool = Field(
        description="Является ли данный ответ окончательным решением проблемы (True/False)."
    )
    needs_clarification: bool = Field(
        description="Требуется ли от пользователя уточняющая информация для решения проблемы (True/False)."
    )
    reason: str = Field(
        description="Краткое обоснование принятого решения (для логирования)."
    )


class AIResponder:
    def __init__(self):
        self.litellm_key = settings.LITELLM_API_KEY
        self.litellm_base_url = settings.LITELLM_BASE_URL
        self.model_name = settings.GEMINI_MODEL
        self.llm_client = AsyncOpenAI(
            api_key=self.litellm_key, base_url=self.litellm_base_url
        )
        self._classifier = AIClassifier()

    async def should_auto_reply(self, task: dict, redis_client) -> bool:
        """
        Проверяет, удовлетворяет ли заявка критериям автоматического ответа.
        """
        task_id = task.get("Id")
        service_id = task.get("ServiceId")
        status_id = task.get("StatusId")

        # 1. Проверяем, что ID услуги входит в список разрешенных
        if not service_id or service_id not in settings.AUTO_REPLY_SERVICE_IDS:
            return False

        # 2. Отвечаем только на новые/открытые заявки (обычно STATUS_OPEN_ID = 31)
        if status_id != settings.STATUS_OPEN_ID:
            return False

        # 3. Проверяем, не была ли задача уже обработана автоответчиком
        if await redis_client.get(f"ai_replied:{task_id}"):
            logger.info(
                "Заявка #%s уже обработана автоответчиком (найден ключ в Redis)",
                task_id,
            )
            return False

        # 4. Проверяем, не передана ли задача какому-либо исполнителю (например, printer-worker)
        if await redis_client.get(f"dispatched:{task_id}"):
            logger.info(
                "Заявка #%s находится в обработке у специализированного воркера (dispatched)",
                task_id,
            )
            return False

        return True

    async def generate_reply(self, task: dict) -> ResponderResult:
        """
        Генерирует ответ на заявку с использованием RAG и LLM.
        """
        name = task.get("Name", "")
        description = task.get("Description", "")
        service_name = task.get("ServiceName", "")
        service_id = task.get("ServiceId")

        # Получаем похожие кейсы через классификатор (чтобы не дублировать логику RAG)
        similar_cases = await self._classifier.get_similar_cases(name, description)

        prompt = f"""
Ты — опытный инженер технической поддержки Беликов Ален (ООО «АйТи ТЭМПО», телефон 49-87, кабинет АБК-3 112).
Твоя задача — проанализировать заявку пользователя и составить ответ строго в твоем фирменном стиле: кратко, профессионально, по существу, без лишних вступлений и «воды».

Текущая заявка:
- ID: {task.get("Id")}
- Тема: {name}
- Описание: {description}
- Раздел: "{service_name}" (ID: {service_id})

Похожие исторические кейсы с решениями из базы знаний:
{similar_cases}

ФИРМЕННЫЙ СТИЛЬ И ШАБЛОНЫ БЕЛИКОВА АЛЕНА:
1. ПК не в сети / сбои сетевого подключения:
"Не вижу ПК в сети.
1. Убедитесь в корректности имени ПК;
2. Перезагрузите компьютер;
3. Проверьте подключение сетевого кабеля;
4. Если кабель подключен, проверьте наличие световой индикации в месте подключения кабеля.
По вопросам звоните на номер 49-87."

2. Аппаратные сбои ПК / не включается / проблемы с диском / замена оборудования:
"Приносите ПК в АБК 3, 112 каб."

3. Доступ к Wi-Fi (WORK-NET):
"Доступ к Wi-Fi предоставлен.
Используйте логин и пароль от вашей учетной записи на ПК. Инструкцию по подключению приложил.
Если возникнут проблемы с подключением, приходите в АБК-3, кабинет 112."

4. Неверный раздел (отмена заявки):
"Заявка отменена, т. к. создана не в подходящем разделе.
Требуется оставить заявку в подходящем разделе: [Точное название раздела]."

5. Решение проблемы / инструкция:
Четкое описание действий без лишних слов. В конце обязательно: "Если возникнут вопросы, перезвоните на номер 49-87."

6. Если требуется время на выполнение:
"Ваша заявка принята в работу. По вопросам звоните на номер 49-87."

ПРАВИЛА ГЕНЕРАЦИИ:
- Пиши на русском языке.
- Никакой «воды» и избыточных шаблонных фраз.
- Если не хватает данных (имя ПК, ошибка, скриншот), запроси их конкретно и установи needs_clarification = True.
- Если даешь готовое решение, установи can_resolve = True.
"""

        try:
            response = await self.llm_client.beta.chat.completions.parse(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                response_format=ResponderResult,
                temperature=0.0,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise ValueError(
                    "Не удалось распарсить ответ LLM в формат ResponderResult"
                )
            result: ResponderResult = parsed
            logger.info(
                "Сгенерирован AI-ответ для заявки #%s (confidence=%.2f, can_resolve=%s, needs_clarify=%s). Причина: %s",
                task.get("Id"),
                result.confidence,
                result.can_resolve,
                result.needs_clarification,
                result.reason,
            )
            return result
        except Exception as e:
            logger.error(
                "Ошибка при генерации AI-ответа для заявки #%s через LLM: %s",
                task.get("Id"),
                e,
            )
            return ResponderResult(
                reply_text="Ваша заявка принята в работу.\n\nПо вопросам звоните на номер 49-87.",
                confidence=0.0,
                can_resolve=False,
                needs_clarification=False,
                reason=f"Сбой LLM генерации: {e}",
            )

    async def process_new_task(self, task: dict) -> bool:
        """
        Полный жизненный цикл обработки новой задачи: проверка -> генерация ответа -> отправка в IntraService.
        """
        raw_id = task.get("Id")
        if raw_id is None:
            logger.warning("Задача без ID пропущена.")
            return False
        task_id = int(raw_id)
        redis_client = get_redis_client()
        try:
            # 1. Проверяем необходимость ответа
            if not await self.should_auto_reply(task, redis_client):
                return False

            logger.info("Запуск автоматического AI-ответа на заявку #%s...", task_id)

            # 2. Генерируем ответ
            result = await self.generate_reply(task)

            # Если уверенность слишком низкая и это не дефолтный ответ, то не отвечаем автоматически
            if result.confidence < 0.6 and "Сбой LLM генерации" not in result.reason:
                logger.info(
                    "Уверенность автоответа для заявки #%s слишком низкая (%.2f < 0.6), автоответ отменен.",
                    task_id,
                    result.confidence,
                )
                return False

            # 3. Отправляем комментарий в IntraService
            comment_ok = await add_task_comment(task_id, result.reply_text)
            if not comment_ok:
                logger.error(
                    "Не удалось отправить комментарий с автоответом в заявку #%s",
                    task_id,
                )
                return False

            # 4. Обновляем статус в соответствии с конфигурацией и результатом генерации
            new_status_id = None
            if (
                settings.AUTO_REPLY_MODE == "comment_and_wait"
                and result.needs_clarification
            ):
                new_status_id = settings.STATUS_WAITING_ID  # 35 (Требует уточнения)
            elif (
                settings.AUTO_REPLY_MODE == "comment_and_resolve"
                and result.can_resolve
                and result.confidence >= 0.85
            ):
                new_status_id = STATUS_RESOLVED  # 29 (Выполнена)

            if new_status_id:
                status_ok = await update_task_status(task_id, new_status_id)
                if status_ok:
                    logger.info(
                        "Статус заявки #%s успешно изменен на %s",
                        task_id,
                        new_status_id,
                    )
                else:
                    logger.error(
                        "Не удалось обновить статус заявки #%s на %s",
                        task_id,
                        new_status_id,
                    )

            # 5. Помечаем задачу как отвеченную в Redis на 7 дней
            await redis_client.set(f"ai_replied:{task_id}", "1", ex=604800)

            # 6. Обновляем метрики в Redis
            try:
                await redis_client.hincrby("ai:stats", "replied", 1)
                await redis_client.hincrby("ai:stats", "total", 1)
                await redis_client.set("ai:stats:last_reply_time", str(task_id))
            except Exception as e_stats:
                logger.error(
                    "Не удалось обновить статистику автоответов в Redis: %s", e_stats
                )

            return True

        except Exception as e:
            logger.exception(
                "Критическая ошибка в процессе автоответа на заявку #%s: %s", task_id, e
            )
            return False
