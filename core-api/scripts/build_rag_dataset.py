"""
ETL-скрипт пакетной выгрузки, канонизации и векторизации базы знаний Helpdesk из IntraService.
Сохраняет извлеченные прецеденты (Проблема -> Решение -> Root Cause) в PostgreSQL (pgvector)
и локальный JSONL-архив с поддержкой возобновляемых чекпоинтов.

Использует модульные сервисы ядра:
- app.services.ai_synthesis.canonize_task_solution (очистка шума, выделение решения)
- app.services.rag.index_task_knowledge (генерация эмбеддингов, сохранение в pgvector)
- app.services.intraservice (клиент API IntraService)
"""

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

from sqlalchemy import select

# Разрешаем импорт модулей ядра core-api
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database.db import AsyncSessionLocal, TaskKnowledgeBase, init_db
from app.services import intraservice
from app.services.ai_synthesis import canonize_task_solution
from app.services.rag import index_task_knowledge
from app.services.worker import get_redis_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("build_rag_dataset")


def load_checkpoint(checkpoint_path: str) -> dict[str, Any]:
    """Загружает сохраненное состояние обработки заявок."""
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("processed_task_ids", [])
                data.setdefault("skipped_task_ids", {})
                data.setdefault("last_pages", {})
                return data
        except Exception as e:
            logger.warning("Сбой чтения чекпоинта %s: %s. Начинаем с нуля.", checkpoint_path, e)
    return {"processed_task_ids": [], "skipped_task_ids": {}, "last_pages": {}}


def save_checkpoint(checkpoint_path: str, data: dict[str, Any]) -> None:
    """Атомарно сохраняет прогресс в файл чекпоинта."""
    try:
        temp_path = checkpoint_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, checkpoint_path)
    except Exception as e:
        logger.error("Ошибка сохранения чекпоинта %s: %s", checkpoint_path, e)


