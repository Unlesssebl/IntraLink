import asyncio
import os
import json
import logging
import sys

# Добавляем корневую директорию core-api в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.services import intraservice
from app.services.ai_classifier import AIClassifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("evaluate_classifier")

async def main():
    # Импортируем get_redis_client и sync_service_catalog
    from app.services.worker import get_redis_client, sync_service_catalog

    service_login = settings.INTRASERVICE_SERVICE_LOGIN
    service_password = settings.INTRASERVICE_SERVICE_PASSWORD

    auth_b64 = None
    if service_login and service_password:
        auth_b64, _ = await intraservice.verify_credentials(service_login, service_password)
        if not auth_b64:
            logger.error("Не удалось авторизоваться в IntraService с использованием настроек .env!")
            sys.exit(1)
        logger.info("Авторизация в IntraService успешна (через .env).")
    else:
        try:
            redis = get_redis_client()
            encrypted_auth = await redis.get("worker:service_auth_b64")
            if encrypted_auth:
                if isinstance(encrypted_auth, bytes):
                    encrypted_auth = encrypted_auth.decode()
                auth_b64 = encrypted_auth
                logger.info("Успешно получены учетные данные сервисного аккаунта из Redis.")
        except Exception as e:
            logger.error("Ошибка при получении учетных данных из Redis: %s", e)

    if not auth_b64:
        logger.error("Авторизационные данные не найдены ни в .env, ни в Redis!")
        sys.exit(1)
        
    # Синхронизируем каталог услуг перед тестом, чтобы он гарантированно был в Redis
    try:
        logger.info("Синхронизируем каталог услуг в Redis...")
        await sync_service_catalog()
    except Exception as e:
        logger.error("Не удалось синхронизировать каталог услуг: %s", e)
        
    classifier = AIClassifier()
    
    # Инициализируем aiohttp сессию
    await intraservice.init_session()
    
    # Получаем по 15 последних закрытых и отмененных задач
    test_tasks = []
    services_map = {}
    try:
        # 15 закрытых
        logger.info("Загружаем последние закрытые задачи для теста...")
        closed_data = await intraservice.get_tasks_by_status(auth_b64, status_id=28, page=1, page_size=15)
        if closed_data and "Tasks" in closed_data:
            test_tasks.extend(closed_data["Tasks"])
            for svc in closed_data.get("Services") or []:
                services_map[svc["Id"]] = svc["Name"]
            
        # 15 отмененных
        logger.info("Загружаем последние отмененные задачи для теста...")
        cancelled_data = await intraservice.get_tasks_by_status(auth_b64, status_id=30, page=1, page_size=15)
        if cancelled_data and "Tasks" in cancelled_data:
            test_tasks.extend(cancelled_data["Tasks"])
            for svc in cancelled_data.get("Services") or []:
                services_map[svc["Id"]] = svc["Name"]
    except Exception as e:
        logger.error("Ошибка при получении тестовых задач: %s", e)
        await intraservice.close_session()
        sys.exit(1)
        
    logger.info("Начинаем оценку классификатора на %d задачах...", len(test_tasks))
    
    stats = {
        "total": 0,
        "none_correct": 0,  # Задача закрыта, классификатор ответил none (True Negative)
        "none_incorrect": 0, # Задача закрыта, классификатор ответил redirect (False Positive)
        "redirect_correct": 0, # Задача отменена, классификатор ответил redirect (True Positive)
        "redirect_incorrect": 0, # Задача отменена, классификатор ответил none (False Negative)
        "errors": 0
    }
    
    results_log = []
    
    for task in test_tasks:
        task_id = task.get("Id")
        status_id_raw = task.get("StatusId")
        status_id = None
        if status_id_raw is not None:
            try:
                status_id = int(status_id_raw)
            except (ValueError, TypeError):
                pass
                
        status_name = "Закрыта" if status_id == 28 else ("Отменена" if status_id == 30 else f"Статус {status_id}")
        service_id = task.get("ServiceId")
        service_name = services_map.get(service_id) or task.get("ServiceName") or "Не указан"
        
        logger.info("Тестируем задачу #%s (%s), раздел: %s", task_id, status_name, service_name)
        
        try:
            result = await classifier.classify_task(task)
            stats["total"] += 1
            
            is_redirect = (result.action == "redirect")
            
            # Логика оценки:
            # Для закрытых задач идеальный ответ - none.
            # Для отмененных задач идеальный ответ - redirect.
            if status_id == 28: # Закрыта
                if not is_redirect:
                    stats["none_correct"] += 1
                    evaluation = "Правильно (True Negative)"
                else:
                    stats["none_incorrect"] += 1
                    evaluation = "Ошибка (False Positive)"
            elif status_id == 30: # Отменена
                if is_redirect:
                    stats["redirect_correct"] += 1
                    evaluation = "Правильно (True Positive)"
                else:
                    stats["redirect_incorrect"] += 1
                    evaluation = "Ошибка (False Negative)"
            else:
                evaluation = f"Неизвестный статус {status_id}"
                
            results_log.append({
                "task_id": task_id,
                "status": status_name,
                "service": service_name,
                "predicted_action": result.action,
                "predicted_service": result.correct_service_name,
                "evaluation": evaluation,
                "reason": result.reason,
                "comment": result.comment_text
            })
            
            logger.info("Результат: %s. Прогноз: %s -> %s (Reason: %s)", 
                        evaluation, result.action, result.correct_service_name, result.reason)
            
        except Exception as e:
            logger.error("Ошибка при обработке задачи #%s: %s", task_id, e)
            stats["errors"] += 1
            
        await asyncio.sleep(4.5)
        
    await intraservice.close_session()
    
    # Вывод результатов
    logger.info("=== РЕЗУЛЬТАТЫ ОЦЕНКИ ===")
    logger.info("Всего обработано: %d", stats["total"])
    logger.info("Правильно определено 'Оставить' (True Negative): %d", stats["none_correct"])
    logger.info("Ложное перенаправление 'Redirect' (False Positive): %d", stats["none_incorrect"])
    logger.info("Правильно перенаправлено 'Redirect' (True Positive): %d", stats["redirect_correct"])
    logger.info("Пропущено перенаправление 'None' (False Negative): %d", stats["redirect_incorrect"])
    logger.info("Ошибок выполнения: %d", stats["errors"])
    
    accuracy = 0.0
    if stats["total"] > 0:
        accuracy = (stats["none_correct"] + stats["redirect_correct"]) / stats["total"]
    logger.info("Итоговая точность (Accuracy): %.2f%%", accuracy * 100)
    logger.info("=========================")
    
    # Сохраняем подробный лог в файл
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evaluation_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "stats": stats,
            "accuracy": accuracy,
            "results": results_log
        }, f, ensure_ascii=False, indent=2)
    logger.info("Подробные результаты сохранены в %s", output_path)

if __name__ == "__main__":
    asyncio.run(main())
