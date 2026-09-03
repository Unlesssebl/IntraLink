---
name: db
description: Проверка и управление состоянием базы данных PostgreSQL + pgvector
---

# Workflow: Проверка базы данных (`/db`)

Проверяет подключение к Docker-контейнеру PostgreSQL + pgvector (Tier 1 RAG) и запускает его при необходимости.

## Шаги выполнения:
1. **Проверка:** Выполнить `uv run python helpdesk-cli/helpdesk.py check-db`.
2. **Автозапуск:** При недоступности контейнера выполнить `uv run python helpdesk-cli/helpdesk.py start-db` и дождаться готовности.
