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

### 3.10. Быстрая переиндексация драйверов
Инициирует процесс быстрой переиндексации (только чтение папки `extracted-drv-inf`), отправляя событие `fast_reindex` в Redis Pub/Sub канал `printer_actions`.

- **URL:** `/admin/api/printers/fast-reindex`
- **Метод:** `POST`
- **Ответ (200 OK):**
  ```json
  {
    "status": "success",
    "message": "Быстрая переиндексация запущена в фоновом режиме."
  }
  ```

### 3.11. Статус индексации драйверов
Возвращает текущий статус фонового процесса индексации драйверов и результаты последнего запуска.

- **URL:** `/admin/api/printers/index-status`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "is_running": false,
    "last_run": 1718100000.0,
    "last_result": {
      "status": "ok",
      "mode": "fast",
      "indexed": 42,
      "duration_sec": 1
    }
  }
  ```

### 3.12. Перезапуск задачи установки
Повторно запускает существующую задачу из истории, отправляя её на повторную установку. Опционально позволяет переопределить модель принтера (`model_key`).

- **URL:** `/admin/api/print-jobs/{task_id}/restart`
- **Метод:** `POST`
- **Параметры запроса:**
  * `model_key` (string, необязательный) — новый ключ модели для перезапуска задачи с другим драйвером.
- **Ответ (200 OK):**
  ```json
  {
    "status": "success",
    "task_id": 12345
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
Запускает фоновый процесс извлечения закрытых задач и построения векторных эмбеддингов RAG для классификатора и автоответов.

- **URL:** `/admin/api/ai-worker/rag/build`
- **Метод:** `POST`
- **Тело запроса (JSON):**
  ```json
  [12, 15]
  ```
  *(Массив ID услуг, для которых необходимо перестроить базу. Если передано `null` или тело отсутствует, запускается глобальный сбор по всем услугам из каталога `worker:service_catalog`, чтобы RAG-база содержала информацию для корректной работы AIClassifier по всем разделам).*
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
- **Особенности работы и жизненный цикл соединения:**
  1. При подключении отдаёт накопленную историю логов из Redis-списка `rag_build_logs_history`.
  2. Если в истории обнаружен финальный маркер `[SYSTEM] Процесс перестроения базы RAG завершен.`, клиенту отправляется типизированное событие `event: done` с телом `finished` и соединение закрывается.
  3. Если в Redis флаг `rag_build:running` не установлен в `"true"`, соединение также немедленно завершается событием `event: done`, предотвращая зависание UI.
  4. В режиме реального времени слушает Pub/Sub канал `rag_build_logs`. При получении финального маркера отправляется событие `event: done` и соединение закрывается.
  5. Корректно отслеживает отключение клиента (событие закрытия вкладки / прерывания запроса) и освобождает ресурсы подписки Redis.
- **Пример ответа:**
  ```text
  event: message
  data: [SYSTEM] Подключение к потоку логов RAG...

  event: message
  data: 2026-06-19 06:07:37 - INFO - services.rag_builder - Запуск процесса перестроения RAG-базы.

  event: message
  data: [SYSTEM] Процесс перестроения базы RAG завершен.

  event: done
  data: finished
  ```

### 4.6. Получение списка примеров RAG
Возвращает список примеров из базы знаний (без учета черного списка). Поддерживает пагинацию и фильтрацию по разделу.

- **URL:** `/admin/api/ai-worker/rag/examples`
- **Метод:** `GET`
- **Параметры запроса:**
  * `page` (int, по умолчанию 1) — номер страницы.
  * `limit` (int, по умолчанию 20) — количество примеров на странице.
  * `service_id` (int, необязательный) — ID услуги для фильтрации.
- **Ответ (200 OK):**
  ```json
  {
    "total": 45,
    "page": 1,
    "limit": 10,
    "examples": [
      {
        "task_id": 133609,
        "original_name": "Создание пользователя",
        "problem": "Необходимо создать пользователя сети...",
        "solution": "Пользователь создан. Данные для входа отправлены...",
        "service_id": 53,
        "service_name": "Создание нового пользователя сети",
        "status_name": "Закрыта"
      }
    ]
  }
  ```

### 4.7. Удаление примера RAG
Помечает задачу как удаленную (вносит в черный список RAG) и очищает сохраненные тексты и эмбеддинг, чтобы предотвратить повторный сбор.

- **URL:** `/admin/api/ai-worker/rag/examples/{task_id}`
- **Метод:** `DELETE`
- **Ответ (200 OK):**
  ```json
  {
    "status": "success",
    "task_id": 133609
  }
  ```

### 4.8. Статистика базы знаний RAG
Возвращает статистику собранных знаний, сгруппированных по ID услуги и названию статуса.

- **URL:** `/admin/api/ai-worker/rag/stats`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "53": {
      "total": 4,
      "Закрыта": 4,
      "Отменена": 0
    }
  }
  ```

### 4.9. Получение настроек квот RAG
Возвращает текущие квоты сбора по умолчанию и индивидуальные квоты для услуг.

- **URL:** `/admin/api/ai-worker/rag/quotas`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "filter_id": 0,
    "global_quotas": {
      "28": 10,
      "30": 5
    },
    "service_quotas": {
      "53": {
        "28": 10,
        "30": 5
      }
    }
  }
  ```

