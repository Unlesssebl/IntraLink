# ----------------------------------------------------
# Этап 1: Сборка фронтенда Admin UI (Node.js)
# ----------------------------------------------------
FROM node:20-alpine AS frontend-builder
WORKDIR /app/admin-ui
COPY admin-ui/package*.json ./
RUN npm ci
COPY admin-ui/ ./
RUN npm run build

# ----------------------------------------------------
# Этап 2: Финальный контейнер Core API (Python 3.13)
# ----------------------------------------------------
FROM python:3.13-slim
WORKDIR /app

# Настройка переменных окружения Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Копирование и установка зависимостей Python
COPY core-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода бэкенда
COPY core-api/ .

# Копирование скомпилированного singlefile HTML из этапа сборки фронтенда
COPY --from=frontend-builder /app/core-api/app/static/admin/index.html ./app/static/admin/index.html

# Открытие порта для приложения
EXPOSE 8000

# Команда для запуска FastAPI через uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
