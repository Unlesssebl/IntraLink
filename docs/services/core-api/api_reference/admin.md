# Панель администратора (Admin UI & API)

Раздел API-шлюза для обслуживания легковесного веб-интерфейса администратора, мониторинга статуса воркеров, удаленного запуска и ручного управления задачами установки принтеров.

## Механизм авторизации

Доступ к API веб-панели администратора (`/admin/api/...`) защищен сессионной кукой `admin_session`, которая содержит подписанный JWT-токен. 

1. Авторизация происходит посредством проверки введенных логина/пароля во внешней системе IntraService.
2. При успешной проверке сервер генерирует JWT-токен (срок действия — 12 часов) и отправляет его в HTTP-ответе в заголовке `Set-Cookie` с флагами `HttpOnly` и `SameSite=Lax`.
3. Браузер автоматически прикрепляет эту куку ко всем последующим запросам к API панели, включая соединения Server-Sent Events (SSE).

---

## 1. Эндпоинты авторизации панели

### 1.1. Вход в систему (Login)
Проверяет реквизиты администратора в IntraService и устанавливает JWT-сессию.

- **URL:** `/admin/api/login`
- **Метод:** `POST`
- **Тело запроса (JSON):**
  ```json
  {
    "username": "admin_login",
    "password": "admin_password"
  }
  ```
- **Ответ (200 OK):**
  ```json
  {
    "status": "success",
    "username": "admin_login"
  }
  ```
  *(В заголовках ответа передается `Set-Cookie: admin_session=<token>; HttpOnly; Path=/`)*
- **Ответ (401 Unauthorized):**
  ```json
  {
    "detail": "Неверный логин или пароль"
  }
  ```

### 1.2. Выход из системы (Logout)
Удаляет сессионную куку администратора.

- **URL:** `/admin/api/logout`
- **Метод:** `POST`
- **Ответ (200 OK):**
  ```json
  {
    "status": "success"
  }
  ```

### 1.3. Получение информации о сессии (Me)
Возвращает имя пользователя текущей активной сессии.

- **URL:** `/admin/api/me`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "username": "admin_login"
  }
  ```
- **Ответ (401 Unauthorized):**
  ```json
  {
    "detail": "Недействительный или истекший токен сессии"
  }
  ```

---

## 2. Интерфейс панели

### 2.1. Получить HTML интерфейс панели
Отдает готовую статическую HTML страницу панели (SPA).

- **URL:** `/admin`
- **Метод:** `GET`
- **Авторизация:** Не требуется для загрузки самой страницы (фронтенд самостоятельно проверяет статус авторизации через `/admin/api/me` и перенаправляет на форму входа при необходимости).
- **Ответ:** `text/html` с кодом страницы.

---

## 3. Управляющие API Эндпоинты (требуют активной JWT-сессии)

### 3.1. Статус воркера
Возвращает текущий статус микросервиса `printer-worker` на основе heartbeat в Redis.

- **URL:** `/admin/api/worker-status`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "status": "online" // или "offline"
  }
  ```

### 3.2. База знаний принтеров
Возвращает список поддерживаемых моделей принтеров и их конфигурационные параметры.

- **URL:** `/admin/api/knowledge-base`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "printer_name_prefixes": ["ittp"],
    "printers": [
      {
        "model_key": "kyocera_ecosys_m2040dn",
        "display_name": "Kyocera ECOSYS M2040dn",
        "driver_name": "Kyocera ECOSYS M2040dn KX",
        "driver_inf_path": "\\\\server\\share\\Kyocera_KX\\OEMSETUP.INF",
        "vendor": "Kyocera",
        "supported_hw_ids": ["USBPRINT\\KyoceraECOSYS_M2040d5885"],
        "connection_type": "tcpip"
      }
    ]
  }
  ```

### 3.3. Список заданий установки
Возвращает последние 50 заданий по установке принтеров, сохраненные в Redis.

- **URL:** `/admin/api/print-jobs`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  [
    {
      "task_id": 12345,
      "tg_user_id": 0,
      "raw_text": "Ручная установка: kyocera_ecosys_m2040dn на ПК WS-102",
      "state": "done", // pending, probing, copying, installing, done, failed, etc.
      "target_pc": "WS-102",
      "model_key": "kyocera_ecosys_m2040dn",
      "connection_type": "tcpip",
      "printer_address": "192.168.1.100",
      "is_manual": true,
      "error_message": null
    }
  ]
  ```

### 3.4. Ручной запуск установки
Инициирует процесс установки принтера на ПК, отправляя событие `manual_trigger` в Redis Pub/Sub канал `printer_actions`.

- **URL:** `/admin/api/print-jobs`
- **Метод:** `POST`
- **Тело запроса (JSON):**
  ```json
  {
    "target_pc": "WS-102",
    "model_key": "kyocera_ecosys_m2040dn",
    "connection_type": "tcpip",
    "printer_address": "192.168.1.100" // Обязательно только для типа "tcpip"
  }
  ```
- **Ответ (200 OK):**
  ```json
  {
    "status": "success",
    "task_id": 58210349 // Сгенерированный уникальный ID задачи
  }
  ```

### 3.5. Действие над заданием установки (Approve/Reject/Ask)
Отправляет решение администратора (одобрить, отклонить, запросить уточнение) по заблокированному на этапе подтверждения заданию.

