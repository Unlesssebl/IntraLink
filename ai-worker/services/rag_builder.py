import asyncio
import os
import json
import logging
from typing import Any, Callable, Coroutine
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from sqlalchemy import select, func

from core.config import settings
from services import is_client as intraservice
from core.db import AsyncSessionLocal, TaskKnowledgeBase, init_db
from services.embeddings import get_embedding

logger = logging.getLogger(__name__)

# Инициализируем клиента OpenAI только если установлена библиотека
try:
    from openai import AsyncOpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("Библиотека openai не установлена. Запуск в режиме Dry-Run.")


class Classification(BaseModel):
    equipment_type: str = Field(
        description="Тип оборудования (например: Принтер/МФУ, ПК, Сеть, Монитор, Телефон, ПО, Прочее)"
    )
    action_type: str = Field(
        description="Тип действия (например: Настройка/Установка, Ремонт, Консультация, Замена, Доступ)"
    )
    tags: list[str] = Field(
        description="Ключевые теги (модель устройства, код ошибки, название ПО, ключевые слова)"
    )


class TaskKBEntry(BaseModel):
    problem: str = Field(
        description="Четкое описание проблемы пользователя без лишних деталей, эмоций и приветствий."
    )
    solution: str = Field(
        description="Точное решение, извлеченное строго из ответа инженера. Не придумывай от себя, сохраняй оригинальную суть и формулировки технического специалиста, отсеивая только мусор и приветствия."
    )
    classification: Classification


def clean_html(raw_html: str | None) -> str:
    """Удаляет простейшие HTML теги, если они есть в описании."""
    if not raw_html:
        return ""
    import re

    cleanr = re.compile("<.*?>")
    cleantext = re.sub(cleanr, "", raw_html)
    return cleantext.strip()


def load_checkpoint(checkpoint_path: str) -> dict:
    """Загружает сохраненное состояние (прогресс) обработки заявок."""
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "rb") as f:
                try:
                    import orjson
                    data = orjson.loads(f.read())
                except ImportError:
                    data = json.loads(f.read().decode("utf-8"))
                if "processed_task_ids" not in data:
                    data["processed_task_ids"] = []
                if "skipped_task_ids" not in data:
                    data["skipped_task_ids"] = {}
                return data
        except Exception as e:
            logger.error(
                "Ошибка при чтении чекпоинта %s: %s. Начинаем с нуля.",
                checkpoint_path,
                e,
            )

    return {"processed_task_ids": [], "skipped_task_ids": {}}


def save_checkpoint(checkpoint_path: str, data: dict):
    """Атомарно сохраняет прогресс в файл чекпоинта."""
    try:
        temp_path = checkpoint_path + ".tmp"
        try:
            import orjson
            content = orjson.dumps(data, option=orjson.OPT_INDENT_2)
            with open(temp_path, "wb") as f:
                f.write(content)
        except ImportError:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, checkpoint_path)
    except Exception as e:
        logger.error("Ошибка при сохранении чекпоинта %s: %s", checkpoint_path, e)


def should_skip_task_by_service_type(task: dict) -> tuple[bool, str]:
    """Проверяет, нужно ли пропустить задачу по типу или названию сервиса (фильтрация IT/не-IT)."""
    exclude_service_ids = [
        int(x.strip())
        for x in os.getenv("EXCLUDE_SERVICE_IDS", "").split(",")
        if x.strip().isdigit()
    ]
    exclude_service_names = [
        x.strip().lower()
        for x in os.getenv(
            "EXCLUDE_SERVICE_NAMES", "ахо,хозяйствен,канцеляри,клининг,охрана"
        ).split(",")
        if x.strip()
    ]
    exclude_type_names = [
        x.strip().lower()
        for x in os.getenv("EXCLUDE_TYPE_NAMES", "").split(",")
        if x.strip()
    ]

    service_id = task.get("ServiceId")
    if service_id is not None:
        try:
            if int(service_id) in exclude_service_ids:
                return True, f"ServiceId {service_id} в списке исключений"
        except (ValueError, TypeError):
            pass

    service_name = (task.get("ServiceName") or "").lower()
    for name in exclude_service_names:
        if name in service_name:
            return (
                True,
                f"ServiceName '{task.get('ServiceName')}' содержит исключение '{name}'",
            )

    type_name = (task.get("TypeName") or "").lower()
    for name in exclude_type_names:
        if name in type_name:
            return (
                True,
                f"TypeName '{task.get('TypeName')}' содержит исключение '{name}'",
            )

    return False, ""


