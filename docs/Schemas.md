# Схемы данных (IntraService API и Core API Gateway)

Этот документ описывает структуры данных (схемы JSON), используемые во внешнем API IntraService, а также контракты запросов и ответов внутреннего шлюза **Core API Gateway**.

---

## 1. Схемы данных Core API Gateway (FastAPI)

Все запросы к Core API (за исключением `/health`) требуют передачи API-ключа бота в заголовке:
`X-Bot-Api-Key: <secret_key>`

### 1.1. Авторизация (`POST /api/v1/auth/login`)
Используется для проверки учетных данных пользователя в IntraService и их сохранения в локальной БД.

**Запрос (LoginRequest):**
```json
{
  "tg_user_id": 123456789,
  "login": "IntraTest",
  "password": "your_password_here"
}
```

**Ответ (LoginResponse):**
```json
{
  "status": "success",
  "message": "Авторизация успешно пройдена и сохранена.",
  "is_user_id": 9891
}
```

### 1.2. Профиль пользователя (`GET /api/v1/users/{tg_user_id}`)
Возвращает профиль пользователя и текущее состояние его фонового опроса.

**Ответ (UserResponse):**
```json
{
  "tg_user_id": 123456789,
  "is_login": "IntraTest",
  "is_user_id": 9891,
  "last_task_id": 1054,
  "last_comment_id": 0,
  "last_check_time": "2026-06-04 15:40:00"
}
```

### 1.3. Обновление состояния опроса (`PATCH /api/v1/users/{tg_user_id}/state`)
Используется планировщиком бота для обновления параметров последней проверки. Все поля в запросе являются опциональными.

**Запрос (UserStateUpdate):**
```json
{
  "last_task_id": 1055,
  "last_comment_id": 12,
  "last_check_time": "2026-06-04 15:45:00"
}
```

**Ответ (UserResponse):**
```json
{
  "tg_user_id": 123456789,
  "is_login": "IntraTest",
  "is_user_id": 9891,
  "last_task_id": 1055,
  "last_comment_id": 12,
  "last_check_time": "2026-06-04 15:45:00"
}
```

---

## 2. Схемы данных IntraService API (REST API)

Ниже описаны структуры сущностей IntraService API, которые проксируются через эндпоинты `/tasks` и `/statuses` микросервиса Core API.

### 2.1. Создание заявки (`POST /api/task`)
Минимальный набор полей для создания тикета:

| Поле | Тип | Обяз. | Описание |
| :--- | :--- | :---: | :--- |
| `Name` | String | + | Тема заявки |
| `Description` | String | | Описание (тело) заявки |
| `ServiceId` | Int | + | ID сервиса |
| `StatusId` | Int | + | ID начального статуса |
| `PriorityId` | Int | | ID приоритета |
| `CreatorId` | Int | | ID заявителя |
| `ExecutorIds` | Int[] | | Список ID исполнителей |

**Пример JSON:**
```json
{
  "Name": "Проблема с доступом",
  "Description": "Не могу зайти в почту",
  "ServiceId": 10,
  "StatusId": 1
}
```

### 2.2. Поля ответа задачи (`GET /api/task/{id}`)
Основные поля, используемые в логике бота и планировщика:
- `Id` (Int) — Уникальный номер заявки.
- `Name` (String) — Тема.
- `Created` (DateTime) — Дата создания.
- `StatusName` / `StatusId` (String / Int) — Информация о статусе задачи.
- `ServiceName` / `ServiceId` (String / Int) — Информация о сервисе.
- `CreatorName` (String) — Имя заявителя.
- `Description` (String) — Полное описание заявки.
- `ExecutorIds` (String) — Строка с ID исполнителей, разделенная запятыми.

### 2.3. История задачи (`GET /api/tasklifetime`)
Используется для отслеживания комментариев и изменений статусов:
- `Date` (String) — Дата события в формате IntraService.
- `Editor` (String) — Имя изменившего задачу.
- `Comments` (String) — Добавленный комментарий.
- `StatusId` (Int) — Новый статус задачи (если менялся).