- **URL:** `/admin/api/print-jobs/{task_id}/action`
- **Метод:** `POST`
- **Тело запроса (JSON):**
  ```json
  {
    "action": "approve" // или "reject", "ask_user"
  }
  ```
- **Ответ (200 OK):**
  ```json
  {
    "status": "success",
    "task_id": 12345
  }
  ```

### 3.6. Поток логов установки (SSE)
Текстовое SSE-соединение (Server-Sent Events) для прослушивания логов задачи установки в реальном времени. При подключении выгружает последние 100 строк истории из Redis, после чего транслирует новые логи из канала `printer_job_logs:{job_id}`.

- **URL:** `/admin/api/print-jobs/{job_id}/logs`
- **Метод:** `GET`
- **Заголовки ответа:** `Content-Type: text/event-stream`
- **Авторизация:** Автоматически через сессионную куку `admin_session`.
- **Пример потока событий:**
  ```text
  event: message
  data: [SYSTEM] Подключение к потоку логов... ожидание вывода воркера.

  event: message
  data: 2026-06-10 13:30:15 - INFO - orchestrator - Инициализация удаленного подключения к WS-102...
  ```

### 3.7. Удаление задания установки
Полностью удаляет задачу, историю её логов и метаданные из Redis.

- **URL:** `/admin/api/print-jobs/{task_id}`
- **Метод:** `DELETE`
- **Ответ (200 OK):**
  ```json
  {
    "status": "success",
    "task_id": 12345
  }
  ```

### 3.8. Получение статуса доменных учетных данных
Возвращает информацию о том, настроена ли общая доменная учетная запись (WinRM/SMB) в Redis, и возвращает логин пользователя. Пароль не возвращается в целях безопасности.

- **URL:** `/admin/api/domain-auth`
- **Метод:** `GET`
- **Ответ (200 OK):**
  - Если настроена:
    ```json
    {
      "is_configured": true,
      "username": "DOMAIN\\Administrator"
    }
    ```
  - Если не настроена:
    ```json
    {
      "is_configured": false,
      "username": null
    }
    ```

### 3.9. Настройка доменных учетных данных
Сохраняет доменную учетную запись для WinRM/SMB-подключений в Redis в зашифрованном виде.

- **URL:** `/admin/api/domain-auth`
- **Метод:** `POST`
- **Тело запроса (JSON):**
  ```json
  {
    "username": "DOMAIN\\Administrator",
    "password": "SecurePassword123"
  }
  ```
- **Ответ (200 OK):**
  ```json
  {
    "status": "success"
  }
  ```

---

## 4. Управление AI-воркером (требуют активной JWT-сессии)

### 4.1. Статус и настройки AI
Возвращает текущие метрики работы AI и конфигурацию из Redis.

- **URL:** `/admin/api/ai-worker/status`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "metrics": {
      "classifications": 12,
      "redirected": 3,
      "replied": 5,
      "total": 8,
      "last_reply_task_id": "12345"
    },
    "config": {
      "auto_reply_service_ids": [12, 15],
      "auto_reply_mode": "comment_and_wait",
      "printer_service_ids": [20, 21]
    },
    "rag_running": false
  }
  ```

### 4.2. Обновление настроек AI
Сохраняет новые настройки AI-воркера в Redis.

- **URL:** `/admin/api/ai-worker/config`
- **Метод:** `POST`
- **Тело запроса (JSON):**
  ```json
  {
    "auto_reply_service_ids": [12, 15],
    "auto_reply_mode": "comment_and_wait", // comment_only | comment_and_wait | comment_and_resolve
    "printer_service_ids": [20, 21]
  }
  ```
- **Ответ (200 OK):**
  ```json
  {
    "status": "success"
  }
  ```

### 4.3. Тестирование автоответа
Генерирует тестовый автоответ для конкретной задачи без реальной отправки в IntraService.

- **URL:** `/admin/api/ai-worker/test-reply/{task_id}`
- **Метод:** `POST`
- **Ответ (200 OK):**
  ```json
  {
    "task_id": 12345,
    "task_name": "Не работает принтер",
    "task_description": "Не печатает Kyocera ECOSYS M2040dn на ПК itt1024",
    "service_name": "Подключение принтера",
    "service_id": 20,
    "generated_reply": "Здравствуйте! Ваша заявка принята в работу...",
    "confidence": 0.95,
    "can_resolve": false,
    "needs_clarification": false,
    "reason": "Заявка по принтеру Kyocera на ПК itt1024."
  }
  ```

### 4.4. Запуск перестроения базы RAG
Запускает асинхронный процесс извлечения закрытых задач и построения векторных эмбеддингов для RAG.

- **URL:** `/admin/api/ai-worker/rag/build`
- **Метод:** `POST`
- **Параметры запроса:** `limit` (int, по умолчанию 50) — лимит задач для обработки.
- **Ответ (200 OK):**
  ```json
  {
    "status": "success",
    "message": "Процесс перестроения базы RAG запущен в фоновом режиме."
  }
  ```

### 4.5. Поток логов сборщика RAG (SSE)
SSE эндпоинт для прослушивания логов фонового процесса сборщика RAG в реальном времени.

- **URL:** `/admin/api/ai-worker/rag/logs`
- **Метод:** `GET`
- **Заголовки ответа:** `Content-Type: text/event-stream`
- **Пример ответа:**
  ```text
  event: message
  data: [SYSTEM] Подключение к потоку логов RAG...

  event: message
  data: [INFO] Начинаем сборку RAG датасета (лимит: 50 заявок)...
  ```

