# Архитектура проекта IntraBot-gemini

Этот документ содержит визуальные схемы, описывающие микросервисную архитектуру проекта, разделенного на **Core API Gateway** и **Telegram Bot**, их внутренние компоненты, уровни абстракции и потоки данных.

---

## 1. Схема компонентов и слоев системы

Проект разделен на два основных сервиса, которые общаются по протоколу HTTP REST с использованием API-ключа авторизации (`X-Bot-Api-Key`):

```mermaid
flowchart TB
    %% Определение стилей
    classDef userStyle fill:#2d3748,stroke:#4a5568,stroke-width:2px,color:#fff;
    classDef telegramStyle fill:#3182ce,stroke:#2b6cb0,stroke-width:2px,color:#fff;
    classDef handlerStyle fill:#dd6b20,stroke:#c05621,stroke-width:2px,color:#fff;
    classDef serviceStyle fill:#319795,stroke:#2c7a7b,stroke-width:2px,color:#fff;
    classDef dbStyle fill:#805ad5,stroke:#6b46c1,stroke-width:2px,color:#fff;
    classDef externalStyle fill:#e53e3e,stroke:#9b2c2c,stroke-width:2px,color:#fff;
    classDef redisStyle fill:#c53030,stroke:#9b2c2c,stroke-width:2px,color:#fff;

    subgraph Clients ["Пользовательский интерфейс"]
        User(["👤 Пользователь в Telegram"]):::userStyle
    end

    subgraph Telegram_Layer ["Внешний слой Telegram"]
        TG_API["💬 Telegram Bot API"]:::telegramStyle
    end

    subgraph Bot_Service ["Telegram Bot Service (Python / aiogram)"]
        direction TB
        B_Main["🚀 main.py"]:::serviceStyle
        B_Config["⚙️ config.py"]:::serviceStyle
        
        subgraph B_Handlers ["Слой представления (Handlers)"]
            H_Start["start_help.py<br/>(Старт / Помощь)"]:::handlerStyle
            H_Auth["auth.py<br/>(Вход / Выход)"]:::handlerStyle
            H_Tickets["tickets.py<br/>(Список заявок)"]:::handlerStyle
        end

        subgraph B_Logic ["Слой интеграции"]
            API_Client["api_client.py<br/>(HTTP Core API клиент)"]:::serviceStyle
            Redis_Listener["redis_listener.py<br/>(Redis Pub/Sub Subscriber)"]:::serviceStyle
        end
    end

    subgraph Broker_Layer ["Шина сообщений"]
        Redis_Broker[("Redis Pub/Sub")]:::redisStyle
    end

    subgraph Core_API_Service ["Core API Service (Python / FastAPI)"]
        direction TB
        API_Main["🚀 main.py"]:::serviceStyle
        API_Config["⚙️ config.py"]:::serviceStyle
        
        subgraph API_Routers ["Роутеры API (Endpoints)"]
            R_Auth["auth.py<br/>(/auth/login, /auth/logout)"]:::handlerStyle
            R_Tasks["tasks.py<br/>(/tasks, /statuses)"]:::handlerStyle
            R_Users["users.py<br/>(/users)"]:::handlerStyle
        end

        subgraph API_Services ["Службы & База данных"]
            IS_Client["intraservice.py<br/>(aiohttp клиент)"]:::serviceStyle
            Worker["worker.py<br/>(APScheduler + Redis Publisher)"]:::serviceStyle
            DB_Module["db.py<br/>(SQLAlchemy async)"]:::dbStyle
        end
    end

    subgraph DB_Layer ["Слой данных"]
        Postgres_DB[("database PostgreSQL")]:::dbStyle
    end

    subgraph External_Systems ["Внешние системы"]
        IS_API["🌐 IntraService REST API"]:::externalStyle
    end

    %% Связи
    User <-->|Взаимодействие| TG_API
    TG_API <-->|Long Polling / Webhook| B_Main
    B_Main -->|Регистрация роутеров| B_Handlers
    B_Main -->|Запуск слушателя| Redis_Listener
    
    H_Start & H_Auth & H_Tickets -->|Вызов методов| API_Client
    
    %% Бот -> Core API
    API_Client <-->|REST HTTP (X-Bot-Api-Key)| API_Main
    
    API_Main -->|Маршрутизация| API_Routers
    
    R_Auth & R_Tasks & R_Users -->|Бизнес-логика| IS_Client
    R_Auth & R_Users -->|Работа с сессиями| DB_Module
    
    %% Воркер -> БД, IntraService и Redis
    Worker -->|Чтение пользователей & стейта| DB_Module
    Worker -->|Запрос обновлений| IS_Client
    Worker -->|Публикация событий| Redis_Broker
    
    %% Redis -> Бот
    Redis_Broker -->|Доставка событий| Redis_Listener
    Redis_Listener -->|Отправка уведомлений| TG_API
    
    DB_Module <-->|SQLAlchemy Async Connection| Postgres_DB
    IS_Client <-->|REST HTTP Basic Auth| IS_API
```

