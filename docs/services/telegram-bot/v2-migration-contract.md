# 📋 Контракт перехода Telegram-бота на Decision Engine v2 API

Этот документ фиксирует архитектурный контракт и требования для предстоящей модернизации Telegram-бота (`telegram-bot`) при переходе на API Core Gateway v2.

---

## 1. Контекст модернизации

В рамках модернизации Decision Engine (Фазы 1–8) контур обработки решений переведен на архитектуру **Functional Core + Imperative Shell**:
1. Устаревший класс `RuleDecision` с хардкодом текстов и статусов заменен на строгие алгебраические исходы `DecisionOutcome` (`ClarificationRequired`, `ActionProposed`, `ResolutionProposed`, `ManualReviewRequired`, `NoMatch`).
2. Единственным источником правды (SSOT) для шаблонов ответов и политик резолюции стали таблицы PostgreSQL `response_templates` и `resolution_policies` с песочницей Jinja Sandbox.
3. Команды инфраструктурного воркера исполняются строго через версионированный Command Bus v2 (`/api/v2/commands/`).
4. Учетные данные (временные пароли) изолированы в краткоживущих артефактах `command_secret_artifacts` и никогда не передаются в открытом виде через API или логи.

---

## 2. Спецификация контракта взаимодействия (HTTP REST)

### 2.1 Получение решения по заявке
- **Эндпоинт:** `POST /api/v2/triage/tasks/{task_id}/decision`
- **Заголовки:** `X-Bot-Api-Key: <key>`
- **Формат ответа:**
```json
{
  "task_id": 140479,
  "decision_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "decision_version": 2,
  "outcome_kind": "clarification",
  "outcome_key": "account_details_invalid",
  "status_id": 35,
  "status_name": "Требует уточнения",
  "expenses": 5,
  "comment": "Добрый день! Для создания учетной записи сотрудника в Active Directory, пожалуйста, укажите ФИО полностью, должность и подразделение ответным комментарием к этой заявке.",
  "is_redirect": false,
  "requires_approval": false,
  "risk_level": "normal",
  "command": null
}
```

### 2.2 Карточки с действиями оператора (HitL Approval)
Если `outcome_kind == "action"` (например, `create_user`, `install_printer`):
```json
{
  "outcome_kind": "action",
  "outcome_key": "create_user_proposed",
  "requires_approval": true,
  "risk_level": 2,
  "command": {
    "command_id": "c71a3d90-8451-46bb-b5bf-79f9457a1b02",
    "action": "create_user",
    "status": "awaiting_approval",
    "parameters": {
      "surname": "Иванов",
      "name": "Иван",
      "department": "ИТ",
      "company": "Интра",
      "title": "Инженер"
    }
  }
}
```

- **Действие оператора в боте:**
  - Кнопка **«Одобрить»**: `POST /api/v2/commands/{command_id}/approval` с телом `{"decision": "approve"}`.
  - Кнопка **«Отклонить»**: `POST /api/v2/commands/{command_id}/approval` с телом `{"decision": "reject", "reason": "Отклонено инженером в Telegram"}`.

### 2.3 Доставка и фиксация в IntraService
После выполнения команды бот вызывает:
- `POST /api/v2/commands/{command_id}/deliver`
Бот **не генерирует комментарии самостоятельно** и **не подставляет пароли**. Core API самостоятельно достает одноразовый секрет из хранилища, рендерит финальный комментарий и публикует его в IntraService.

---

## 3. Требования безопасности при реализации в боте

1. **Запрет хранения паролей в FSM/памяти бота:**
   Временные пароли учетных записей Active Directory никогда не передаются в сообщения бота заявителю или инженеру.
2. **Сквозная индикация рисков:**
   Если `risk_level == "critical"` (например, обнаружен риск производственного простоя `downtime_priority`), сообщение инженеру должно содержать предупреждающий эмодзи 🚨 и блокировку кнопки «Отменить».
3. **Идемпотентность кнопок:**
   Все inline-кнопки одобрения должны иметь защиту от повторного нажатия (de-bounce на уровне callback query ID).
