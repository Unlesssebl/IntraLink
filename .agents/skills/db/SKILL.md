---
name: db
description: >-
  Проверка и управление состоянием базы данных PostgreSQL + pgvector (Tier 1 RAG).
  Используйте при вызове команды /db или проверке статуса docker-контейнера базы.
---

# 🗄️ Workflow: Проверка базы данных (`/db`)

Проверяет подключение к Docker-контейнеру PostgreSQL + pgvector (Tier 1 RAG) и запускает его при необходимости.

## Шаги выполнения:

1. **Проверка подключения:**
   Выполнить `uv run python helpdesk-cli/helpdesk.py check-db`.
2. **Автозапуск контейнера:**
   При недоступности базы выполнить `uv run python helpdesk-cli/helpdesk.py start-db` и дождаться готовности сервиса к работе.
