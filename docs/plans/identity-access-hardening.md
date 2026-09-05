# План укрепления идентификации и полномочий IntraLink

Статус: предлагается к реализации  
Контекст: одна организация, PostgreSQL + Redis, Command API v2

## 1. Цель

Каждый запрос должен отвечать на четыре вопроса:

1. Кто инициировал действие: человек или сервис.
2. Каким способом подтверждена личность.
3. Какое точное полномочие разрешило операцию.
4. Кто и когда может отозвать этот доступ.

PostgreSQL хранит пользователей, роли, сервисные identities, сессии, подтверждения и аудит. Redis используется только для коротких кэшей, rate limit и доставки событий.

## 2. Зафиксированные архитектурные решения

### 2.1. Роли людей

| Роль | Назначение |
|---|---|
| `helpdesk_operator` | Работа с заявками, создание команд, подтверждение обычных операций |
| `system_admin` | Управление ролями, сервисами, политиками, killswitch и неопределёнными результатами |
| `security_auditor` | Просмотр команд, подтверждений и security-аудита без права изменений |

`ADMIN_LOGINS` остаётся только одноразовым bootstrap-механизмом для первого администратора. После инициализации права назначаются в PostgreSQL.

### 2.2. Сервисные identities

Каждый компонент получает собственную identity и собственный ключ:

| Principal | Минимальные полномочия |
|---|---|
| `telegram-bot` | Чтение назначенных заявок, передача Telegram approval challenge |
| `helpdesk-cli` | Чтение триажа, создание предложений и команд от имени текущего оператора |
| `intralink-mcp` | Только явно разрешённые MCP-инструменты |
| `windows-worker` | `command:claim:windows`, `command:finish:windows` |
| `backend-worker` | `command:claim:backend`, `command:finish:backend` |
| `poller` | Чтение IntraService и публикация внутренних событий |

Общий `BOT_API_KEY` выводится из эксплуатации. API-ключ хранится на сервере только как Argon2id-хеш, имеет `key_id`, scopes, срок действия и дату отзыва. Для ротации допускаются два активных ключа одного principal на короткий переходный период.

### 2.3. Уровни риска действий

| Уровень | Действия | Правило |
|---|---|---|
| `R0` | `diagnose_host`, `rag_sync` | Может выполняться автоматически по policy |
| `R1` | `install_printer`, `apply_triage` | Явное подтверждение оператора; автор может подтвердить свою команду |
| `R2` | `grant_wlan`, будущие `create_user`, `reset_password` | Подтверждение другого оператора либо `system_admin` |
| `R3` | Изменение policy, ролей, credentials, разрешение `needs_review` | Только `system_admin`, всегда с причиной |

Это сохраняет скорость первой линии для принтеров и заявок, но вводит разделение обязанностей для доступа и учётных записей.

### 2.4. Пользовательские сессии

- Корпоративная проверка учётных данных выполняется только при входе.
- Access JWT живёт 15 минут.
- Refresh-сессия живёт 8 часов, хранится сервером и отзывается немедленно.
- JWT содержит `sub`, `user_id`, `roles`, `session_id`, `jti`, `iss`, `aud`, `iat`, `nbf`, `exp`.
- Сервер проверяет алгоритм, issuer, audience, срок и активность сессии.
- Web использует `HttpOnly`, `Secure`, `SameSite=Strict` cookie. CLI получает короткий access token через интерактивный вход.
- Токены удаляются из query-параметров и логов. SSE переводится на `fetch` streaming с cookie или Authorization header.
- Корпоративный пароль не сохраняется. Если IntraService требует делегированный Basic-токен, он хранится отдельно в зашифрованном credential vault с TTL не длиннее refresh-сессии.

### 2.5. Telegram-подтверждения

Передаваемый ботом `tg_user_id` не считается доказательством личности сам по себе.

