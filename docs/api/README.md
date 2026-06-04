# Core API Gateway (FastAPI)

Микросервис-шлюз для интеграции с внешним API IntraService, используемый Telegram-ботом и фоновым планировщиком (воркером).

## Базовая информация

- **Базовый URL в локальной среде:** `http://localhost:8000` (или настроенный адрес Core API)
- **Формат данных:** `application/json`

---

## Авторизация (API-ключ)

Большинство запросов к Core API Gateway (кроме системного эндпоинта `/health`) требуют передачи API-ключа бота в HTTP-заголовках запроса.

| Заголовок | Тип | Обязательный | Описание |
| :--- | :---: | :---: | :--- |
| `X-Bot-Api-Key` | String | Да | Секретный ключ авторизации бота, задаваемый в конфигурации (`BOT_API_KEY`). |

**Пример заголовков:**
```http
Content-Type: application/json
X-Bot-Api-Key: your-super-secret-bot-api-key
```

Если ключ не передан или не совпадает с установленным на сервере, API возвращает ошибку `401 Unauthorized`.

---

## Стандартные коды ответов и ошибки

Шлюз использует стандартные HTTP-коды ответов:

- **`200 OK` / `201 Created`** — Запрос успешно обработан.
- **`400 Bad Request`** — Ошибка в параметрах запроса или теле JSON.
- **`401 Unauthorized`** — Неверный API-ключ шлюза (`X-Bot-Api-Key`) или неверные данные Basic Auth для внешнего IntraService API.
- **`404 Not Found`** — Запрашиваемый ресурс (например, пользователь) не найден.
- **`502 Bad Gateway`** — Внешний сервис IntraService недоступен или вернул ошибку.
- **`500 Internal Server Error`** — Внутренняя ошибка сервера Core API Gateway.

---

## Разделы документации

1. 🔐 [Аутентификация](file:///f:/Work/Projects/IntraLink/docs/api/auth.md) — вход (`/login`) и выход (`/logout`).
2. 👤 [Пользователи](file:///f:/Work/Projects/IntraLink/docs/api/users.md) — получение состояния пользователя.
3. 📋 [Задачи и статусы](file:///f:/Work/Projects/IntraLink/docs/api/tasks.md) — список задач, история изменений и справочники.
4. 🛠️ [Системные эндпоинты](file:///f:/Work/Projects/IntraLink/docs/api/system.md) — проверка статуса сервиса `/health`.
5. 📦 [Сущности IntraService](file:///f:/Work/Projects/IntraLink/docs/api/intraservice_entities.md) — описание моделей данных внешней системы.
