# Hybrid Auth: Персональные права оператора в Admin UI

## Проблема

`POST /admin/api/tasks/{task_id}/apply` всегда использует `worker:service_auth_b64` из Redis — это либо сервисный аккаунт воркера (RoleId 55, нет прав на смену статуса), либо учётные данные последнего вошедшего оператора (race condition при нескольких вкладках). Итог: `500` при попытке совершить действие, на которое сервисный аккаунт не имеет прав.

Кроме того, UI показывает одинаковые кнопки всем операторам независимо от `Rights.ToStatuses` конкретной заявки и роли пользователя — действие может быть показано, но упасть на сервере.

---

## Архитектурное решение: Operator Session Auth

Ключевой инсайт: при логине JWT уже содержит `username`. При логине `auth_b64` (Base64 `login:password`) уже вычислен и доступен в `admin_login`. Нужно хранить его **в Redis под ключом, привязанным к сессии**, и передавать на мутирующие эндпоинты **через зависимость**, а не брать из глобального `worker:service_auth_b64`.

```
admin_login  →  encrypt(auth_b64)  →  Redis "admin_auth:{username}"  (TTL 12h)
                JWT{sub: username}  →  HttpOnly Cookie

apply_task_action  →  verify_admin_jwt → username
                   →  Redis.get("admin_auth:{username}")  →  operator auth_b64
                   →  IntraService API (от имени оператора)
```

Воркер по-прежнему использует `worker:service_auth_b64` (сервисный аккаунт) — только для чтения/мониторинга. Мутации (смена статуса, комментарии) — только от имени оператора.

---

## User Review Required

> [!IMPORTANT]
> **Разделение ключей Redis:** `worker:service_auth_b64` остаётся неизменным для воркера.
> Новый ключ `admin_auth:{username}` хранит учётные данные оператора с TTL 12h.
> При логине больше **не перезаписывается** `worker:service_auth_b64`.

> [!WARNING]
> **Двухэтапный переход через статус 27** будет **удалён**. Вместо этого — прямой переход в целевой статус. Статус 27 отображается в UI только если он разрешён в `Rights.ToStatuses` заявки. Если логика вашего процесса требует обязательного прохода через 27 — скажите, добавим опцию.

> [!CAUTION]
> **`executor_ids`** в `apply_task_action` будет браться из `Rights.ExecutorIds` (что вернул IntraService для оператора), а не прокидываться хардкодом. Frontend не должен посылать произвольные `executor_ids` — это поле будет игнорироваться, если не разрешено правами.

---

## Open Questions

- Нужно ли хранить `executor_ids` из `Rights` на фронтенде и автоматически подставлять оператора как исполнителя?
- Должны ли кнопки `«В работе»` (статус 27) показываться явно, или только финальные статусы (29, 30, 48)?

---

## Предлагаемые изменения

### 1. Backend — `deps.py`

#### [MODIFY] [`deps.py`](file:///c:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intraservice-tg-bot/core-api/app/routers/deps.py)

- Добавить зависимость `get_operator_auth_b64(username: str = Depends(verify_admin_jwt)) -> str`  
  Читает `admin_auth:{username}` из Redis, расшифровывает, возвращает plain `auth_b64`.  
  Если ключ не найден → `401` "Сессия истекла, войдите заново".

---

### 2. Backend — `admin.py`

#### [MODIFY] [`admin.py`](file:///c:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intraservice-tg-bot/core-api/app/routers/admin.py)

**`admin_login`** (строки 34-76):
- После получения `auth_b64` — сохранять в `admin_auth:{username}` с TTL 12h.
- **Убрать** перезапись `worker:service_auth_b64`.

**`get_task_details`** (строка 1200):
- Добавить `include=usertaskrights` к запросу `get_single_task`.
- В ответ включить поле `rights` → `{ to_statuses: [...], can_add_comment: bool, can_add_expenses: bool }`.

**`apply_task_action`** (строка 1321):
- Добавить зависимость `get_operator_auth_b64` вместо Redis-чтения `worker:service_auth_b64`.
- **Удалить** двухэтапный переход через статус 27 (шаг 2 в строке 1356-1361).
- Один вызов `update_task_full` с целевым статусом напрямую.
- При ошибке `400` от IntraService — возвращать `403 Forbidden` с человекочитаемым текстом вместо `500`.

**`bulk_apply_tasks`** (строка 1422):
- Аналогично: получать `auth_b64` оператора и передавать в каждый вызов.
- Сигнатуру `apply_task_action` изменить — принять `auth_b64` как явный параметр.

**`get_task_details`** — дополнить ответ:
```python
"rights": {
    "to_statuses": task_data.get("Rights", {}).get("ToStatuses") or [],
    "can_add_comment": True,   # всегда разрешён при наличии сессии
    "can_add_expenses": True,  # аналогично
}
```

---

### 3. Backend — `intraservice.py`

#### [MODIFY] [`intraservice.py`](file:///c:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intraservice-tg-bot/core-api/app/services/intraservice.py)

**`get_single_task`**:
- Добавить параметр `include_rights: bool = False`.
- Если `True` — добавлять `include=usertaskrights` к query string.

**`update_task_full`**:
- При `HTTP 400` от IntraService — логировать тело ответа и бросать `RuntimeError` с деталью из ответа (сейчас просто `return False`), чтобы caller мог вернуть `403`.

---

### 4. Frontend — SPA (`admin` bundle)

> [!NOTE]
> Фронтенд встроен в SPA, который билдится в `core-api/app/static/admin.*`.
> Правки — в исходниках SPA (нужно найти путь к исходникам).

**При открытии карточки заявки:**
- Запрашивать `/admin/api/tasks/{id}/details` (уже включает `rights`).
- Получив `rights.to_statuses`, динамически строить список доступных кнопок действий.
- Если `rights.to_statuses` пуст → показывать только кнопку «Открыть в IntraService».

**Кнопки действий:**
| Условие | Отображение |
|---|---|
| `to_statuses` содержит `27` | Кнопка «В работе» |
| `to_statuses` содержит `29` | Кнопка «Выполнена» |
| `to_statuses` содержит `30` | Кнопка «Отменена» |
| `to_statuses` содержит `48` | Кнопка «Ожидание» |
| Нет разрешённых статусов | Информер «Нет доступных действий» |

**Обработка ошибок `403`:**
- Показывать toast "Нет прав для этого действия" вместо безликого "500 Internal Server Error".

---

## Verification Plan

### Automated Tests
```bash
cd core-api && python -m pytest tests/ -v -k "admin or apply"
```

### Manual Verification
1. Войти под `belikov.a` (RoleId 37) → убедиться, что `admin_auth:belikov.a` появился в Redis.
2. Открыть карточку заявки → проверить, что `rights.to_statuses` содержит реальные статусы для этого оператора.
3. Нажать «Выполнена» → заявка должна перейти в статус 29 без `500`.
4. Войти под аккаунтом с ограниченными правами → убедиться, что недоступные кнопки скрыты.
5. Убедиться, что `worker:service_auth_b64` в Redis **не изменился** после логина оператора.
6. Дождаться TTL 12h → убедиться, что повторный вызов `/apply` возвращает `401`, а не `500`.