1. Оператор связывает Telegram с корпоративной учётной записью одноразовым кодом из Web UI.
2. Для ожидающей команды Core API создаёт случайный approval challenge со сроком 10 минут.
3. В PostgreSQL хранится только SHA-256 challenge, привязанный к `command_id`, `request_hash`, `tg_user_id` и допустимому решению.
4. Telegram получает непрозрачный одноразовый challenge и показывает параметры команды.
5. Бот передаёт challenge с собственной service identity.
6. Core API атомарно проверяет service scope, связь Telegram, роль, срок, request hash и отсутствие предыдущего использования.
7. Challenge помечается использованным в одной транзакции с `command_approval`.

Компрометация ключа бота тогда не позволяет подтверждать произвольные команды без активного challenge.

## 3. Целевая модель данных

Добавить Alembic-миграцией:

- `principals`: единая identity человека или сервиса, `type`, `status`, `display_name`;
- `roles`, `permissions`, `role_permissions`, `principal_roles`;
- `service_credentials`: `principal_id`, `key_id`, `secret_hash`, scopes, `expires_at`, `revoked_at`, `last_used_at`;
- `auth_sessions`: `session_id`, `principal_id`, refresh hash, сроки, IP/user-agent fingerprint, `revoked_at`;
- `telegram_links`: `principal_id`, `tg_user_id`, `verified_at`, `revoked_at`;
- `approval_challenges`: command/request binding, hash, адресат, срок и `used_at`;
- `security_events`: append-only журнал входов, отказов, выдачи ролей, ротации ключей и решений.

Текущую таблицу `users` разделить: данные Telegram переносятся в `telegram_links`, временные делегированные credentials — в vault. `is_active` становится реальным состоянием principal, а не вычисляемым ответом API.

## 4. Единый слой авторизации

Вместо `verify_admin_jwt`, `verify_api_key` и `verify_admin_or_api_key` ввести:

- `authenticate_request() -> PrincipalContext`;
- `require_permission("command:create")`;
- `require_service_scope("command:claim:windows")`;
- `authorize_command_transition(context, command, transition)` для object-level правил и R2 separation of duties.

`PrincipalContext` содержит неизменяемые `principal_id`, `principal_type`, `roles`, `scopes`, `session_id` и `auth_method`. Имя инициатора больше не принимается из JSON клиента.

## 5. Матрица ключевых разрешений

| Операция | Operator | Admin | Auditor | Bot | Worker |
|---|---:|---:|---:|---:|---:|
| Просмотр заявок и своих команд | ✓ | ✓ | ✓ | ограниченно | — |
| Создание команды | ✓ | ✓ | — | ограниченно | — |
| Подтверждение R1 | ✓ | ✓ | — | challenge relay | — |
| Подтверждение R2 | другой оператор | ✓ | — | challenge relay | — |
| Изменение policy/ролей/ключей | — | ✓ | — | — | — |
| Разрешение `needs_review` | — | ✓ | — | — | — |
| Claim/finish | — | — | — | — | только свой executor |
| Просмотр security audit | свои события | ✓ | ✓ | — | — |

## 6. Этапы реализации

### Этап 0. Контракт и защитные тесты

- Зафиксировать полный список маршрутов и требуемые permissions.
- Добавить deny-by-default тест: каждый изменяющий маршрут обязан иметь permission dependency.
- Добавить тесты запрета подмены `initiator`, Telegram ID и worker executor.

Критерий: CI падает при появлении незащищённого mutation endpoint.

### Этап 1. Схема identities и RBAC

- Добавить целевые таблицы и seed фиксированного permission catalog.
- Перенести `ADMIN_LOGINS` в bootstrap-команду `create-initial-admin`.
- Мигрировать существующих Telegram-пользователей в principals и links со статусом `pending_reverification`.

Критерий: права читаются только из PostgreSQL; Redis недоступен — проверка прав продолжает работать.

### Этап 2. Единые JWT-сессии

- Объединить два текущих admin login flow.
- Добавить короткий access JWT, refresh rotation и немедленный revoke.
- Перевести Web и CLI на новый login/refresh/logout.
- Удалить JWT из query string, закрыть старые токены после переходного окна.

