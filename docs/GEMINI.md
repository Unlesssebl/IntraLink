# Инструкции по работе с IntraService API и архитектурой IntraBot

Этот файл является обязательным руководством для любого AI-агента в данном воркспейсе. Он описывает структуру проекта, особенности интеграции с API IntraService и правила работы с кодовой базой.

## 1. Навигация по документации API
- Полная техническая документация по API IntraService (версия 4.47) находится в каталоге `docs/IntraService_API/` и разбита на 77 файлов вида `IntraService_API_v4_47_ЧастьN.md`.
- **ВСЕГДА** начинайте поиск эндпоинтов и методов с индексного файла: [IntraService_API_Index.md](file:///c:/Users/belikov.a/Desktop/%D0%90%D0%BA%D1%82%D1%8B,%20%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D1%8B/Work/%21Projects/intraservice-tg-bot/docs/IntraService_API/IntraService_API_Index.md).
- **СХЕМЫ JSON** запросов и ответов описаны в файле [Schemas.md](file:///c:/Users/belikov.a/Desktop/%D0%90%D0%BA%D1%82%D1%8B,%20%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D1%8B/Work/%21Projects/intraservice-tg-bot/docs/Schemas.md).

## 2. Реализация авторизации (Auth)
- Используется **Basic Authentication**.
- Заголовок запроса: `Authorization: Basic <base64(login:password)>`.
- В базе данных SQLite учетные данные сохраняются в таблице `users` в поле `is_password_b64`. 
- Проверка учетных данных пользователя выполняется через метод `verify_credentials` в [api.py](file:///c:/Users/belikov.a/Desktop/%D0%90%D0%BA%D1%82%D1%8B,%20%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D1%8B/Work/%21Projects/intraservice-tg-bot/services/api.py) путем отправки запроса на эндпоинт `user` с параметром `getcurrentuserinfo=true`. Это позволяет также получить внутренний `user_id` сотрудника в системе IntraService для последующей фильтрации заявок.

## 3. Формат запросов, ответов и работы с датами
- **Base URL:** Значение берется из переменной окружения `INTRAService_URL` (обычно заканчивается на `/api/`).
- **Формат даты в API:** Даты могут приходить в форматах `YYYY-MM-DD HH:MM:SS`, `YYYY-MM-DDTHH:MM:SS` (с символом T), а также `DD.MM.YYYY HH:MM:SS` и `DD.MM.YYYY HH:MM`. 
  - Используйте вспомогательную функцию `parse_api_date(date_str)` из `services/api.py` для приведения дат к объектам `datetime`.
  - При передаче временных фильтров в API (параметры `CreatedMoreThan` или `ChangedMoreThan`) используйте формат `YYYY-MM-DD HH:MM`.
- **ID сущностей:** Передаются и возвращаются как целые числа (`Int`).

## 4. Особенности работы с методами API в проекте
- **Получение списка задач (`/api/task`):**
  - Поддерживает фильтрацию (параметры `ExecutorIds`, `StatusIds`, `CreatedMoreThan`, `ChangedMoreThan`, `page`, `pagesize`).
  - При запросах автоматически добавляется параметр `include=status` для получения названий статусов.
- **История изменений задачи (`/api/tasklifetime`):**
  - Используется для отслеживания комментариев и переходов статусов по конкретной задаче. Принимает параметр `taskid`.
  - Поля `Comments` и `StatusId` в событиях истории парсятся в `services/scheduler.py` для отправки точечных уведомлений пользователям.
- **Справочник статусов (`/api/taskstatus`):**
  - Используется для сопоставления ID статусов с их именами и определения неактивных (закрытых/финальных) заявок при помощи признаков `IsFinal` и `IsFixed`.

## 5. Структура Базы Данных (SQLite)
Таблица `users` ([db.py](file:///c:/Users/belikov.a/Desktop/%D0%90%D0%BA%D1%82%D1%8B,%20%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D1%8B/Work/%21Projects/intraservice-tg-bot/database/db.py)) содержит:
- `tg_user_id` (INTEGER PRIMARY KEY) — ID пользователя в Telegram.
- `is_login` (TEXT) — Логин пользователя.
- `is_password_b64` (TEXT) — Закодированная в Base64 пара `login:password`.
- `is_user_id` (INTEGER) — Внутренний ID пользователя в IntraService.
- `last_task_id` (INTEGER) — ID последней отправленной в Telegram новой заявки.
- `last_comment_id` (INTEGER) — ID последнего отправленного комментария (в текущей логике фильтрация идет по времени).
- `last_check_time` (TEXT) — Время последней проверки в формате `YYYY-MM-DD HH:MM:SS`.

## 6. Рекомендации по разработке и безопасности
1. **Асинхронность:** Все вызовы к БД (`aiosqlite`) и к API (`aiohttp`) должны выполняться асинхронно с ключевым словом `await`.
2. **Безопасность:** Пароли в БД хранятся в Base64. В будущих версиях требуется внедрить симметричное шифрование. Сообщения с паролями от пользователя в Telegram-чате должны удаляться сразу после считывания (уже реализовано в `handlers/auth.py`).
3. **SSL Верификация:** Переменная `SSL_VERIFY = False` в `api.py` отключает проверку сертификатов для совместимости с внутренними доменами. В продакшн-окружении её значение следует вынести в конфигурацию.

