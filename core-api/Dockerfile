# ----------------------------------------------------
# Этап 1: Сборка фронтенда Web UI (Node.js)
# ----------------------------------------------------
FROM node:20-alpine AS frontend-builder
WORKDIR /app/intra-web
COPY intra-web/package*.json ./
RUN npm ci
COPY intra-web/ ./
RUN npm run build


# ----------------------------------------------------
# Этап 2: Финальный контейнер Core API (Python 3.13)
# ----------------------------------------------------
FROM python:3.13-slim
WORKDIR /app

# Настройка переменных окружения Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/app"

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Копирование и установка зависимостей Python
COPY core-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование единого пакета общих утилит (SSOT) и бэкенда
COPY shared/ ./shared/
COPY core-api/ .
COPY docs/presentations/ ./docs/presentations/

# Копирование скомпилированного singlefile HTML из этапа сборки фронтенда
COPY --from=frontend-builder /app/core-api/app/static/admin/index.html ./app/static/admin/index.html

# Открытие порта для приложения
EXPOSE 8000

# Команда для запуска FastAPI через uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ----------------------------------------------------
# Этап 3: Тестовый образ (не используется в production)
# ----------------------------------------------------
# Dev-зависимости фиксируются корневым uv.lock.  Отдельный target позволяет
# запускать pytest в Docker, не добавляя тестовые инструменты в runtime-образ.
FROM python:3.13-slim AS test
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/app"

COPY pyproject.toml uv.lock /workspace/
RUN pip install --no-cache-dir uv \
    && cd /workspace \
    && uv sync --frozen --group dev --no-install-project

COPY shared/ ./shared/
COPY core-api/ .
# Тесты execution worker вычисляют путь от /app/tests к /execution-worker.
# Модуль нужен только test target и не попадает в production-образ.
COPY execution-worker/ /execution-worker/
# MCP-тесты аналогично используют абсолютный monorepo-путь от /app/tests.
COPY intralink-mcp/ /intralink-mcp/

ENTRYPOINT ["/workspace/.venv/bin/python", "-m", "pytest"]
CMD ["tests", "-q"]
