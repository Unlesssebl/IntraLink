import os
import json
import logging
from debug_tools import add_to_path

add_to_path("ai-worker")


async def _fetch_rag_cases(name: str, description: str, limit: int = 5) -> list[dict]:
    """
    Напрямую запрашивает pgvector и возвращает сырой список кейсов с cosine score.
    Используется для интроспекции: что именно видит LLM в контексте.
    """
    from services.embeddings import get_embedding
    from core.db import AsyncSessionLocal, TaskKnowledgeBase
    from sqlalchemy import select

    query_text = f"Тема: {name}\nОписание: {description}"
    query_vector = await get_embedding(query_text)

    async with AsyncSessionLocal() as session:
        distance_expr = TaskKnowledgeBase.embedding.cosine_distance(query_vector)
        stmt = (
            select(TaskKnowledgeBase, distance_expr.label("distance"))
            .order_by("distance")
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()

    cases = []
    for row in rows:
        case: TaskKnowledgeBase = row[0]
        distance: float = row[1]
        similarity = round(1.0 - distance, 4)
        cases.append({
            "task_id": case.task_id,
            "service_name": case.service_name,
            "status_name": case.status_name,
            "similarity": similarity,
            "distance": round(distance, 4),
            "problem": case.problem,
            "solution": case.solution,
        })
    return cases


async def run_ai_debugger(task_data: dict):
    print("=" * 55)
    print("🤖  AI-WORKER DEBUGGER  (Dry-Run)")
    print("=" * 55)

    task_id = task_data.get("Id")
    name = task_data.get("Name", "")
    description = task_data.get("Description", "")
    service_name = task_data.get("ServiceName", "—")
    service_id = task_data.get("ServiceId", "—")

    print(f"\nID Заявки  : {task_id}")
    print(f"Раздел     : {service_name} (ID: {service_id})")
    print(f"Тема       : {name}")
    desc = str(description)
    print(f"Описание   : {desc[:200]}{'...' if len(desc) > 200 else ''}")

    # ── ШАГ 0: RAG-интроспекция (что реально найдёт pgvector) ──
    print("\n[0] 🔍 RAG-ПОИСК (pgvector cosine similarity, топ-5)...")
    try:
        cases = await _fetch_rag_cases(name, description, limit=5)
        if not cases:
            print("  ⚠️  pgvector не вернул кейсов. База знаний пуста?")
        else:
            for i, c in enumerate(cases, 1):
                sim_bar = "█" * int(c["similarity"] * 10) + "░" * (10 - int(c["similarity"] * 10))
                print(f"\n  #{i} Task {c['task_id']} | [{sim_bar}] sim={c['similarity']:.4f} | dist={c['distance']:.4f}")
                print(f"     Раздел  : {c['service_name']} | Статус: {c['status_name']}")
                print(f"     Проблема: {str(c['problem'])[:120]}{'...' if len(str(c['problem'])) > 120 else ''}")
                if c['solution']:
                    print(f"     Решение : {str(c['solution'])[:100]}{'...' if len(str(c['solution'])) > 100 else ''}")
    except Exception as e:
        print(f"  ❌ Ошибка RAG-поиска: {e}")
        print("     (Проверьте подключение к PostgreSQL и наличие эмбеддингов в БД)")

    # ── ШАГ 1: AIClassifier ────────────────────────────────────
    print("\n[1] 🧠 AIClassifier (классификация раздела, LLM + RAG)...")
    try:
        from services.classifier import AIClassifier
        classifier = AIClassifier()
        classification = await classifier.classify_task(task_data)

        action_icon = {"none": "✅ none", "redirect": "🔀 redirect"}.get(
            classification.action, f"❓ {classification.action}"
        )
        conf_bar = "█" * int(classification.confidence * 10) + "░" * (10 - int(classification.confidence * 10))
        print(f"  Действие    : {action_icon}")
        print(f"  Уверенность : [{conf_bar}] {classification.confidence:.2f}")
        print(f"  Обоснование : {classification.reason}")
        if classification.action == "redirect":
            print(f"  Перенаправить в: {classification.correct_service_name} (ID: {classification.correct_service_id})")
            print(f"  Комментарий:\n  {'─'*40}\n  {classification.comment_text}\n  {'─'*40}")
    except Exception as e:
        print(f"  ❌ Ошибка при классификации: {e}")

    # ── ШАГ 2: AIResponder ─────────────────────────────────────
    print("\n[2] 💬 AIResponder (генерация автоответа, LLM + RAG)...")
    try:
        from services.responder import AIResponder
        responder = AIResponder()
        reply = await responder.generate_reply(task_data)

        conf_bar = "█" * int(reply.confidence * 10) + "░" * (10 - int(reply.confidence * 10))
        print(f"  Может решить     : {'✅ Да' if reply.can_resolve else '❌ Нет'}")
        print(f"  Нужно уточнение  : {'⚠️  Да' if reply.needs_clarification else '✅ Нет'}")
        print(f"  Уверенность      : [{conf_bar}] {reply.confidence:.2f}")
        print(f"  Обоснование      : {reply.reason}")
        if reply.reply_text:
            print(f"\n  Текст ответа:\n  {'─'*40}")
            for line in reply.reply_text.splitlines():
                print(f"  {line}")
            print(f"  {'─'*40}")
        else:
            print("  ⚠️  Автоответ не сгенерирован (confidence слишком низкий или нужно уточнение).")
    except Exception as e:
        print(f"  ❌ Ошибка при генерации ответа: {e}")
