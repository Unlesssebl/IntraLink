# Панель администратора (Admin UI & API)

Раздел API-шлюза для обслуживания легковесного веб-интерфейса администратора и удаленного запуска задач принтеров.

## Эндпоинты панели

### 1. Получить HTML интерфейс панели
Отдает готовую статическую HTML страницу панели (SPA).

- **URL:** `/admin`
- **Метод:** `GET`
- **Авторизация:** Не требуется (ключ вводится пользователем в браузере и используется для последующих API-запросов).
- **Ответ:** `text/html` с кодом страницы.

---

## API Эндпоинты (требуют авторизации)

Все нижеперечисленные методы требуют авторизации по API-ключу `BOT_API_KEY`.
Ключ можно передать двумя способами:
1. Заголовок `X-Bot-Api-Key: <key>` (Рекомендуется для REST API)
2. Query-параметр `?api_key=<key>` (Используется для SSE-соединений, где браузер не поддерживает заголовки)

---

### 2. Статус воркера
Возвращает текущий статус микросервиса `printer-worker`.

- **URL:** `/admin/api/worker-status`
- **Метод:** `GET`
- **Ответ (200 OK):**
  ```json
  {
    "status": "online" // или "offline"
  }
  ```

---

### 3. База знаний принтеров
Возвращает список поддерживаемых моделей принтеров и их драйверов.

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

---

### 4. Список заданий установки
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

---

### 5. Ручной запуск установки
Инициирует процесс установки принтера на ПК, отправляя событие `manual_trigger` в Redis Pub/Sub.

- **URL:** `/admin/api/print-jobs`
- **Метод:** `POST`
- **Тело запроса:**
  ```json
  {
    "target_pc": "WS-102",
    "model_key": "kyocera_ecosys_m2040dn",
    "connection_type": "tcpip",
    "printer_address": "192.168.1.100" // Обязательно только для tcpip
  }
  ```
- **Ответ (200 OK):**
  ```json
  {
    "status": "success",
    "task_id": 58210349 // Сгенерированный уникальный ID задачи
  }
  ```

---

### 6. Поток логов установки (SSE)
Нативное текстовое SSE-соединение (Server-Sent Events) для прослушивания логов задачи установки принтера в реальном времени.

- **URL:** `/admin/api/print-jobs/{job_id}/logs`
- **Метод:** `GET`
- **Заголовки ответа:** `Content-Type: text/event-stream`
- **Параметр авторизации (Query):** `?api_key=<your-api-key>`
- **Пример потока событий:**
  ```text
  event: message
  data: [SYSTEM] Подключение к потоку логов... ожидание вывода воркера.

  event: message
  data: 2026-06-10 13:30:15 - INFO - orchestrator - Инициализация удаленного подключения к WS-102 (WMI Bootstrap)...

  event: message
  data: 2026-06-10 13:30:18 - INFO - executors - WinRM успешно настроен и запущен на WS-102.
  ```