def has_engineer_comment(task: dict, comments: list) -> bool:
    """
    Проверяет, что среди комментариев есть хотя бы один от инженера поддержки.
    """

    def safe_int(val) -> int | None:
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    creator_id = safe_int(task.get("CreatorId"))
    user_id = safe_int(task.get("UserId"))
    creator_name = (task.get("CreatorName") or "").lower()
    user_name = (task.get("UserName") or "").lower()
    executor_id = safe_int(task.get("ExecutorId"))

    executor_ids_raw = task.get("ExecutorIds") or []
    if isinstance(executor_ids_raw, str):
        executor_ids_raw = [x.strip() for x in executor_ids_raw.split(",") if x.strip()]
    elif isinstance(executor_ids_raw, int):
        executor_ids_raw = [executor_ids_raw]

    executor_ids_set = set()
    for eid in executor_ids_raw:
        if (val := safe_int(eid)) is not None:
            executor_ids_set.add(val)

    for item in comments:
        comment_text = item.get("Comments") or item.get("Comment")
        if not comment_text:
            continue

        comment_text_str = str(comment_text).strip()
        if "автоматически переведена в статус" in comment_text_str:
            continue

        editor_id = safe_int(item.get("EditorId"))
        editor_name = (item.get("Editor") or "").lower()

        if editor_name == "система" or editor_id == 0:
            continue

        if executor_id is not None and editor_id == executor_id:
            return True
        if executor_ids_set and editor_id in executor_ids_set:
            return True

        if creator_id is not None and editor_id == creator_id:
            continue
        if user_id is not None and editor_id == user_id:
            continue

        if creator_name and creator_name in editor_name:
            continue
        if user_name and user_name in editor_name:
            continue

        return True

    return False


def is_meaningful_solution(solution: str) -> bool:
    """Валидирует качество решения от LLM."""
    if not solution or len(solution.strip()) < 15:
        return False

    lower_sol = solution.lower().strip()
    useless_phrases = [
        "решение отсутствует",
        "нет решения",
        "решить не удалось",
        "проблема не решена",
        "не удалось решить",
        "ошибка",
        "не решено",
        "решение не предоставлено",
        "неизвестно",
        "решение не найдено",
        "нет информации о решении",
        "заявка закрыта без решения",
    ]

    for phrase in useless_phrases:
        if phrase in lower_sol:
            if len(lower_sol) < len(phrase) + 20:
                return False

    return True


