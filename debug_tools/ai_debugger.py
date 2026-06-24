import os
import json
from debug_tools import add_to_path

# Добавляем ai-worker в пути для импорта его модулей
add_to_path("ai-worker")

from services.classifier import AIClassifier
from services.responder import AIResponder

async def run_ai_debugger(task_data: dict):
    print("="*50)
    print("🤖 AI-WORKER DEBUGGER (Dry-Run)")
    print("="*50)
    print(f"ID Заявки: {task_data.get('Id')}")
    print(f"Раздел: {task_data.get('ServiceName')}")
    print(f"Заголовок: {task_data.get('Name')}")
    desc = str(task_data.get('Description', ''))
    print(f"Описание: {desc[:200]}{'...' if len(desc) > 200 else ''}")
    
    print("\n[1] 🧠 Запуск AIClassifier (Классификация раздела)...")
    try:
        classifier = AIClassifier()
        classification = await classifier.classify_task(task_data)
        print("✅ Результат классификации:")
        print(f"  Действие (Action): {classification.action}")
        print(f"  Причина (Reason): {classification.reason}")
        print(f"  Корректный раздел: {classification.correct_service_name}")
        print(f"  Текст комментария: {classification.comment_text}")
    except Exception as e:
        print(f"❌ Ошибка при классификации: {e}")

    print("\n[2] 💬 Запуск AIResponder (Генерация ответа)...")
    try:
        responder = AIResponder()
        reply = await responder.generate_reply(task_data)
        print("✅ Результат генерации ответа:")
        print(f"  Может решить (Can Resolve): {reply.can_resolve}")
        print(f"  Требует уточнений (Needs Clarification): {reply.needs_clarification}")
        print(f"  Уверенность (Confidence): {reply.confidence}")
        print(f"  Обоснование (Reason): {reply.reason}")
        print(f"  Текст ответа:\n{'-'*30}\n{reply.reply_text}\n{'-'*30}")
    except Exception as e:
        print(f"❌ Ошибка при генерации ответа: {e}")