def should_skip_task_by_service_type(task: dict[str, Any]) -> tuple[bool, str]:
    """Фильтрует не-IT заявки (АХО, охрана, клининг и др.) по ID или имени сервиса."""
    exclude_service_ids = [
        int(x.strip())
        for x in os.getenv("EXCLUDE_SERVICE_IDS", "").split(",")
        if x.strip().isdigit()
    ]
    exclude_service_names = [
        x.strip().lower()
        for x in os.getenv("EXCLUDE_SERVICE_NAMES", "ахо,хозяйствен,канцеляри,клининг,охрана").split(",")
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
            return True, f"ServiceName '{task.get('ServiceName')}' содержит '{name}'"

    type_name = (task.get("TypeName") or "").lower()
    for name in exclude_type_names:
        if name in type_name:
            return True, f"TypeName '{task.get('TypeName')}' содержит '{name}'"

    return False, ""


async def resolve_auth() -> str:
    """Получает учетные данные сервисного аккаунта IntraService из настроек или Redis."""
    service_login = settings.INTRASERVICE_SERVICE_LOGIN
    service_password = settings.INTRASERVICE_SERVICE_PASSWORD

    if service_login and service_password:
        auth_b64, _ = await intraservice.verify_credentials(service_login, service_password)
        if auth_b64:
            logger.info("Авторизация в IntraService успешна (из .env).")
            return auth_b64

    # Fallback на сохраненный токен в Redis
    try:
        redis = get_redis_client()
        encrypted_auth = await redis.get("worker:service_auth_b64")
        if encrypted_auth:
            if isinstance(encrypted_auth, bytes):
                encrypted_auth = encrypted_auth.decode()
            logger.info("Успешно получены учетные данные сервисного аккаунта из Redis.")
            return encrypted_auth
    except Exception as e:
        logger.error("Ошибка при получении учетных данных из Redis: %s", e)

    raise RuntimeError(
        "Логин или пароль сервисного аккаунта не заданы в .env и отсутствуют в Redis!"
    )


async def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    checkpoint_path = os.path.join(script_dir, "rag_checkpoint.json")
    jsonl_path = os.path.join(script_dir, "rag_dataset.jsonl")

    limit_tasks = int(os.getenv("LIMIT_TASKS", "50"))
    statuses = [28, 30]  # 28: Закрыта, 30: Отменена

    # Инициализация БД (pgvector) и HTTP сессии IntraService
    await init_db()
    await intraservice.init_session()

    try:
        auth_b64 = await resolve_auth()
    except Exception as e:
        logger.critical(str(e))
        await intraservice.close_session()
        sys.exit(1)

    checkpoint = load_checkpoint(checkpoint_path)
    processed_task_ids: set[int] = set(checkpoint.get("processed_task_ids", []))
    skipped_task_ids: dict[str, str] = checkpoint.get("skipped_task_ids", {})
    last_pages: dict[str, int] = checkpoint.get("last_pages", {})

    stats = {
        "total_inspected": 0,
        "added_to_db": 0,
        "already_in_checkpoint": 0,
        "skipped_non_it": 0,
        "skipped_no_solution": 0,
        "errors": 0,
    }

    new_processed_count = 0
    logger.info("Начало ETL-сбора датасета. Лимит новых записей: %d", limit_tasks)

    try:
        for status in statuses:
            status_str = str(status)
            page = last_pages.get(status_str, 1)
            logger.info("Сбор заявок для статуса %d (страница %d)", status, page)

            while new_processed_count < limit_tasks:
                tasks_data = await intraservice.get_tasks_by_status(
                    auth_b64=auth_b64, status_id=status, page=page, page_size=50
                )

                if not tasks_data:
                    logger.info("Заявки для статуса %d закончились.", status)
                    break

                tasks = tasks_data.get("Tasks", []) if isinstance(tasks_data, dict) else tasks_data
                if not tasks:
                    logger.info("Страница %d для статуса %d пуста. Завершение статуса.", page, status)
                    break

                logger.info("Получено %d заявок на странице %d для статуса %d", len(tasks), page, status)

                for task in tasks:
                    if new_processed_count >= limit_tasks:
                        break

                    stats["total_inspected"] += 1
                    task_id = task.get("Id")
                    if not task_id:
                        continue
                    task_id_str = str(task_id)

                    # 1. Проверка чекпоинта
                    if task_id in processed_task_ids or task_id_str in skipped_task_ids:
                        stats["already_in_checkpoint"] += 1
                        continue

                    # 2. Фильтрация не-IT
                    should_skip, reason = should_skip_task_by_service_type(task)
                    if should_skip:
                        skipped_task_ids[task_id_str] = f"non_it: {reason}"
                        stats["skipped_non_it"] += 1
                        continue

                    # 3. Проверка наличия в PostgreSQL
                    async with AsyncSessionLocal() as session:
                        stmt = select(TaskKnowledgeBase.task_id).where(TaskKnowledgeBase.task_id == task_id)
                        res = await session.execute(stmt)
                        if res.scalar_one_or_none():
                            processed_task_ids.add(task_id)
                            stats["already_in_checkpoint"] += 1
                            continue

                    # 4. Получение истории переписки и канонизация решения (SSOT)
                    lifetime = await intraservice.get_task_lifetime(auth_b64, task_id) or []
                    canon = canonize_task_solution(task, lifetime)
                    solution = canon.get("solution") or ""
                    problem = canon.get("problem") or task.get("Name") or ""
                    root_cause = canon.get("root_cause") or "Штатное выполнение"

                    if not solution or solution == "Заявка выполнена в штатном режиме." and not lifetime:
                        skipped_task_ids[task_id_str] = "no_meaningful_solution"
                        stats["skipped_no_solution"] += 1
                        continue

                    # 5. Векторизация и индексация в pgvector через index_task_knowledge (SSOT)
                    service_id = int(task.get("ServiceId") or 0)
                    service_name = task.get("ServiceName") or "Общие"
                    status_name = "Закрыта" if status == 28 else "Отменена"

                    try:
                        async with AsyncSessionLocal() as session:
                            ok = await index_task_knowledge(
                                db=session,
                                task_id=task_id,
                                original_name=task.get("Name") or f"Заявка #{task_id}",
                                problem=problem,
                                solution=solution,
                                service_id=service_id,
                                service_name=service_name,
                                status_name=status_name,
                                classification_data={
                                    "synced_from_history": True,
                                    "root_cause": root_cause,
                                    "etl_script": True,
                                },
                            )
                        if not ok:
                            stats["errors"] += 1
                            continue
                    except Exception as e:
                        logger.error("Ошибка векторизации/сохранения заявки #%d: %s", task_id, e)
                        stats["errors"] += 1
                        continue

                    # 6. Резервная запись в JSONL
                    try:
                        with open(jsonl_path, "a", encoding="utf-8") as f:
                            record = {
                                "task_id": task_id,
                                "name": task.get("Name") or "",
                                "service_id": service_id,
                                "service_name": service_name,
                                "problem": problem,
                                "solution": solution,
                                "root_cause": root_cause,
                                "timestamp": time.time(),
                            }
                            f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    except Exception as log_err:
                        logger.warning("Сбой записи в %s: %s", jsonl_path, log_err)

                    processed_task_ids.add(task_id)
                    new_processed_count += 1
                    stats["added_to_db"] += 1
                    logger.info("Заявка #%d успешно векторизована и добавлена в pgvector.", task_id)

                    # Сохранение чекпоинта
                    save_checkpoint(
                        checkpoint_path,
                        {
                            "processed_task_ids": list(processed_task_ids),
                            "skipped_task_ids": skipped_task_ids,
                            "last_pages": last_pages,
                        },
                    )

                page += 1
                last_pages[status_str] = page
                save_checkpoint(
                    checkpoint_path,
                    {
                        "processed_task_ids": list(processed_task_ids),
                        "skipped_task_ids": skipped_task_ids,
                        "last_pages": last_pages,
                    },
                )

        logger.info("=== ИТОГОВАЯ СТАТИСТИКА ETL ВЫГРУЗКИ ===")
        logger.info("Всего проверено заявок: %d", stats["total_inspected"])
        logger.info("Добавлено в pgvector: %d", stats["added_to_db"])
        logger.info("Уже было в чекпоинте/БД: %d", stats["already_in_checkpoint"])
        logger.info("Пропущено не-IT: %d", stats["skipped_non_it"])
        logger.info("Пропущено без решения: %d", stats["skipped_no_solution"])
        logger.info("Ошибок: %d", stats["errors"])
        logger.info("=========================================")

    finally:
        await intraservice.close_session()


if __name__ == "__main__":
    asyncio.run(main())
