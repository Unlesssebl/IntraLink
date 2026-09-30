# 📋 Микроплан Milestone 2: Ядро генерации отчётов и DLP-санитизация (AutopilotReporter)

## 🎯 Цель этапа
Разработать изолированный, строго протестированный сервис `AutopilotReporter` ([`core-api/app/services/autopilot_reporter.py`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/core-api/app/services/autopilot_reporter.py)), формирующий скрытые комментарии (`IsPrivateComment = true`) для трёх ключевых событий жизненного цикла тикета в двух режимах детализации (`applied` и `technical`), с гарантированной DLP-санитизацией секретов (`***REDACTED***`) и защитой от переполнения длины сообщения.

---

## 🧱 Задачи Milestone 2

### 1. DLP-санитизация (`sanitize_for_internal_comment`)
* **Требования к очистке данных:**
  - Рекурсивный обход любых структур данных (dict, list, tuple, primitive).
  - Маскирование по именам ключей (case-insensitive):
    `password`, `passwd`, `secret`, `token`, `authorization`, `credential`, `api_key`, `auth_b64`, `private_key`, `access_token`, `refresh_token`.
  - Маскирование по строковым регулярным выражениям внутри текстовых значений:
    - `(?i)\b(?:password|пароль|token|токен|secret|secret_key|api_key)\s*[:=]\s*([^\s,;]+)`
    - `Bearer\s+[A-Za-z0-9_\-\.]{15,}`
    - Basic auth / Base64 токены.
  - Замена значений на `***REDACTED***`.
  - Безопасное усечение слишком длинных строк и массивов.

### 2. Сервис `AutopilotReporter`
* **Расположение:** `core-api/app/services/autopilot_reporter.py`
* **Сигнатуры основных методов:**
  - `format_start_comment(..., depth: str = "applied") -> str`
    - Триггер `on_start`: принятие тикета автопилотом.
    - Входные данные: `run_id`, `task_id`, `scenario_key`, `scenario_version`, `target_host`, `confidence`, `facts`.
  - `format_pause_comment(..., depth: str = "applied") -> str`
    - Триггер `on_pause_or_error`: пауза, нехватка фактов (статус 35) или ошибка шага/воркера.
    - Входные данные: `run_id`, `task_id`, `scenario_key`, `reason`, `missing_facts`, `error_detail`, `facts`.
  - `format_complete_comment(..., depth: str = "applied") -> str`
    - Триггер `on_complete`: успешное подтверждение и закрытие тикета.
    - Входные данные: `run_id`, `task_id`, `scenario_key`, `outcome`, `proof`, `duration_seconds`.
* **Форматирование уровня «Прикладной» (`applied`):**
  - Лаконичный префикс: `[IntraLink Autopilot: Старт]`, `[IntraLink Autopilot: Пауза]`, `[IntraLink Autopilot: Успех]`.
  - Человекочитаемое описание на русском языке (1–2 понятных предложения).
  - Понятные названия сценариев (русскоязычный маппинг ключей сценариев: например, `install_printer` ➔ «Установка сетевого принтера»).
* **Форматирование уровня «Технический» (`technical`):**
  - Заголовок с контекстом: `[IntraLink Autopilot: Trace]`.
  - Строка метаданных: сценарий, статус цикла, timestamp UTC.
  - Блок ````json ... ```` с отформатированным отступом `indent=2`, содержащий полный очищенный контекст выполнения.
* **Защита от превышения лимитов IntraService API:**
  - Максимальная длина комментария: 3500 символов.
  - При превышении JSON-блок аккуратно усекается с маркером `"... [дамп контекста усечен]"`.

### 3. Комплексные Unit-тесты (`core-api/tests/test_autopilot_reporter.py`)
* Тест 1: DLP-санитизация — проверка маскирования ключей словаря (включая глубокую вложенность).
* Тест 2: DLP-санитизация — проверка маскирования текста с паролями и токенами внутри строк.
* Тест 3: Генерация `format_start_comment` в режимах `applied` и `technical`.
* Тест 4: Генерация `format_pause_comment` в режимах `applied` и `technical` (с `missing_facts` и без).
* Тест 5: Генерация `format_complete_comment` в режимах `applied` и `technical` (с `proof` и `outcome`).
* Тест 6: Проверка лимита длины комментария (3500 символов) при передаче гигантских фактов.
* Тест 7: Отказоустойчивость при пустых/None полях (None-safe).

---

## 🔍 Критерии приёмки Milestone 2
1. Модуль `core-api/app/services/autopilot_reporter.py` реализован без сторонних тяжелых зависимостей, с чистой архитектурой.
2. Все тесты в `core-api/tests/test_autopilot_reporter.py` запускаются через `$env:PYTHONPATH='.;core-api'; uv run python -m pytest core-api/tests/test_autopilot_reporter.py -v` и возвращают 100% `PASSED`.
3. Регрессионные тесты не сломаны.
4. Ни один секрет (`password`, `token`) не протекает наружу ни в одном из форматов.