async def process_task_with_gemini(
    client: Any,
    task: dict,
    comments: list,
    log_msg: Callable[[str], Coroutine[Any, Any, None]] | None = None,
) -> TaskKBEntry | None:
    """Отправляет данные заявки в LLM (через LiteLLM) для очистки и структурирования с повторными попытками."""
    model = task.get("Field1103", "")
    pc_name = task.get("Field1112", "")
    custom_fields_str = f"Модель МФУ/Принтера: {model}, Имя ПК: {pc_name}"

    formatted_comments = []
    for item in comments:
        comment_text = item.get("Comments") or item.get("Comment")
        creator = item.get("Editor") or "Система"
        date = item.get("Date")

        if comment_text:
            if "автоматически переведена в статус" in comment_text:
                continue
            formatted_comments.append(f"[{date}] {creator}: {comment_text}")

    comments_str = "\n".join(formatted_comments)

    prompt = f"""
Проанализируй закрытую или отмененную заявку технической поддержки и извлеки из нее структурированную базу знаний (Проблема -> Решение).
КРИТИЧЕСКОЕ ПРАВИЛО: При формировании поля solution (Решение) используй СТРОГО те действия, факты и формулировки, которые описал инженер технической поддержки (особенно если автор комментариев — Беликов Ален).
Твоя задача — сохранить оригинальную суть и лаконичные формулировки специалиста, отсеяв только мусор (эмоции, системные логи, приветствия). Не придумывай шаги от себя.
Решение должно быть оригинальным ответом техподдержки, просто очищенным от воды.

ОСОБОЕ ВНИМАНИЕ КЕЙСАМ ОТМЕНЫ (STATUS "ОТМЕНЕНА"): 
Если заявка была отменена инженером из-за того, что она создана в неверном разделе/сервисе каталога услуг, решением (solution) должно быть оригинальное указание правильного раздела (сервиса), в котором пользователю нужно пересоздать заявку, извлеченное строго из комментариев инженера поддержки.

Входные данные заявки:
Название: {task.get("Name")}
Описание: {clean_html(task.get("Description"))}
Сервис: {task.get("ServiceName")} (ID: {task.get("ServiceId")})
Тип: {task.get("TypeName")}
Текущий статус: {task.get("StatusName") or task.get("StatusId")}
Кастомные поля: {custom_fields_str}

История переписки (комментарии):
{comments_str}
"""

    if not OPENAI_AVAILABLE or not client:
        return TaskKBEntry(
            problem=f"Проблема из заявки: {task.get('Name')}",
            solution="[DRY-RUN] Решение не сгенерировано, так как клиент OpenAI/LiteLLM отсутствует.",
            classification=Classification(
                equipment_type="Прочее", action_type="Консультация", tags=["dry-run"]
            ),
        )

    model_name = settings.GEMINI_MODEL
    max_retries = 3
    initial_delay = 2
    backoff_factor = 2
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            response = await client.beta.chat.completions.parse(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                response_format=TaskKBEntry,
                temperature=0.1,
            )
            return response.choices[0].message.parsed
        except Exception as e:
            if attempt == max_retries:
                msg = f"Все попытки ({max_retries + 1}) запроса к LLM для заявки #{task.get('Id')} завершились сбоем: {e}"
                logger.error(msg)
                if log_msg:
                    await log_msg(f"[ERROR] {msg}")
                return None
            
            msg = f"Попытка {attempt + 1} запроса к LLM для заявки #{task.get('Id')} завершилась сбоем: {e}. Повтор через {delay} сек..."
            logger.warning(msg)
            if log_msg:
                await log_msg(f"[WARNING] {msg}")
                
            await asyncio.sleep(delay)
            delay *= backoff_factor

    return None


async def fetch_task_comments_safe(
    auth_b64: str, task_id: int, semaphore: asyncio.Semaphore
) -> tuple[int, list[dict] | None]:
    async with semaphore:
        try:
            comments = await intraservice.get_task_comments(auth_b64, task_id)
            if isinstance(comments, dict) and "TaskLifetimes" in comments:
                comments = comments["TaskLifetimes"]
            elif not isinstance(comments, list):
                comments = []
            return task_id, comments
        except Exception as e:
            logger.error(
                "Ошибка при получении комментариев для задачи %s: %s", task_id, e
            )
            return task_id, None


