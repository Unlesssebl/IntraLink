# 👤 Пользователи

Роутер: [users.py](file:///f:/Work/Projects/IntraLink/core-api/app/routers/users.py)

Префикс: `/api/v1/users`

Все эндпоинты требуют передачи заголовка `X-Bot-Api-Key`.

---

## 1. Получить профиль пользователя (`GET /{tg_user_id}`)

Возвращает сохраненный профиль пользователя по его Telegram ID и текущие параметры фонового опроса.

### Запрос

- **Метод:** `GET`
- **Путь:** `/api/v1/users/{tg_user_id}`
- **Параметры пути:**

| Параметр | Тип | Обязательный | Описание |
| :--- | :---: | :---: | :--- |
| `tg_user_id` | Integer | Да | ID пользователя в Telegram. |

**Пример запроса:**
`GET /api/v1/users/123456789`

### Ответ

- **Код состояния:** `200 OK`
- **Тело ответа (JSON - UserResponse):**

| Поле | Тип | Описание |
| :--- | :---: | :--- |
| `tg_user_id` | Integer | Telegram ID пользователя. |
| `is_login` | String | Логин пользователя в IntraService. |
| `is_user_id` | Integer \| null | ID пользователя во внешней системе IntraService. |
| `last_task_id` | Integer | ID последней обработанной задачи воркером. |
| `last_comment_id` | Integer | ID последнего обработанного комментария. |
| `last_check_time` | String \| null | Дата и время последней проверки обновлений в формате UTC (в формате `YYYY-MM-DD HH:MM:SS`). |

**Пример JSON-ответа:**
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

- **Код ошибки `404 Not Found`**: Если пользователь не найден в локальной базе данных.
  ```json
  {
    "detail": "Пользователь с Telegram ID 123456789 не найден."
  }
  ```

---

## Важное примечание: управление состоянием опроса

> [!NOTE]
> В отличие от предыдущих архитектурных решений, эндпоинт `PATCH /users/{tg_user_id}/state` **отсутствует** в Core API. 
> 
> Поскольку фоновый воркер опроса событий ([worker.py](file:///f:/Work/Projects/IntraLink/core-api/app/services/worker.py)) выполняется на стороне самого Core API Gateway, обновление полей состояния (`last_task_id`, `last_comment_id`, `last_check_time`) происходит автоматически внутри базы данных через ORM (SQLAlchemy) при каждом цикле опроса. Внешним клиентам (например, Telegram-боту) не требуется вызывать методы для обновления состояния.