### 4.10. Обновление настроек квот RAG
Обновляет настройки квот и ID фильтра 1-й линии для опрашивающего RAG-воркера.

- **URL:** `/admin/api/ai-worker/rag/quotas`
- **Метод:** `POST`
- **Тело запроса (JSON):**
  ```json
  {
    "filter_id": 0,
    "global_quotas": {
      "28": 10,
      "30": 5
    },
    "service_quotas": {
      "53": {
        "28": 10,
        "30": 5
      }
    }
  }
  ```
- **Ответ (200 OK):**
  ```json
  {
    "status": "success"
  }
  ```

---

## 5. Оперативная очередь 1-й линии и интерактивный триаж (Helpdesk Queue API)

### 5.1. Каталог корпоративных шаблонов ответов
Возвращает список шаблонов ответов заявителю из `templates.json` со статусами и трудозатратами.

- **URL:** `/admin/api/templates`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "templates": [
      {
        "key": "wifi_access",
        "name": "Предоставление Wi-Fi (WLAN-WORKNET)",
        "status_id": 29,
        "status_name": "Выполнена (29)",
        "expenses": 10,
        "template": "Доступ к беспроводной корпоративной сети...",
        "badge_color": "success"
      }
    ],
    "map": { ... }
  }
  ```

### 5.2. Сетевая экспресс-диагностика хоста
Выполняет быстрый ICMP-пинг и проверку портов SMB (445) и WinRM (5985) с 60-секундным кэшированием.

- **URL:** `/admin/api/diag/{host}`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "host": "WS-302",
    "is_online": true,
    "avg_rtt": "2ms",
    "smb_ok": true,
    "winrm_ok": true,
    "status_label": "🟢 2ms"
  }
  ```

### 5.3. Детальная информация по заявке (Контекст шторки)
Возвращает тему, описание, вложения (скриншоты) и историю комментариев.

- **URL:** `/admin/api/tasks/{task_id}/details`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "id": 98412,
    "name": "Доступ к Wi-Fi",
    "description": "Прошу предоставить доступ...",
    "creator": "Иванов И.И.",
    "pc_name": "WS-302",
    "phone": "12-34",
    "room": "302",
    "comments": [ ... ],
    "attachments": [ ... ],
    "cls_info": { ... }
  }
  ```

### 5.4. Пакетное применение действий (Bulk Apply)
Применяет финализацию сразу к нескольким выбранным заявкам в очереди.

- **URL:** `/admin/api/tasks/bulk-apply`
- **Метод:** `POST`
- **Тело запроса (JSON):**
  ```json
  {
    "tasks": [
      {
        "task_id": 98412,
        "status_id": 29,
        "comment": "Доступ к Wi-Fi предоставлен",
        "minutes": 10,
        "executor_ids": "8664,10502",
        "is_private": false
      }
    ]
  }
  ```
- **Ответ (200 OK):**
  ```json
  {
    "total": 1,
    "success_count": 1,
    "failed_count": 0,
    "applied": [ ... ],
    "failed": [ ]
  }
  ```



