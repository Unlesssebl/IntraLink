# Архитектура проекта IntraBot-gemini

Этот документ содержит визуальные схемы, описывающие архитектуру Telegram-бота, его внутренние компоненты, уровни абстракции и потоки данных при интеграции с IntraService и SQLite.

---

## 1. Схема компонентов и уровней системы

Данная блок-схема иллюстрирует разделение приложения на слои (Presentation, Business Logic, Data, External) и связи между ними:

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

    subgraph Telegram_Layer ["Слой Telegram Bot API"]
        TG_API["💬 Telegram Bot API"]:::telegramStyle
    end

    subgraph App_Layer ["Telegram Bot Application (Python / aiogram)"]
        direction TB
        
        subgraph Entry ["Точка входа"]
            Main["🚀 main.py"]:::serviceStyle
            Config["⚙️ config.py"]:::serviceStyle
        end

        subgraph Handlers ["Слой обработчиков (Presentation Layer)"]
            direction LR
            H_Start["start_help.py<br/>(Команды /start, /help)"]:::handlerStyle
            H_Auth["auth.py<br/>(Авторизация /login, FSM)"]:::handlerStyle
            H_Tickets["tickets.py<br/>(Заявки /mytickets, Пагинация)"]:::handlerStyle
        end

        subgraph Services ["Слой логики и интеграции (Business Logic)"]
            direction TB
            API_Client["api.py<br/>(aiohttp клиент IntraService)"]:::serviceStyle
            Scheduler["scheduler.py<br/>(APScheduler фоновый опрос)"]:::serviceStyle
        end

        subgraph Storage ["Слой данных (Data Layer)"]
            DB_Module["db.py<br/>(aiosqlite обертка)"]:::dbStyle
            SQLite_DB[("sqlite intrabot.db")]:::dbStyle
        end
    end

    subgraph External_Systems ["Внешние системы"]
        IS_API["🌐 IntraService REST API"]:::externalStyle
    end

    %% Связи
    User <-->|Взаимодействие| TG_API
    TG_API <-->|Поллинг событий & отправка сообщений| Main
    Main -->|Инициализация и запуск| Scheduler
    Main -->|Регистрация роутеров| Handlers
    
    H_Start -->|Проверка авторизации| DB_Module
    H_Auth -->|Сохранение сессии| DB_Module
    H_Auth -->|Проверка данных| API_Client
    H_Tickets -->|Запрос сессии| DB_Module
    H_Tickets -->|Запрос заявок| API_Client
    
    Scheduler -->|Периодический опрос по интервалу| API_Client
    Scheduler -->|Запрос и обновление состояния| DB_Module
    Scheduler -->|Уведомления о событиях| TG_API
    
    API_Client <-->|REST HTTP Basic Auth| IS_API
    
    DB_Module <-->|Асинхронные SQL запросы| SQLite_DB
```

---

## 2. Диаграмма потока данных (Data Flow)

Ниже представлена диаграмма, показывающая, как данные передаются между компонентами при выполнении типичных операций:

### А. Процесс авторизации и сохранения сессии
```mermaid
sequenceDiagram
    autonumber
    actor User as Пользователь
    participant H as handlers/auth.py (FSM)
    participant API as services/api.py
    participant DB as database/db.py
    participant IS as IntraService API

    User->>H: /login (Ввод логина и пароля)
    Note over H: Удаление сообщения с паролем для безопасности
    H->>API: verify_credentials(login, password)
    API->>IS: HTTP GET /api/user?getcurrentuserinfo=true (Basic Auth)
    IS-->>API: Response (UserInfo: ID, Name, Email...)
    Note over API: Кодирование login:password в Base64
    API-->>H: Возврат (auth_b64, user_id)
    H->>DB: add_or_update_user(tg_id, login, auth_b64, user_id)
    DB->>DB: Сохранение / обновление записи в intrabot.db
    DB-->>H: Подтверждение записи
    H->>User: Сообщение: Успешная авторизация + Reply-меню
```

### Б. Фоновый опрос обновлений (Scheduler Loop)
```mermaid
sequenceDiagram
    autonumber
    participant Sch as services/scheduler.py
    participant DB as database/db.py
    participant API as services/api.py
    participant IS as IntraService API
    participant TG as Telegram API

    loop Каждые POLLING_INTERVAL секунд
        Sch->>DB: get_all_users()
        DB-->>Sch: Список активных пользователей (auth_b64, last_task_id, last_check_time)
        
        Note over Sch: Для каждого пользователя:
        
        %% 1. Новые заявки
        Sch->>API: get_tasks(CreatedMoreThan = last_check_time)
        API->>IS: HTTP GET /api/task (Basic Auth)
        IS-->>API: Список созданных задач
        API-->>Sch: Данные задач
        Note over Sch: Фильтрация: Id > last_task_id
        alt Есть новые задачи
            Sch->>TG: Отправка сообщения: "Новая заявка #ID"
        end

        %% 2. Изменения в существующих задачах
        Sch->>API: get_tasks(ChangedMoreThan = last_check_time, ExecutorIds = user_id)
        API->>IS: HTTP GET /api/task (Basic Auth)
        IS-->>API: Список измененных задач
        API-->>Sch: Данные задач
        
        loop Для каждой измененной задачи
            Sch->>API: get_task_lifetime(task_id)
            API->>IS: HTTP GET /api/tasklifetime
            IS-->>API: Список исторических событий задачи
            API-->>Sch: Список событий
            Note over Sch: Фильтрация событий по времени (> last_check_time)
            alt Найден новый комментарий
                Sch->>TG: Отправка сообщения: "💬 Новый комментарий..."
            else Найдено изменение статуса
                Sch->>TG: Отправка сообщения: "🔄 Статус заявки изменен..."
            end
        end
        
        Sch->>DB: update_user_state(tg_id, last_task_id = max, last_check_time = current_time)
        DB-->>Sch: Сохранено
    end
```
