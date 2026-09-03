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
