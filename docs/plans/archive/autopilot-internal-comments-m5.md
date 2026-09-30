# 🎯 Микроплан Milestone 5: Комплексное E2E тестирование, канареечная верификация и приёмка

**Связанные документы:**
- Генеральный план: [`docs/plans/autopilot-internal-comments-plan.md`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/docs/plans/autopilot-internal-comments-plan.md)
- RFC: [`autopilot-internal-comments-rfc.md`](file:///C:/Users/belikov.a/.gemini/antigravity-cli/brain/85234743-c523-4633-8926-fbce3d460cad/autopilot-internal-comments-rfc.md)
- Микроплан M1: [`docs/plans/autopilot-internal-comments-m1.md`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/docs/plans/autopilot-internal-comments-m1.md)
- Микроплан M2: [`docs/plans/autopilot-internal-comments-m2.md`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/docs/plans/autopilot-internal-comments-m2.md)
- Микроплан M3: [`docs/plans/autopilot-internal-comments-m3.md`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/docs/plans/autopilot-internal-comments-m3.md)
- Микроплан M4: [`docs/plans/autopilot-internal-comments-m4.md`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/docs/plans/autopilot-internal-comments-m4.md)

---

## 1. Цели этапа

Цель финального майлстоуна — провести сквозную комплексную приёмку (End-to-End) механизма скрытых служебных комментариев (`IsPrivateComment = true`) на реальной тестовой СУБД PostgreSQL 16 (`pgvector`), подтвердить корректность взаимодействия всех слоёв системы (DB ➔ Reporter ➔ DLP Sanitizer ➔ Orchestrator ➔ API ➔ Web UI), актуализировать операционную документацию и закрыть проектный чеклист DoD.

---

## 2. Задачи Milestone 5

### 🧪 2.1. Комплексный E2E тестовый сьют (`core-api/tests/test_autopilot_internal_comments_e2e.py`)
Разработать и выполнить на тестовой базе данных PostgreSQL (порт 5434, `intraservice_test`) комплексные сквозные тесты:

1. **`test_e2e_full_lifecycle_success_with_internal_comments`**:
   - Сквозной цикл заявки (создание `TicketRun` ➔ `advance` ➔ отправка стартового комментария ➔ выполнение команды ➔ `on_complete` ➔ закрытие).
   - Проверка: оба комментария отправлены с `is_private=True`, зафиксированы события `TicketRunEvent(name="internal_comment_sent")`, тело комментария соответствует уровню `applied`.
2. **`test_e2e_lifecycle_pause_clarification`**:
   - Жизненный цикл с остановкой: нехватка фактов или ошибка исполнения ➔ переход в `paused` / статус 35.
   - Проверка: отправлен комментарий `on_pause_or_error` с описанием причины инженеру, зафиксировано событие, заявка переведена в ожидание ответа.
3. **`test_e2e_technical_depth_and_dlp_masking`**:
   - Сквозной цикл со сценарием `depth="technical"` и наличием конфиденциальных полей (`temp_pass`, `secret_token`, `Bearer ...`).
   - Проверка: отправленный в IntraService комментарий оформлен в виде валидного JSON Trace, все секреты замещены на `***REDACTED***`, длина не превышает 3500 символов.
4. **`test_e2e_shadow_mode_no_comments`**:
   - Сценарий с `rollout_mode="shadow"`.
   - Проверка: комментарии в IntraService **не отправляются** (`add_task_comment` не вызывается), события `internal_comment_sent` не генерируются, в журнал пишется служебная отметка.
5. **`test_e2e_canary_mode_behavior`**:
   - Сценарий с `rollout_mode="canary"` и заданным `canary_percent`.
   - Проверка: детерминированная бакетизация (тикеты в бакете получают скрытые комментарии, тикеты вне бакета не отправляют комментарии во внешний контур).
6. **`test_e2e_network_failure_resilience`**:
   - Сбой сети при отправке скрытого комментария (`add_task_comment` выбрасывает `RuntimeError` / `HTTPError`).
   - Проверка: оркестратор не прерывает работу, исключение логируется как предупреждение, основной цикл `advance` успешно завершает свои бизнес-действия (Fault Tolerance).

---

### 📚 2.2. Актуализация документации оператора Helpdesk
1. Обновить [`docs/web-autopilot/operations.md`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/docs/web-autopilot/operations.md):
   - Добавить раздел «Служебные скрытые комментарии (`IsPrivateComment`)».
   - Описать триггеры (`on_start`, `on_pause_or_error`, `on_complete`).
   - Описать уровни детализации (`applied` и `technical`) и правила каскадного наследования настроек (Глобальные ➔ Сценарий).
   - Описать регламент безопасности DLP (маскировка паролей и токенов).
   - Описать поведение в режимах `shadow` и `canary`.

---

### 🏁 2.3. Закрытие генерального плана и чеклиста Definition of Done
1. Обновить [`docs/plans/autopilot-internal-comments-plan.md`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/docs/plans/autopilot-internal-comments-plan.md):
   - Проставить отметки `[x]` для всех пунктов Definition of Done.
   - Зафиксировать статус завершения всех 5 майлстоунов.

---

## 3. Критерии готовности (Acceptance Criteria)

- [ ] Все E2E тесты в `test_autopilot_internal_comments_e2e.py` успешно проходят на тестовой PostgreSQL 16 (порт 5434).
- [ ] Полный регрессионный сьют тестов проекта `core-api` выполняется без ошибок.
- [ ] Документация в `docs/web-autopilot/operations.md` полностью актуализирована.
- [ ] Генеральный план `docs/plans/autopilot-internal-comments-plan.md` обновлен до статуса «Завершено».
- [ ] Изменения зафиксированы атомарным коммитом в Git.