Критерий: отозванная сессия перестаёт работать сразу; смена роли применяется без ожидания истечения access JWT.

### Этап 3. Service principals и ключи

- Выдать отдельные identities Telegram, CLI/MCP, poller и каждому типу worker.
- Перевести API на `X-Service-Key-Id` + `X-Service-Secret` либо стандартный Authorization scheme.
- Ограничить worker по executor: Windows worker не может claim backend-команду и наоборот.
- Добавить ротацию, revoke и `last_used_at`.

Критерий: утечка одного ключа не предоставляет полномочия другого сервиса.

### Этап 4. Permission migration маршрутов

- Сначала мигрировать Command API v2, policy, credentials и admin users.
- Затем triage mutations, rules admin, diagnostics, events и read endpoints.
- Старый `verify_admin_or_api_key` оставить временным адаптером только на перечисленных read-only маршрутах.
- Любой неизвестный principal, scope или permission блокируется.

Критерий: матрица разрешений покрыта интеграционными тестами 401/403/200.

### Этап 5. Telegram linking и approval challenge

- Реализовать Web-код привязки Telegram.
- Добавить challenge generation/consumption и транзакционную защиту от повторного использования.
- Удалить `/approval/telegram` с доверием к обычному `tg_user_id`.
- В сообщении показывать action, цель, request hash fragment, автора и срок.

Критерий: подмена Telegram ID, повтор callback, просроченный challenge и изменённая команда получают отказ.

### Этап 6. Аудит и наблюдаемость

- Писать security events для login success/failure, revoke, role/key changes, 403, approvals и review resolution.
- Не записывать JWT, API secrets, passwords и полный Basic auth.
- Метрики: auth failures, revoked-session use, denied permissions, expired/replayed challenges, key age.
- Алерты: серия отказов, использование отозванного ключа, массовая смена ролей, R2 approvals одним участником.

Критерий: по `command_id` восстанавливается полная цепочка identities и решений без секретов в логах.

### Этап 7. Rollout и удаление наследия

1. Развернуть новые таблицы и dual-read identities.
2. Создать первого администратора и сервисные principals.
3. Перевести Web, workers, Telegram, CLI и MCP по одному.
4. Включить deny-by-default и наблюдать 403/401 в течение переходного окна.
5. Отозвать `BOT_API_KEY`, старый `WORKER_API_KEY`, старые JWT и убрать `ADMIN_LOGINS` из runtime.
6. Удалить старые auth dependencies и Telegram credential storage.

Откат выполняется только до пункта 5 через feature flag на чтение старой identity. После отзыва общих ключей возврат требует явной повторной выдачи credentials.

## 7. Последовательность коммитов

1. `test(auth): add endpoint permission inventory and deny-by-default checks`
2. `feat(auth): add principals roles permissions and sessions schema`
3. `feat(auth): issue short-lived JWT and rotating refresh sessions`
4. `feat(auth): add scoped service principals and key rotation`
5. `refactor(auth): migrate command and administration permissions`
6. `feat(telegram): add linked identity approval challenges`
7. `refactor(auth): migrate remaining routes and remove shared keys`
8. `feat(audit): add security events metrics and alerts`

Каждый коммит должен проходить существующий полный набор тестов и новые RBAC security tests. Миграции только добавляющие до финального удаления наследия.

## 8. Готовность этапа

Работа завершена, когда:

- нет runtime-авторизации через `ADMIN_LOGINS` и общий `BOT_API_KEY`;
- каждый mutation endpoint требует конкретное permission;
- каждая команда содержит стабильные `initiator_principal_id` и `approver_principal_id`;
- R2 запрещает самоподтверждение;
- Telegram использует одноразовый challenge;
- worker ограничен своим executor и не может управлять policy;
- отзыв пользователя, сессии или service key действует немедленно;
- security audit отвечает на вопрос «кто разрешил действие» без обращения к Redis.