---

## 2. Диаграмма потока данных (Data Flow)

Ниже представлены sequence-диаграммы, показывающие, как данные передаются между сервисами при выполнении типичных операций.

### А. Процесс авторизации пользователя
В новой архитектуре бот не хранит никаких паролей или токенов в своей локальной памяти. Все данные учетной записи отправляются и сохраняются в Core API:

```mermaid
sequenceDiagram
    autonumber
    actor User as Пользователь
    participant BotH as bot/handlers/auth.py
    participant BotC as bot/services/api_client.py
    participant CoreM as core-api/app/main.py
    participant CoreIS as core-api/app/services/intraservice.py
    participant CoreDB as core-api/app/database/db.py
    participant IS as IntraService API

    User->>BotH: /login (Ввод логина и пароля)
    Note over BotH: Удаление сообщения с паролем для безопасности
    BotH->>BotC: login(tg_user_id, login, password)
    BotC->>CoreM: POST /api/v1/auth/login (X-Bot-Api-Key)
    Note over CoreM: Проверка X-Bot-Api-Key в deps.verify_api_key
    CoreM->>CoreIS: verify_credentials(login, password)
    CoreIS->>IS: HTTP GET /api/user?getcurrentuserinfo=true (Basic Auth)
    IS-->>CoreIS: Ответ (UserInfo с Id сотрудника)
    Note over CoreIS: Кодирование login:password в Base64
    CoreIS-->>CoreM: Возврат (auth_b64, user_id)
    CoreM->>CoreDB: Сохранение сессии в PostgreSQL
    CoreDB-->>CoreM: Подтверждение записи
    CoreM-->>BotC: Response (status="success", is_user_id=...)
    BotC-->>BotH: Данные ответа
    BotH->>User: Сообщение: Авторизация прошла успешно!
```

### Б. Фоновый опрос обновлений планировщиком (Scheduler Loop) и шина сообщений

В новой архитектуре планировщик запущен на стороне Core API. Он опрашивает IntraService напрямую, используя сохраненный Base64 токен, и публикует результаты в Redis Pub/Sub. Бот слушает канал Redis и пересылает сообщения пользователям в Telegram:

```mermaid
sequenceDiagram
    autonumber
    participant Work as core-api/app/services/worker.py (APScheduler)
    participant CoreDB as core-api/app/database/db.py (PostgreSQL)
    participant CoreIS as core-api/app/services/intraservice.py
    participant IS as IntraService API
    participant Redis as Redis (Pub/Sub)
    participant Listener as bot/services/redis_listener.py
    participant TG as Telegram API

    loop Каждые POLLING_INTERVAL секунд
        Work->>CoreDB: Запрос всех пользователей
        CoreDB-->>Work: Список пользователей (tg_user_id, last_task_id, last_check_time...)
        
        loop Для каждого пользователя
            %% 1. Новые задачи
            Work->>CoreIS: get_tasks(auth_b64, CreatedMoreThan)
            CoreIS->>IS: GET /api/task (Basic Auth)
            IS-->>CoreIS: Список созданных задач
            CoreIS-->>Work: Задачи
            
            Note over Work: Фильтрация: Id > last_task_id
            alt Есть новые задачи
                Work->>Redis: Publish ("intraservice_events", "new_task" payload)
                Work->>CoreDB: Сохранение состояния (last_task_id)
            end

            %% 2. Изменения / комментарии
            Work->>CoreIS: get_tasks(auth_b64, ChangedMoreThan)
            CoreIS->>IS: GET /api/task (Basic Auth)
            IS-->>CoreIS: Измененные задачи
            CoreIS-->>Work: Задачи
            
            loop Для каждой измененной задачи с пользователем в качестве исполнителя
                Work->>CoreIS: get_task_lifetime(auth_b64, task_id)
                CoreIS->>IS: GET /api/tasklifetime (Basic Auth)
                IS-->>CoreIS: События задачи
                CoreIS-->>Work: События
                Note over Work: Фильтрация событий по времени
                alt Есть новые комментарии
                    Work->>Redis: Publish ("intraservice_events", "new_comment" payload)
                else Изменен статус
                    Work->>Redis: Publish ("intraservice_events", "status_change" payload)
                end
            end

            Work->>CoreDB: Сохранить время проверки (last_check_time)
        end
    end

    %% Асинхронное получение и доставка сообщений в Telegram
    loop При получении сообщения в канале "intraservice_events"
        Redis->>Listener: Сообщение (данные события в JSON)
        Note over Listener: Декодирование JSON и валидация
        Listener->>TG: bot.send_message(tg_user_id, message, parse_mode="HTML")
        alt TelegramForbiddenError / Чат не найден
            Listener->>CoreDB: Удаление сессии пользователя (logout)
        end
    end
```
