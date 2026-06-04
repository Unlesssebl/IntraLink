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
            Scheduler["scheduler.py<br/>(APScheduler фоновый опрос)"]:::serviceStyle
        end
    end

    subgraph Core_API_Service ["Core API Service (Python / FastAPI)"]
        direction TB
        API_Main["🚀 main.py"]:::serviceStyle
        API_Config["⚙️ config.py"]:::serviceStyle
        
        subgraph API_Routers ["Роутеры API (Endpoints)"]
            R_Auth["auth.py<br/>(/auth/login, /auth/logout)"]:::handlerStyle
            R_Tasks["tasks.py<br/>(/tasks, /statuses)"]:::handlerStyle
            R_Users["users.py<br/>(/users, /users/state)"]:::handlerStyle
        end

        subgraph API_Services ["Службы & База данных"]
            IS_Client["intraservice.py<br/>(aiohttp клиент)"]:::serviceStyle
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
    B_Main -->|Запуск планировщика| Scheduler
    
    H_Start & H_Auth & H_Tickets -->|Вызов методов| API_Client
    Scheduler -->|Запрос обновлений & пользователей| API_Client
    
    %% Бот -> Core API
    API_Client <-->|REST HTTP (X-Bot-Api-Key)| API_Main
    
    API_Main -->|Маршрутизация| API_Routers
    
    R_Auth & R_Tasks & R_Users -->|Бизнес-логика| IS_Client
    R_Auth & R_Users -->|Работа с сессиями| DB_Module
    
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

### Б. Фоновый опрос обновлений планировщиком (Scheduler Loop)
Планировщик в боте периодически опрашивает Core API, который в свою очередь обращается к IntraService, используя сохраненный Base64 токен:

```mermaid
sequenceDiagram
    autonumber
    participant Sch as bot/services/scheduler.py
    participant BotC as bot/services/api_client.py
    participant CoreAPI as core-api/app (FastAPI)
    participant CoreDB as core-api/app/database/db.py
    participant CoreIS as core-api/app/services/intraservice.py
    participant IS as IntraService API
    participant TG as Telegram API

    loop Каждые POLLING_INTERVAL секунд
        Sch->>BotC: get_all_users()
        BotC->>CoreAPI: GET /api/v1/users (X-Bot-Api-Key)
        CoreAPI->>CoreDB: Получить список всех пользователей
        CoreDB-->>CoreAPI: Данные пользователей (tg_user_id, last_task_id, last_check_time...)
        CoreAPI-->>BotC: Список пользователей
        BotC-->>Sch: Список пользователей
        
        loop Для каждого пользователя
            %% 1. Новые задачи
            Sch->>BotC: get_tasks(tg_user_id, CreatedMoreThan)
            BotC->>CoreAPI: GET /api/v1/tasks?tg_user_id=...&CreatedMoreThan=...
            CoreAPI->>CoreDB: Запрос auth_b64 пользователя
            CoreDB-->>CoreAPI: auth_b64
            CoreAPI->>CoreIS: get_tasks(auth_b64, CreatedMoreThan)
            CoreIS->>IS: GET /api/task (Basic Auth)
            IS-->>CoreIS: Список созданных задач
            CoreIS-->>CoreAPI: Задачи
            CoreAPI-->>BotC: Задачи
            BotC-->>Sch: Задачи
            Note over Sch: Фильтрация: Id > last_task_id
            alt Есть новые задачи
                Sch->>TG: Отправка сообщения: "🆕 Новая заявка #ID"
                Sch->>BotC: update_user_state(tg_user_id, last_task_id)
                BotC->>CoreAPI: PATCH /api/v1/users/{id}/state (last_task_id)
                CoreAPI->>CoreDB: Сохранение состояния в PostgreSQL
            end

            %% 2. Изменения / комментарии
            Sch->>BotC: get_tasks(tg_user_id, ChangedMoreThan)
            BotC->>CoreAPI: GET /api/v1/tasks?tg_user_id=...&ChangedMoreThan=...
            CoreAPI->>CoreIS: get_tasks (с ExecutorIds)
            CoreIS->>IS: GET /api/task
            IS-->>CoreIS: Измененные задачи
            CoreIS-->>Sch: Задачи
            
            loop Для каждой измененной задачи
                Sch->>BotC: get_task_lifetime(tg_user_id, task_id)
                BotC->>CoreAPI: GET /api/v1/tasks/{id}/lifetime?tg_user_id=...
                CoreAPI->>CoreIS: get_task_lifetime(auth_b64, task_id)
                CoreIS->>IS: GET /api/tasklifetime
                IS-->>CoreIS: События задачи
                CoreIS-->>Sch: События
                Note over Sch: Фильтрация событий по времени
                alt Есть новые комментарии/статусы
                    Sch->>TG: Отправка сообщения: "💬 Новый комментарий / 🔄 Статус..."
                end
            end

            Sch->>BotC: update_user_state(tg_user_id, last_check_time)
            BotC->>CoreAPI: PATCH /api/v1/users/{id}/state (last_check_time)
            CoreAPI->>CoreDB: Сохранить время проверки в PostgreSQL
        end
    end
```