async def build_rag_dataset(
    filter_id: int,
    global_quotas: dict,
    service_quotas: dict,
    service_ids: list[int],
    auth_b64: str,
    executor_id: int | None = None,
    progress_callback: Callable[[str], Coroutine[Any, Any, None]] | None = None,
) -> dict:
    """
    Асинхронно добирает примеры в базу знаний RAG до заполнения квот по услугам и статусам.
    Приоритетно извлекает закрытые заявки указанного инженера (по умолчанию Беликов Ален).
    """

    async def log_msg(msg: str):
        logger.info(msg)
        if progress_callback:
            await progress_callback(msg)

    target_executor_id = executor_id if executor_id is not None else settings.RAG_EXECUTOR_ID
    await log_msg(
        f"Запуск процесса перестроения RAG-базы. Фильтр: {filter_id}, Исполнитель ID: {target_executor_id or 'Все'}"
    )
    await log_msg(f"Целевые услуги для проверки: {service_ids}")

    # Инициализация путей
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scripts_dir = os.path.join(base_dir, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)

    checkpoint_path = os.path.join(scripts_dir, "rag_checkpoint.json")

    # Инициализация БД
    try:
        await init_db()
        await log_msg("База данных PostgreSQL успешно инициализирована.")
    except Exception as e:
        await log_msg(f"Ошибка при инициализации базы данных: {e}")
        return {"status": "error", "message": f"DB Init failed: {e}"}

    # Загружаем каталог услуг из Redis для определения названий услуг по ID
    service_names_map = {}
    try:
        from services.redis_client import get_redis_client
        r_client = get_redis_client()
        catalog_str = await r_client.get("worker:service_catalog")
        if catalog_str:
            flat_catalog = json.loads(catalog_str)
            service_names_map = {
                item["id"]: item["name"]
                for item in flat_catalog
                if "id" in item and "name" in item
            }
    except Exception as e:
        await log_msg(f"Ошибка при получении каталога услуг из Redis: {e}")

    # Загружаем чекпоинт (только для skipped_task_ids)
    checkpoint = load_checkpoint(checkpoint_path)
    skipped_task_ids = checkpoint.get("skipped_task_ids", {})

    # Считаем текущее наполнение квот в БД
    db_counts = {}
    try:
        async with AsyncSessionLocal() as session:
            query = (
                select(
                    TaskKnowledgeBase.service_id,
                    TaskKnowledgeBase.status_name,
                    func.count(TaskKnowledgeBase.task_id),
                )
                .where(TaskKnowledgeBase.is_blacklisted.is_(False))
                .group_by(TaskKnowledgeBase.service_id, TaskKnowledgeBase.status_name)
            )
            res = await session.execute(query)
            for s_id, status_name, cnt in res.all():
                if s_id not in db_counts:
                    db_counts[s_id] = {}
                db_counts[s_id][status_name] = cnt
    except Exception as e:
        await log_msg(f"Ошибка при чтении текущей статистики из БД: {e}")
        return {"status": "error", "message": f"DB read failed: {e}"}

    stats = {
        "total_inspected": 0,
        "added_to_db": 0,
        "skipped_non_it": 0,
        "skipped_no_comments": 0,
        "skipped_no_engineer_comments": 0,
        "skipped_meaningless_solution": 0,
        "skipped_duplicates": 0,
        "llm_errors": 0,
    }

    # Клиент LiteLLM
    llm_client = (
        AsyncOpenAI(
            api_key=settings.LITELLM_API_KEY, base_url=settings.LITELLM_BASE_URL
        )
        if OPENAI_AVAILABLE
        else None
    )

    # Инициализируем aiohttp сессию
    await intraservice.init_session()

    try:
        for service_id in service_ids:
            for status_id in [28, 30]:
                status_name = "Закрыта" if status_id == 28 else "Отменена"

                # Вычисляем квоту для данной услуги и статуса
                quota = service_quotas.get(str(service_id), {}).get(str(status_id))
                if quota is None:
                    quota = global_quotas.get(str(status_id))
                if quota is None:
                    quota = 10 if status_id == 28 else 5

                current_count = db_counts.get(service_id, {}).get(status_name, 0)

                if current_count >= quota:
                    await log_msg(
                        f"Услуга ID {service_id}, статус '{status_name}': квота уже заполнена ({current_count}/{quota})."
                    )
                    continue

                needed = quota - current_count
                await log_msg(
                    f"Услуга ID {service_id}, статус '{status_name}': требуется собрать {needed} примеров (уже собрано {current_count}, квота {quota})."
                )

                page = 1
                added_for_combo = 0

                while added_for_combo < needed:
                    # Точечный запрос по фильтру, разделу и статусу
                    params = {
                        "include": "status,customfields,service",
                        "ServiceIds": str(service_id),
                        "StatusIds": str(status_id),
                        "page": page,
                        "pageSize": 50,
                    }
                    if filter_id > 0:
                        params["filterId"] = str(filter_id)
                    if target_executor_id:
                        params["ExecutorIds"] = str(target_executor_id)
                    tasks_data = await intraservice.get_tasks(auth_b64, params)

                    if not tasks_data or "Tasks" not in tasks_data:
                        await log_msg(
                            f"Больше нет заявок для услуги ID {service_id} и статуса '{status_name}'."
                        )
                        break

                    tasks = tasks_data["Tasks"]
                    if not tasks:
                        await log_msg(
                            f"Страница {page} пуста для услуги ID {service_id} и статуса '{status_name}'. Закончили сбор."
                        )
                        break

                    await log_msg(
                        f"Получено {len(tasks)} заявок на странице {page} для услуги ID {service_id} и статуса '{status_name}'."
                    )

                    tasks_to_process = []
                    cnt_already_db = 0
                    cnt_checkpoint = 0
                    cnt_non_it = 0

                    for task in tasks:
                        stats["total_inspected"] += 1
                        task_id = task.get("Id")
                        task_id_str = str(task_id)

                        # 1. Проверяем в БД: не в черном ли списке и не собран ли уже?
                        async with AsyncSessionLocal() as db_session:
                            db_task = await db_session.get(TaskKnowledgeBase, task_id)
                            if db_task:
                                cnt_already_db += 1
                                continue

                        # 2. Проверяем чекпоинт пропущенных
                        if task_id_str in skipped_task_ids:
                            cnt_checkpoint += 1
                            continue

                        # 3. Фильтруем non-IT разделы
                        should_skip, reason = should_skip_task_by_service_type(task)
                        if should_skip:
                            skipped_task_ids[task_id_str] = f"non_it_service: {reason}"
                            stats["skipped_non_it"] += 1
                            save_checkpoint(
                                checkpoint_path, {"skipped_task_ids": skipped_task_ids}
                            )
                            cnt_non_it += 1
                            continue

                        tasks_to_process.append(task)

                    if not tasks_to_process:
                        await log_msg(
                            f"Все {len(tasks)} заявок на странице {page} отфильтрованы (Уже в БД: {cnt_already_db}, В чекпоинте: {cnt_checkpoint}, Не-IT: {cnt_non_it}). Переходим на страницу {page + 1}."
                        )
                        page += 1
                        continue

                    await log_msg(
                        f"Страница {page}: отфильтровано (Уже в БД: {cnt_already_db}, В чекпоинте: {cnt_checkpoint}, Не-IT: {cnt_non_it}). "
                        f"Осталось проверить: {len(tasks_to_process)} заявок. Запрашиваем историю комментариев..."
                    )

                    # Параллельно опрашиваем комментарии
                    comments_semaphore = asyncio.Semaphore(5)
                    comments_results = await asyncio.gather(
                        *[
                            fetch_task_comments_safe(
                                auth_b64, task.get("Id"), comments_semaphore
                            )
                            for task in tasks_to_process
                        ]
                    )
                    comments_map = {tid: c for tid, c in comments_results}
                    
                    await log_msg(f"История комментариев получена для {len(comments_results)} заявок.")

                    for task in tasks_to_process:
                        if added_for_combo >= needed:
                            break

                        task_id = task.get("Id")
                        task_id_str = str(task_id)
                        
                        await log_msg(f"Начало обработки заявки #{task_id}: '{task.get('Name') or 'Без названия'}'")
                        
                        comments = comments_map.get(task_id) or []

                        valid_comments = []
                        for item in comments:
                            comment_text = item.get("Comments") or item.get("Comment")
                            if comment_text:
                                comment_text_str = str(comment_text).strip()
                                if (
                                    comment_text_str
                                    and "автоматически переведена в статус"
                                    not in comment_text_str
                                ):
                                    valid_comments.append(item)

                        if not valid_comments:
                            await log_msg(f"Заявка #{task_id} пропущена: нет содержательных комментариев.")
                            skipped_task_ids[task_id_str] = "no_comments"
                            stats["skipped_no_comments"] += 1
                            save_checkpoint(
                                checkpoint_path, {"skipped_task_ids": skipped_task_ids}
                            )
                            continue

                        if not has_engineer_comment(task, valid_comments):
                            await log_msg(f"Заявка #{task_id} пропущена: нет комментариев от инженера поддержки.")
                            skipped_task_ids[task_id_str] = "no_engineer_comments"
                            stats["skipped_no_engineer_comments"] += 1
                            save_checkpoint(
                                checkpoint_path, {"skipped_task_ids": skipped_task_ids}
                            )
                            continue

                        # LLM анализ
                        await log_msg(f"Заявка #{task_id}: отправка запроса в LLM ({settings.GEMINI_MODEL}) для анализа переписки...")
                        kb_entry = await process_task_with_gemini(
                            llm_client, task, valid_comments, log_msg
                        )
                        if not kb_entry:
                            await log_msg(f"[ERROR] Заявка #{task_id}: ошибка при анализе через LLM.")
                            stats["llm_errors"] += 1
                            continue

                        await log_msg(f"Заявка #{task_id}: анализ LLM завершен. Решение: '{kb_entry.solution[:60]}...'")

                        if not is_meaningful_solution(kb_entry.solution):
                            await log_msg(
                                f"Заявка #{task_id} отклонена: неинформативное решение ('{kb_entry.solution[:60]}...')."
                            )
                            skipped_task_ids[task_id_str] = (
                                f"meaningless_solution: {kb_entry.solution[:50]}..."
                            )
                            stats["skipped_meaningless_solution"] += 1
                            save_checkpoint(
                                checkpoint_path, {"skipped_task_ids": skipped_task_ids}
                            )
                            continue

                        # Генерируем эмбеддинг
                        await log_msg(f"Заявка #{task_id}: генерация векторного эмбеддинга...")
                        document_text = f"Проблема: {kb_entry.problem}\nРешение: {kb_entry.solution}"
                        try:
                            emb = await get_embedding(document_text)
                        except Exception as e:
                            await log_msg(
                                f"[ERROR] Ошибка при генерации эмбеддинга для заявки #{task_id}: {e}"
                            )
                            stats["llm_errors"] += 1
                            continue

                        # Семантическая дедупликация
                        await log_msg(f"Заявка #{task_id}: проверка на семантический дубликат в RAG-базе...")
                        is_duplicate = False
                        try:
                            async with AsyncSessionLocal() as db_session:
                                # Используем pgvector косинусное расстояние
                                query = (
                                    select(
                                        TaskKnowledgeBase,
                                        (
                                            1.0
                                            - TaskKnowledgeBase.embedding.cosine_distance(
                                                emb
                                            )
                                        ).label("similarity"),
                                    )
                                    .where(
                                        TaskKnowledgeBase.service_id == service_id,
                                        TaskKnowledgeBase.is_blacklisted.is_(False),
                                        TaskKnowledgeBase.embedding.is_not(None),
                                    )
                                    .order_by(
                                        TaskKnowledgeBase.embedding.cosine_distance(emb)
                                    )
                                    .limit(1)
                                )

                                res_dup = await db_session.execute(query)
                                row = res_dup.first()
                                if row:
                                    existing_entry, similarity = row
                                    if similarity > 0.95:
                                        await log_msg(
                                            f"Заявка #{task_id} отклонена: семантический дубликат заявки #{existing_entry.task_id} (сходство {similarity:.3f})"
                                        )
                                        skipped_task_ids[task_id_str] = (
                                            f"semantic_duplicate_of_{existing_entry.task_id}"
                                        )
                                        save_checkpoint(
                                            checkpoint_path,
                                            {"skipped_task_ids": skipped_task_ids},
                                        )
                                        stats["skipped_duplicates"] += 1
                                        is_duplicate = True
                        except Exception as e:
                            await log_msg(
                                f"[ERROR] Ошибка при проверке дубликатов для заявки #{task_id}: {e}"
                            )

                        if is_duplicate:
                            continue

                        # Сохраняем в БД
                        try:
                            service_name = service_names_map.get(service_id) or task.get("ServiceName") or ""
                            async with AsyncSessionLocal() as db_session:
                                db_entry = TaskKnowledgeBase(
                                    task_id=task_id,
                                    original_name=task.get("Name") or "",
                                    problem=kb_entry.problem,
                                    solution=kb_entry.solution,
                                    service_id=service_id,
                                    service_name=service_name,
                                    status_name=status_name,
                                    classification_data=kb_entry.classification.model_dump(),
                                    embedding=emb,
                                    is_blacklisted=False,
                                )
                                db_session.add(db_entry)
                                await db_session.commit()
                                await log_msg(
                                    f"Заявка #{task_id} успешно сохранена в RAG-базу."
                                )

                                added_for_combo += 1
                                stats["added_to_db"] += 1
                        except Exception as e:
                            await log_msg(
                                f"[ERROR] Ошибка записи в БД для заявки #{task_id}: {e}"
                            )
                            stats["llm_errors"] += 1
                            continue

                        # Небольшая задержка, чтобы не нагружать Rate Limiting API/LLM
                        await log_msg(f"Ожидание 4.0 сек для соблюдения лимитов API...")
                        await asyncio.sleep(4.0)

                    page += 1

        await log_msg("=== ПЕРЕСТРОЕНИЕ RAG БАЗЫ ЗАВЕРШЕНО ===")
        await log_msg(
            f"Проверено: {stats['total_inspected']}, Добавлено: {stats['added_to_db']}, Пропущено дублей: {stats.get('skipped_duplicates', 0)}"
        )

    finally:
        await intraservice.close_session()

    return stats
