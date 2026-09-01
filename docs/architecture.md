# Архитектура проекта IntraLink (intraservice-tg-bot)

Данный документ описывает архитектуру системы, её ключевые компоненты, потоки данных и принципы взаимодействия между сервисами.

> **Главная цель проекта** — создание единой, надежной платформы автоматизации выполнения заявок в IntraService. Система сочетает детерминированный движок правил (Rule Engine), локальный семантический RAG-поиск (FastEmbed + pgvector) и модули прямого исполнения задач в инфраструктуре (Active Directory, WinRM, SMB).

---

## 🏗️ 1. Общий обзор системы

Проект построен по **модульной архитектуре с единым ядром логики и исполнения**:

| Сервис / Модуль | Технологии | Роль |
|---|---|---|
| **Core API** | FastAPI, SQLAlchemy 2.0, APScheduler, Redis | Шлюз к IntraService API, управление БД, фоновый опрос очереди, хостинг встроенной веб-панели управления (`intra-web`) |
| **Telegram Bot** | aiogram 3.x, aiohttp | Мобильный интерфейс оператора и пользователей, доставка Push-уведомлений |
| **Intra Web (Admin UI)** | React 19, Vite, Tailwind CSS v4 | Встроенная веб-панель мониторинга заявок, очередей и управления доступом |
| **Helpdesk Agent & Execution Hub** | Python, FastEmbed, pgvector, PowerShell | Интерактивный CLI-кокпит оператора в среде Antigravity (AGY), детерминированный триаж, RAG и исполнение команд (AD WLAN, WinRM принтеры) |

Каналы связи:
- **HTTP REST** с заголовком `X-Bot-Api-Key` — для запросов от Telegram-бота к Core API.
- **Redis Pub/Sub** — для асинхронной доставки событий мониторинга от Core API к Telegram-боту.

---

### 1.1. Схема компонентов и слоёв системы

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

    subgraph Clients ["Пользовательские интерфейсы"]
        User(["👤 Пользователь в Telegram"]):::userStyle
        Admin(["👨‍💻 Инженер в Web / AGY IDE"]):::userStyle
    end

    subgraph Interface_Layer ["Слой интерфейсов"]
        TG_API["💬 Telegram Bot API"]:::telegramStyle
        Intra_Web["🖥️ Intra Web (SPA /admin)"]:::serviceStyle
    end

    subgraph Bot_Service ["Telegram Bot Service (Python / aiogram)"]
        direction TB
        B_Main["🚀 main.py"]:::serviceStyle
        B_Handlers["handlers/<br/>(auth, tickets, start)"]:::handlerStyle
        API_Client["api_client.py<br/>(HTTP Core API клиент)"]:::serviceStyle
        Redis_Listener["redis_listener.py<br/>(Redis Pub/Sub Subscriber)"]:::serviceStyle
    end

    subgraph Broker_Layer ["Шина сообщений"]
        Redis_Broker[("Redis Pub/Sub & Cache")]:::redisStyle
    end

    subgraph Core_API_Service ["Core API Service (Python / FastAPI)"]
        direction TB
        API_Main["🚀 main.py (Port 8000)"]:::serviceStyle
        
        subgraph API_Routers ["Роутеры API (Endpoints)"]
            R_Auth["auth.py<br/>(/api/v1/auth)"]:::handlerStyle
            R_Tasks["tasks.py<br/>(/api/v1/tasks)"]:::handlerStyle
            R_Admin["admin.py<br/>(/admin/api, /admin)"]:::handlerStyle
        end

        subgraph API_Services ["Службы & База данных"]
            IS_Client["intraservice.py<br/>(aiohttp клиент)"]:::serviceStyle
            Worker["worker.py<br/>(APScheduler + Redis Publisher)"]:::serviceStyle
            DB_Module["db.py<br/>(SQLAlchemy async)"]:::dbStyle
        end
    end

    subgraph Helpdesk_Agent_Hub ["Helpdesk Agent & Execution Hub (AGY / CLI)"]
        direction TB
        HD_Tool["🚀 helpdesk_tool.py"]:::serviceStyle
        HD_Rules["rules/<br/>(Credentials, Redirect, Duplicates)"]:::handlerStyle
        HD_RAG["kb.py<br/>(FastEmbed + pgvector)"]:::dbStyle
        HD_Diag["diagnostics.py<br/>(Ping, DNS, SMB:445, WinRM)"]:::serviceStyle
        
        subgraph Executors ["⚡ executors/ (Модули исполнения)"]
            EX_AD["ad.py<br/>(WLAN-WORKNET / AD Groups)"]:::serviceStyle
            EX_PRN["printers.py<br/>(WinRM / SMB / WMI Bootstrap)"]:::serviceStyle
        end
    end

    subgraph DB_Layer ["Слой данных"]
        Postgres_DB[("PostgreSQL 16 (App Data & pgvector RAG)")]:::dbStyle
    end

    subgraph External_Systems ["Инфраструктура и внешние системы"]
        IS_API["🌐 IntraService REST API"]:::externalStyle
        AD_Domain["🏢 Active Directory / WinRM (corporate.loc)"]:::externalStyle
    end

    %% Связи
    User <-->|Взаимодействие| TG_API
    TG_API <-->|Polling| B_Main
    B_Main --> B_Handlers
    B_Handlers --> API_Client
    API_Client <-->|REST HTTP| API_Main
    Redis_Broker -->|task_events| Redis_Listener

    Admin <-->|Браузер (:8000/admin)| Intra_Web
    Intra_Web <--> API_Main
    Admin <-->|Команды /triage, /apply, /wlan| HD_Tool

    API_Main --> API_Routers
    API_Routers --> IS_Client & DB_Module
    Worker --> IS_Client & DB_Module & Redis_Broker

    HD_Tool --> HD_Rules & HD_RAG & HD_Diag & Executors
    HD_RAG <--> Postgres_DB
    Executors <-->|AD / WinRM| AD_Domain

    DB_Module <--> Postgres_DB
    IS_Client <--> IS_API
```

---

## 🔒 2. Принципы надежности и исполнения

1. **Защита от слепого закрытия (Verified Execution Only):**
   Статус `29 Выполнена` устанавливается **исключительно** после фактического успешного выполнения действия в инфраструктуре (добавление в группу AD `WLAN-WORKNET`, удаленная установка принтера по WinRM).
2. **Двухэтапный жизненный цикл финализации (`apply` / `wlan`):**
   Перевод в финальные статусы `29 Выполнена` или `30 Отменена` обязательно выполняется через статус `27 В работе` с фиксацией двух исполнителей (`Беликов Ален` - ID `8664` и `Беликов Ален_assitant` - ID `10502`) и списанием трудозатрат.
3. **Single-DC Affinity для Active Directory:**
   Операции записи (`Add-ADGroupMember`) и контрольной проверки (`MemberOf`) привязываются к одному контроллеру домена для исключения сбоев из-за задержек межсайтовой репликации.
